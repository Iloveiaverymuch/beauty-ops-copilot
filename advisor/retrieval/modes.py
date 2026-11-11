"""The 4 retrieval modes behind one interface, from cheapest to most precise.

    dense_bq: question embedding -> BigQuery VECTOR_SEARCH (cosine)          ~1 s/query (job overhead), reference
    dense   : question embedding -> cosine over vectors cached in memory      same results, ~ms (serving default)
    bm25    : question tokens   -> BM25 over the same chunks, in process      exact terms: "0,3 %", "PAO", "24 heures"
    hybrid  : both lists (50 candidates each) fused by Reciprocal Rank Fusion  robust default
    rerank  : hybrid top 20 -> Vertex AI ranking API (cross-encoder) -> top k  best precision, extra latency

Each Result carries `meta["ms"]` timings per stage so the eval can report latency per mode, not just quality.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict

import google.auth
from google.auth.transport.requests import AuthorizedSession

from advisor import config
from advisor.embeddings import embed_query
from advisor.retrieval import store
from advisor.retrieval.base import Result
from advisor.retrieval.bm25 import BM25Index

RRF_K = 60          # standard constant; damps the weight of any single #1, rewards agreement between lists
CANDIDATES = 50     # per retriever, before fusion (fusion needs material; cheap at this size)
RERANK_POOL = 20    # cross-encoder cost grows with candidates: score only the fused top 20


def _to_results(scored: list[tuple[str, float]], k: int, ms: dict) -> list[Result]:
    by_id = {c["chunk_id"]: c for c in store.public_chunks()}
    out = []
    for cid, s in scored[:k]:
        c = by_id[cid]
        out.append(Result(cid, c["doc_id"], c["section"], c["title"], c["text"], round(s, 4), {"ms": dict(ms)}))
    return out


def rrf(ranked_lists: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of 1 / (k + rank). Uses RANKS only, so BM25 scores
    (unbounded) and cosine similarities (0..1) never need to be put on a common scale."""
    scores: dict[str, float] = defaultdict(float)
    for lst in ranked_lists:
        for rank, doc_id in enumerate(lst, start=1):
            scores[doc_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


class DenseRetriever:
    """backend="memory" (default for serving): cosine over vectors cached from BigQuery, ~ms.
    backend="bq": BigQuery VECTOR_SEARCH per question, ~1 s (kept as a mode to show the trade-off)."""
    name = "dense"

    def __init__(self, backend: str | None = None):
        self.backend = backend or os.getenv("DENSE_BACKEND", "memory")
        self.name = "dense" if self.backend == "memory" else "dense_bq"

    def scored(self, query: str, k: int) -> tuple[list[tuple[str, float]], dict]:
        t0 = time.perf_counter()
        qvec = embed_query(query)
        t1 = time.perf_counter()
        search = store.memory_search_chunks if self.backend == "memory" else store.vector_search_chunks
        hits = search(qvec, k)
        return hits, {"embed": round((t1 - t0) * 1000), f"vector_{self.backend}": round((time.perf_counter() - t1) * 1000, 1)}

    def search(self, query: str, k: int = 5) -> list[Result]:
        hits, ms = self.scored(query, max(k, 1))
        return _to_results(hits, k, ms)


class BM25Retriever:
    name = "bm25"
    _index: BM25Index | None = None
    _lock = threading.Lock()

    @classmethod
    def index(cls) -> BM25Index:
        if cls._index is None:
            with cls._lock:
                if cls._index is None:
                    # embed_text = "Titre > Section\ntext": the title words count for lexical match too
                    cls._index = BM25Index([(c["chunk_id"], c["embed_text"]) for c in store.public_chunks()])
        return cls._index

    def scored(self, query: str, k: int) -> tuple[list[tuple[str, float]], dict]:
        t0 = time.perf_counter()
        hits = self.index().search(query, k)
        return hits, {"bm25": round((time.perf_counter() - t0) * 1000, 2)}

    def search(self, query: str, k: int = 5) -> list[Result]:
        hits, ms = self.scored(query, k)
        return _to_results(hits, k, ms)


class HybridRetriever:
    name = "hybrid"

    def __init__(self):
        self.dense, self.bm25 = DenseRetriever(), BM25Retriever()

    def scored(self, query: str, k: int) -> tuple[list[tuple[str, float]], dict]:
        d, ms_d = self.dense.scored(query, CANDIDATES)
        b, ms_b = self.bm25.scored(query, CANDIDATES)
        return rrf([[i for i, _ in d], [i for i, _ in b]])[:k], {**ms_d, **ms_b}

    def search(self, query: str, k: int = 5) -> list[Result]:
        hits, ms = self.scored(query, max(k, RERANK_POOL))
        return _to_results(hits, k, ms)


class RerankRetriever:
    """Two stages: cheap recall (hybrid top 20) then expensive precision (cross-encoder reads question + chunk
    TOGETHER, which bi-encoders can't). Score = relevance in [0, 1]; used for the no-answer threshold."""
    name = "rerank"
    _session: AuthorizedSession | None = None
    _lock = threading.Lock()

    def __init__(self):
        self.hybrid = HybridRetriever()

    @classmethod
    def session(cls) -> AuthorizedSession:
        if cls._session is None:
            with cls._lock:
                if cls._session is None:
                    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
                    cls._session = AuthorizedSession(creds)   # refreshes the OAuth token automatically
        return cls._session

    def search(self, query: str, k: int = 5) -> list[Result]:
        candidates = self.hybrid.search(query, RERANK_POOL)
        ms = dict(candidates[0].meta["ms"]) if candidates else {}
        by_id = {c["chunk_id"]: c for c in store.public_chunks()}
        t0 = time.perf_counter()
        ranked = rerank_records(query, [(r.chunk_id, f"{r.title} > {r.section}", r.text) for r in candidates], k)
        ms["rerank"] = round((time.perf_counter() - t0) * 1000)
        return [Result(cid, by_id[cid]["doc_id"], by_id[cid]["section"], by_id[cid]["title"], by_id[cid]["text"],
                       round(score, 4), {"ms": ms}) for cid, score in ranked]


def rerank_records(query: str, records: list[tuple[str, str, str]], top_n: int) -> list[tuple[str, float]]:
    """Vertex AI ranking API (cross-encoder). records = [(id, title, text)] -> [(id, relevance 0..1)] best first."""
    if not records:
        return []
    url = (f"https://discoveryengine.googleapis.com/v1/projects/{config.PROJECT}/locations/{config.RANK_LOCATION}"
           f"/rankingConfigs/default_ranking_config:rank")
    body = {"model": config.RANK_MODEL, "query": query, "topN": top_n,
            "records": [{"id": i, "title": title, "content": text} for i, title, text in records]}
    resp = RerankRetriever.session().post(url, json=body, headers={"x-goog-user-project": config.PROJECT}, timeout=10)
    resp.raise_for_status()
    return [(r["id"], r["score"]) for r in resp.json().get("records", [])]


MODES = {"dense_bq": lambda: DenseRetriever("bq"), "dense": DenseRetriever, "bm25": BM25Retriever,
         "hybrid": HybridRetriever, "rerank": RerankRetriever}


def get_retriever(name: str):
    return MODES[name]()
