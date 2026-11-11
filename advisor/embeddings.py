"""Dense embeddings with Vertex AI, plus a local cache.

Libraries
---------
- `google-genai` (google.genai): Google's unified Python SDK for Gemini models. With `vertexai=True` it calls
  Vertex AI in OUR project and region (IAM, quotas, data residency, billing) instead of the consumer Gemini
  API. Same SDK the agents already use for generation.
- Model `gemini-embedding-001`: turns a text into a vector so that texts with similar MEANING end up close
  (cosine similarity), even with no word in common ("tiraillements" ~ "peau qui tire"). Multilingual, which
  matters because the corpus and the questions are French.

Choices (see docs/RAG_DECISIONS.md)
-----------------------------------
- task_type: RETRIEVAL_DOCUMENT when indexing, RETRIEVAL_QUERY when searching. The model is trained
  asymmetrically: a short question and the paragraph that answers it are mapped close to each other.
  Using the same task type on both sides measurably lowers recall.
- output_dimensionality=768 instead of the native 3,072: the model is trained with Matryoshka
  Representation Learning (the first N dimensions are a usable embedding on their own). 4x less storage and
  faster search for a small quality loss; re-check with the eval if the corpus grows.
- L2 normalization: Google requires it for truncated vectors when using dot product. Cosine (what we use in
  BigQuery) is insensitive to length, so normalizing is free insurance for either metric.
- Cache: key = sha256(model | dim | task | text). Re-running ingest on unchanged content makes 0 API calls
  (cost, speed); editing one paragraph re-embeds only that chunk. Changing the model or the dimension
  changes every key, so stale vectors can never be mixed with new ones.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from google import genai
from google.genai import types

from advisor import config

CACHE = Path(__file__).resolve().parent / ".cache" / "embeddings.jsonl"   # gitignored
_lock = threading.Lock()
_client: genai.Client | None = None


def client() -> genai.Client:
    """One shared client (it holds an HTTP connection pool). Region comes from EMBED_LOCATION (probed).

    Two traps, both hit for real:
    - google-genai's Client CLOSES its HTTP connection in __del__ (when garbage-collected). A client that
      nobody references any more dies mid-request -> "Cannot send a request, as the client has been closed".
      So the module keeps a strong reference for the life of the process.
    - Lazy init from 8 threads = check-then-act race: several threads saw `_client is None`, each created a
      client and overwrote the global, and the overwritten ones were collected mid-call. Hence the lock
      (double-checked so the fast path stays lock-free).
    """
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = genai.Client(vertexai=True, project=config.PROJECT, location=config.EMBED_LOCATION)
    return _client


def _key(text: str, task: str) -> str:
    return hashlib.sha256(f"{config.EMBED_MODEL}|{config.EMBED_DIM}|{task}|{text}".encode()).hexdigest()


def _load_cache() -> dict[str, list[float]]:
    if not CACHE.exists():
        return {}
    return {r["k"]: r["v"] for r in map(json.loads, CACHE.read_text().splitlines()) if r}


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _embed_one(text: str, task: str) -> list[float]:
    c = client()                      # local strong reference for the duration of the call
    resp = c.models.embed_content(
        model=config.EMBED_MODEL, contents=text,
        config=types.EmbedContentConfig(task_type=task, output_dimensionality=config.EMBED_DIM))
    return _normalize(list(resp.embeddings[0].values))


def embed(texts: list[str], task: str = "RETRIEVAL_DOCUMENT", workers: int = 8) -> tuple[list[list[float]], int]:
    """Embed many texts for INDEXING. Returns (vectors in input order, number of API calls actually made).

    One text per request: gemini-embedding-001 on Vertex accepts a single input per call, so throughput comes
    from a small thread pool (I/O-bound work; 8 parallel calls stays far under the default quota).
    """
    cache = _load_cache()
    keys = [_key(t, task) for t in texts]
    missing = [(k, t) for k, t in dict(zip(keys, texts)).items() if k not in cache]   # dict() dedupes identical texts
    if missing:
        CACHE.parent.mkdir(exist_ok=True)
        with ThreadPoolExecutor(workers) as pool:
            vecs = list(pool.map(lambda kt: _embed_one(kt[1], task), missing))
        with _lock, CACHE.open("a") as fh:
            for (k, _), v in zip(missing, vecs):
                cache[k] = v
                fh.write(json.dumps({"k": k, "v": v}) + "\n")
    return [cache[k] for k in keys], len(missing)


def embed_query(text: str) -> list[float]:
    """Embed ONE user question for SEARCHING (task RETRIEVAL_QUERY). Not cached: questions rarely repeat, and
    its latency is part of what we measure per request."""
    return _embed_one(text, "RETRIEVAL_QUERY")
