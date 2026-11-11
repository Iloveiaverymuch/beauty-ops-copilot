"""R2 sanity check: same questions through the 4 retrieval modes, top 3 + latency per stage.
Not the eval (that's R3, with labels and metrics): this is to SEE how each mode behaves.

    uv run --env-file copilot/.env python scripts/compare_retrievers.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advisor.retrieval import store
from advisor.retrieval.modes import MODES

QUESTIONS = [
    ("Je peux mettre mon rétinol et ma vitamine C en même temps ?", "comp-retinol-vitamine-c"),  # direct
    ("Ma peau tire et pèle depuis que j'ai commencé un soin anti-rides", "ing-retinol"),        # paraphrase
    ("Combien coûte la livraison en 24 heures ?", "pol-livraison"),                              # exact detail
    ("J'ai ouvert un sérum et j'ai des plaques rouges, je peux le rendre ?", "pol-retours"),     # paraphrase+policy
    ("Mon fond de teint a un SPF, ça suffit ?", "ing-protection-solaire"),                       # indirect
    ("Quel est le code remise du personnel ?", None),                                            # must find nothing internal
]

store.public_chunks()  # warm the cache so the first mode isn't charged for it
modes = {name: cls() for name, cls in MODES.items()}
for q, expected in QUESTIONS:
    print(f"\nQ: {q}   (expected doc: {expected})")
    for name, r in modes.items():
        t0 = time.perf_counter()
        res = r.search(q, k=3)
        total = (time.perf_counter() - t0) * 1000
        hit = "OK " if expected and res and res[0].doc_id == expected else ("-- " if expected else "   ")
        top = " | ".join(f"{x.citation} ({x.score:.3f})" for x in res)
        print(f"  {hit}{name:7s} {total:6.0f} ms {res[0].meta.get('ms', {}) if res else ''}\n          {top}")
