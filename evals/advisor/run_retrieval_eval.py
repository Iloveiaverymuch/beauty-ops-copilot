"""R3 retrieval eval: every golden question through every retrieval mode.

    uv run --env-file copilot/.env python -m evals.advisor.run_retrieval_eval [--modes dense bm25 hybrid rerank]

Reports, per mode: section-level recall@5 and MRR (strict), doc-level recall@5 (lenient), per-category recall,
latency p50/p95, poisoned-review exposure, internal-doc leaks, and for rerank a no-answer threshold sweep.
Writes the full per-question detail to evals/advisor/results/retrieval_<timestamp>.json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from advisor.retrieval import store
from advisor.retrieval.modes import MODES
from evals.advisor.golden import GOLDEN, POISONED
from evals.advisor.metrics import recall_at_k, reciprocal_rank

K = 5
OUT = Path(__file__).resolve().parent / "results"


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", nargs="+", default=["dense", "bm25", "hybrid", "rerank"])
    args = ap.parse_args()
    store.public_chunks(), store.public_chunk_vectors()   # warm caches: no mode pays the one-time load

    detail, summary = [], {}
    for mode in args.modes:
        r = MODES[mode]()
        rows = []
        for g in GOLDEN:
            t0 = time.perf_counter()
            res = r.search(g["q"], k=K)
            ms = (time.perf_counter() - t0) * 1000
            cites = [x.citation for x in res]
            row = {"mode": mode, "id": g["id"], "cat": g["cat"], "q": g["q"], "expect": g["expect"],
                   "retrieved": cites, "scores": [x.score for x in res], "ms": round(ms),
                   "poisoned_in_top5": POISONED in cites, "internal_leak": any(c.startswith("int-") for c in cites)}
            if g["expect"]:
                ra = g.get("require_all", False)
                row |= {"r5_section": recall_at_k(cites, g["expect"], K, ra), "r5_doc": recall_at_k(cites, g["expect"], K, ra, "doc"),
                        "rr_section": reciprocal_rank(cites, g["expect"]), "top1_ok": bool(cites) and cites[0] in g["expect"]}
            rows.append(row)
        detail += rows
        ans = [x for x in rows if x["expect"]]
        by_cat = defaultdict(list)
        for x in ans:
            by_cat[x["cat"]].append(x["r5_section"])
        lat = [x["ms"] for x in rows]
        summary[mode] = {
            "recall5_section": round(statistics.mean(x["r5_section"] for x in ans), 3),
            "recall5_doc": round(statistics.mean(x["r5_doc"] for x in ans), 3),
            "mrr_section": round(statistics.mean(x["rr_section"] for x in ans), 3),
            "top1_section": round(statistics.mean(x["top1_ok"] for x in ans), 3),
            "by_category": {c: f"{sum(v):.0f}/{len(v)}" for c, v in sorted(by_cat.items())},
            "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95),
            "poisoned_in_top5": sum(x["poisoned_in_top5"] for x in rows), "internal_leaks": sum(x["internal_leak"] for x in rows),
            "n_answerable": len(ans), "n_total": len(rows),
        }

    if "rerank" in summary:   # no-answer threshold sweep on the reranker's top-1 score
        rr = [x for x in detail if x["mode"] == "rerank"]
        sweep = []
        for t in [round(0.05 * i, 2) for i in range(2, 13)]:
            correct_refusal = sum(1 for x in rr if not x["expect"] and (not x["scores"] or x["scores"][0] < t))
            false_refusal = sum(1 for x in rr if x["expect"] and x["scores"] and x["scores"][0] < t)
            n_no = sum(1 for x in rr if not x["expect"])
            sweep.append({"t": t, "correct_refusals": f"{correct_refusal}/{n_no}", "false_refusals": f"{false_refusal}/{len(rr) - n_no}"})
        summary["rerank"]["threshold_sweep"] = sweep
        summary["rerank"]["top1_scores"] = {x["id"]: (x["scores"][0] if x["scores"] else None) for x in rr}

    print(f"\n{'mode':8s} {'R@5 sect':>8s} {'R@5 doc':>8s} {'MRR':>6s} {'top1':>6s} {'p50 ms':>7s} {'p95 ms':>7s} {'poison':>7s} {'leaks':>6s}")
    for m, s in summary.items():
        print(f"{m:8s} {s['recall5_section']:8.3f} {s['recall5_doc']:8.3f} {s['mrr_section']:6.3f} {s['top1_section']:6.3f} "
              f"{s['p50_ms']:7.0f} {s['p95_ms']:7.0f} {s['poisoned_in_top5']:5d}/{s['n_total']} {s['internal_leaks']:5d}")
    print("\nsection recall@5 by category:")
    for m, s in summary.items():
        print(f"  {m:8s} " + "  ".join(f"{c}={v}" for c, v in s["by_category"].items()))
    if "rerank" in summary:
        print("\nrerank no-answer threshold sweep (top-1 score < t => 'je ne sais pas'):")
        for row in summary["rerank"]["threshold_sweep"]:
            print(f"  t={row['t']:.2f}  correct refusals {row['correct_refusals']:>5s}   false refusals {row['false_refusals']:>6s}")
        print("  top-1 scores of no-answer / security questions: " + ", ".join(
            f"{k}={v:.2f}" for k, v in summary["rerank"]["top1_scores"].items() if k[0] in "xs" and v is not None))
    misses = [x for x in detail if x["expect"] and x["mode"] == args.modes[-1] and not x["r5_section"]]
    if misses:
        print(f"\nmisses ({args.modes[-1]}):")
        for x in misses:
            print(f"  {x['id']} {x['q'][:60]}\n     expect {x['expect']}\n     got    {x['retrieved'][:3]}")
    OUT.mkdir(exist_ok=True)
    path = OUT / f"retrieval_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps({"summary": summary, "detail": detail}, ensure_ascii=False, indent=1))
    print(f"\nsaved {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")


if __name__ == "__main__":
    main()
