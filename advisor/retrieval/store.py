"""Read side of BigQuery for the advisor. ONLY the *_public tables are ever referenced here.

- `public_chunks()` / `public_products()`: the customer-visible rows, loaded ONCE per process (no embeddings)
  and kept in memory. At this size (62 chunks, 40 products) that's a few KB; it feeds BM25 and lets us turn
  ids back into text without another query. At catalog scale this becomes a cache with a TTL or an index
  service, not a full load.
- `vector_search_*()`: BigQuery VECTOR_SEARCH with the query vector passed as a QUERY PARAMETER (never string
  formatting: no SQL injection, and BigQuery can cache the plan).

Library: google-cloud-bigquery. `client.query(sql, job_config)` starts a query job; `.result()` waits and
iterates rows. Each query has a fixed overhead (job scheduling) of a few hundred ms: that's the latency cost
of using a warehouse as a vector store, measured in scripts/compare_retrievers.py.
"""
from __future__ import annotations

import threading

from google.cloud import bigquery

from advisor import config

_lock = threading.Lock()
_bq: bigquery.Client | None = None
_chunks: list[dict] | None = None
_products: dict[str, dict] | None = None

T_CHUNKS = f"`{config.PROJECT}.{config.BQ_DATASET}.chunks_public`"
T_PRODUCTS = f"`{config.PROJECT}.{config.BQ_DATASET}.products_public`"
PRODUCT_FIELDS = ("sku, name, brand, category, concerns, skin_types, actives, fragrance_free, price_eur, "
                  "promo_price_eur, size_ml, usage_ml_per_day, routine_moment, description, image, search_text")


def bq() -> bigquery.Client:
    global _bq
    if _bq is None:
        with _lock:                       # same double-checked init as the embeddings client (thread pools)
            if _bq is None:
                _bq = bigquery.Client(project=config.PROJECT, location=config.BQ_LOCATION)
    return _bq


def public_chunks() -> list[dict]:
    global _chunks
    if _chunks is None:
        with _lock:
            if _chunks is None:
                rows = bq().query(f"SELECT chunk_id, doc_id, title, doc_type, section, position, text, embed_text "
                                  f"FROM {T_CHUNKS} ORDER BY doc_id, position").result()
                _chunks = [dict(r) for r in rows]
    return _chunks


def public_products() -> dict[str, dict]:
    global _products
    if _products is None:
        with _lock:
            if _products is None:
                rows = bq().query(f"SELECT {PRODUCT_FIELDS} FROM {T_PRODUCTS}").result()
                _products = {r["sku"]: dict(r) for r in rows}
    return _products


def vector_search_chunks(qvec: list[float], k: int = 50) -> list[tuple[str, float]]:
    """[(chunk_id, cosine_similarity)] best first. BigQuery returns cosine DISTANCE (0 = same direction), we
    convert to similarity (1 - distance) so 'higher is better' holds for every mode."""
    sql = (f"SELECT base.chunk_id, distance FROM VECTOR_SEARCH(TABLE {T_CHUNKS}, 'embedding', "
           f"(SELECT @qvec AS embedding), top_k => {int(k)}, distance_type => 'COSINE') ORDER BY distance")
    cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ArrayQueryParameter("qvec", "FLOAT64", qvec)])
    return [(r["chunk_id"], 1 - r["distance"]) for r in bq().query(sql, job_config=cfg).result()]


def filtered_product_search(qvec: list[float] | None, skin_type: str | None, concern: str | None,
                            category: str | None, max_price: float | None, fragrance_free: bool | None,
                            k: int = 50) -> list[tuple[str, float]]:
    """SQL attribute filters FIRST (exact: 'under 15 EUR' means under 15 EUR), then cosine ranking INSIDE the
    filtered set (pre-filtering in VECTOR_SEARCH's base subquery; exact search, so no recall loss).
    Effective price = promo price if any, else list price. No query text -> filtered set ordered by price."""
    where = ("(@skin IS NULL OR @skin IN UNNEST(skin_types)) AND (@concern IS NULL OR @concern IN UNNEST(concerns)) "
             "AND (@category IS NULL OR category = @category) "
             "AND (@max_price IS NULL OR COALESCE(promo_price_eur, price_eur) <= @max_price) "
             "AND (@ff IS NULL OR fragrance_free = @ff)")
    params = [bigquery.ScalarQueryParameter("skin", "STRING", skin_type),
              bigquery.ScalarQueryParameter("concern", "STRING", concern),
              bigquery.ScalarQueryParameter("category", "STRING", category),
              bigquery.ScalarQueryParameter("max_price", "FLOAT64", max_price),
              bigquery.ScalarQueryParameter("ff", "BOOL", fragrance_free)]
    if qvec is None:
        sql = (f"SELECT sku, COALESCE(promo_price_eur, price_eur) AS p FROM {T_PRODUCTS} WHERE {where} "
               f"ORDER BY p LIMIT {int(k)}")
        rows = bq().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
        return [(r["sku"], 0.0) for r in rows]
    params.append(bigquery.ArrayQueryParameter("qvec", "FLOAT64", qvec))
    sql = (f"SELECT base.sku, distance FROM VECTOR_SEARCH((SELECT sku, embedding FROM {T_PRODUCTS} WHERE {where}), "
           f"'embedding', (SELECT @qvec AS embedding), top_k => {int(k)}, distance_type => 'COSINE') ORDER BY distance")
    rows = bq().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    return [(r["sku"], 1 - r["distance"]) for r in rows]
