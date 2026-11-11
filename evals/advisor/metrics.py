"""Retrieval metrics on citations '<doc_id>#<section>'. Section level = strict (the right passage); doc level =
lenient (the right document). Both reported: the gap between them is what reranking buys."""
from __future__ import annotations


def _doc(c: str) -> str:
    return c.split("#", 1)[0]


def recall_at_k(retrieved: list[str], expect: list[str], k: int = 5, require_all: bool = False,
                level: str = "section") -> float:
    """Hit metric per question: 1.0 if ANY (or ALL with require_all) expected item is in the top k."""
    top = retrieved[:k]
    if level == "doc":
        top, expect = [_doc(c) for c in top], [_doc(c) for c in expect]
    found = [e in top for e in expect]
    return float(all(found) if require_all else any(found))


def reciprocal_rank(retrieved: list[str], expect: list[str], level: str = "section") -> float:
    """1 / rank of the FIRST expected item (0 if absent). Rewards putting the answer at #1, which matters
    because the model reads the first passages most reliably and we only send the top 4."""
    for rank, c in enumerate(retrieved, start=1):
        if (c if level == "section" else _doc(c)) in ([e if level == "section" else _doc(e) for e in expect]):
            return 1.0 / rank
    return 0.0
