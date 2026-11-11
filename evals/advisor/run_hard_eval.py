"""Hard-set retrieval eval: products (exact identifiers, typos, English, colloquial) + knowledge base (short,
jargon, numbers, typos, English), and the base golden set again, with confidence intervals and paired tests.

    uv run --env-file copilot/.env python -m evals.advisor.run_hard_eval
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from advisor.embeddings import embed_query
from advisor.retrieval import store
from advisor.retrieval.bm25 import BM25Index
from advisor.retrieval.modes import MODES, rrf, rerank_records
from evals.advisor.golden import GOLDEN
from evals.advisor.golden_hard import KB_HARD, PRODUCT_HARD
from evals.advisor.metrics import recall_at_k, reciprocal_rank
from evals.advisor.stats import mcnemar, wilson

K, POOL = 5, 20
MODE_NAMES = ["dense", "bm25", "hybrid", "rerank"]
OUT = Path(__file__).resolve().parent / "results"


class ProductRetrievers:
    """Same 4 modes as the knowledge base, over the 40 product texts (no attribute filters: pure retrieval)."""

    def __init__(self):
        self.products = store.public_products()
        self.vecs = store.public_product_vectors()
        self.bm25 = BM25Index([(s, p["search_text"]) for s, p in self.products.items()])

    def run(self, mode: str, q: str) -> list[str]:
        dense = lambda: [s for s, _ in sorted(((s, sum(a * b for a, b in zip(qv, v))) for s, v in self.vecs), key=lambda x: -x[1])]  # noqa: E731
        if mode in ("dense", "hybrid", "rerank"):
            qv = embed_query(q)
        if mode == "dense":
            return dense()[:K]
        lex = [s for s, _ in self.bm25.search(q, 50)]
        if mode == "bm25":
            return lex[:K]
        fused = [s for s, _ in rrf([dense()[:50], lex])]
        if mode == "hybrid":
            return fused[:K]
        recs = [(s, self.products[s]["name"], self.products[s]["search_text"]) for s in fused[:POOL]]
        return [s for s, _ in rerank_records(q, recs, K)]


def evaluate(items, run, require_all_key=True):
    rows = []
    for g in items:
        t0 = time.perf_counter()
        got = run(g["q"])
        rows.append({"id": g["id"], "type": g.get("type") or g.get("cat"), "got": got[:K], "expect": g["expect"],
                     "r5": recall_at_k(got, g["expect"], K, g.get("require_all", False)),
                     "rr": reciprocal_rank(got, g["expect"]), "top1": bool(got) and got[0] in g["expect"],
                     "ms": round((time.perf_counter() - t0) * 1000)})
    return rows


def report(title: str, results: dict[str, list[dict]]) -> dict:
    n = len(next(iter(results.values())))
    print(f"\n=== {title} (n={n}) ===")
    print(f"{'mode':8s} {'R@5':>5s} {'MRR':>6s} {'top1':>5s}  {'top1 95% CI':>13s}")
    out = {}
    for m, rows in results.items():
        k1 = sum(r["top1"] for r in rows)
        lo, hi = wilson(k1, n)
        r5, mrr = sum(r["r5"] for r in rows) / n, sum(r["rr"] for r in rows) / n
        out[m] = {"r5": round(r5, 3), "mrr": round(mrr, 3), "top1": f"{k1}/{n}", "ci": [round(lo, 2), round(hi, 2)]}
        print(f"{m:8s} {r5:5.2f} {mrr:6.3f} {k1:2d}/{n:<2d}  [{lo:.2f}, {hi:.2f}]")
    types = sorted({r["type"] for r in next(iter(results.values()))})
    print("top-1 by query type:  " + "  ".join(f"{t:>10s}" for t in types))
    for m, rows in results.items():
        by = defaultdict(list)
        for r in rows:
            by[r["type"]].append(r["top1"])
        print(f"  {m:8s}            " + "  ".join(f"{sum(by[t]):>4d}/{len(by[t]):<5d}" for t in types))
    print("paired top-1 (McNemar exact): A-only / B-only wins, p")
    for a, b in combinations(results, 2):
        x, y, p = mcnemar([r["top1"] for r in results[a]], [r["top1"] for r in results[b]])
        flag = "  <- significant" if p < 0.05 else ""
        print(f"  {a:7s} vs {b:7s}: {x:2d} / {y:<2d}  p={p:.3f}{flag}")
    return out


def main():
    store.public_chunks(), store.public_chunk_vectors()
    prod = ProductRetrievers()
    kb = {m: MODES[m]() for m in MODE_NAMES}
    kb_run = lambda m: (lambda q: [r.citation for r in kb[m].search(q, k=K)])  # noqa: E731
    base_items = [g for g in GOLDEN if g["expect"]]
    res = {
        "products_hard": {m: evaluate(PRODUCT_HARD, lambda q, m=m: prod.run(m, q)) for m in MODE_NAMES},
        "kb_hard": {m: evaluate(KB_HARD, kb_run(m)) for m in MODE_NAMES},
        "kb_base": {m: evaluate(base_items, kb_run(m)) for m in MODE_NAMES},
    }
    summary = {name: report(name, r) for name, r in res.items()}
    combined = {m: res["products_hard"][m] + res["kb_hard"][m] + res["kb_base"][m] for m in MODE_NAMES}
    summary["all_combined"] = report("ALL (products hard + kb hard + kb base)", combined)
    print("\nmisses at top-1 (rerank):")
    for name in ("products_hard", "kb_hard"):
        for r in res[name]["rerank"]:
            if not r["top1"]:
                print(f"  {r['id']:5s} [{r['type']}] got {r['got'][:2]} expected {r['expect'][:2]}")
    OUT.mkdir(exist_ok=True)
    path = OUT / f"hard_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps({"summary": summary, "detail": res}, ensure_ascii=False, indent=1))
    print(f"\nsaved {path.name}")


if __name__ == "__main__":
    main()
