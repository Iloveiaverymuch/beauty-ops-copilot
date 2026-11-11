"""Shared retrieval types. Every mode (dense, bm25, hybrid, rerank) returns list[Result] through `search()`,
so the eval harness, the tools and the agent never care which mode is behind them (one-line swap)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Result:
    chunk_id: str
    doc_id: str
    section: str
    title: str
    text: str
    score: float                      # meaning depends on the mode: cosine sim, BM25, RRF, or reranker relevance
    meta: dict = field(default_factory=dict)

    @property
    def citation(self) -> str:
        """What the answer cites and what the eval checks: '<doc_id>#<section>'."""
        return f"{self.doc_id}#{self.section}"


class Retriever(Protocol):
    name: str

    def search(self, query: str, k: int = 5) -> list[Result]:
        """Top-k results, best first."""
        ...
