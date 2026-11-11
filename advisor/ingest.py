"""Offline ingestion: products + knowledge base -> embeddings -> BigQuery. Run once, re-run on content change.

    uv run --env-file copilot/.env python -m advisor.ingest

Flow
----
products.json --product_text()--+                                   +--> beauty_rag.products (ALL columns)
kb/*.md ------chunk_doc()-------+--> embed() (Vertex, cached) --> load (WRITE_TRUNCATE)
                                                                    +--> beauty_rag.chunks   (ALL docs)
then CREATE OR REPLACE TABLE (physical copies, rebuilt each ingest):
    products_public = products minus cost/supplier/internal_note, visibility = 'public'
    chunks_public   = chunks WHERE visibility = 'public'
The customer-facing advisor reads ONLY the *_public tables (R2). Confidential data is filtered by the data
layer, not by asking the model to keep a secret: a prompt can be talked out of a rule, a WHERE clause can't.
Tables, not views: VECTOR_SEARCH does not accept logical views. In production the advisor's service account
is granted only the *_public tables (or a separate public dataset), so even a bug in our code cannot read
internal rows or columns.

Library: `google-cloud-bigquery` is Google's Python client for BigQuery (the serverless data warehouse).
`load_table_from_json` starts a LOAD JOB (batch, free of query cost) instead of streaming inserts.

Idempotency: deterministic ids + WRITE_TRUNCATE = a re-run replaces the table with identical content, it
never appends duplicates. At real catalog scale (millions of SKUs, daily changes) this becomes an
incremental MERGE keyed on (id, content hash) fed by the PIM change stream.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery

from advisor import config
from advisor.chunking import load_kb, product_text
from advisor.embeddings import embed

DATA = Path(__file__).resolve().parent / "data"
F, S, I, B, A = "FLOAT64", "STRING", "INT64", "BOOL", "REPEATED"
INTERNAL_PRODUCT_FIELDS = ("cost_price_eur", "supplier", "internal_note")
# What the advice retriever may read: brand-authored, customer-visible content only (no internal docs, no reviews).
ADVICE_INDEX_FILTER = "visibility = 'public' AND doc_type != 'avis'"

PRODUCTS_SCHEMA = [
    bigquery.SchemaField("sku", S, mode="REQUIRED"), bigquery.SchemaField("name", S), bigquery.SchemaField("brand", S),
    bigquery.SchemaField("category", S), bigquery.SchemaField("concerns", S, mode=A),
    bigquery.SchemaField("skin_types", S, mode=A),
    bigquery.SchemaField("actives", "RECORD", mode=A, fields=[bigquery.SchemaField("name", S), bigquery.SchemaField("pct", F)]),
    bigquery.SchemaField("fragrance_free", B), bigquery.SchemaField("price_eur", F), bigquery.SchemaField("promo_price_eur", F),
    bigquery.SchemaField("size_ml", F), bigquery.SchemaField("usage_ml_per_day", F), bigquery.SchemaField("routine_moment", S),
    bigquery.SchemaField("description", S), bigquery.SchemaField("image", S), bigquery.SchemaField("visibility", S),
    bigquery.SchemaField("cost_price_eur", F), bigquery.SchemaField("supplier", S), bigquery.SchemaField("internal_note", S),
    bigquery.SchemaField("search_text", S),                       # the text BM25 indexes and the model embeds
    bigquery.SchemaField("embedding", F, mode=A),                 # ARRAY<FLOAT64>, what VECTOR_SEARCH reads
    bigquery.SchemaField("ingested_at", "TIMESTAMP"),
]
CHUNKS_SCHEMA = [
    bigquery.SchemaField("chunk_id", S, mode="REQUIRED"), bigquery.SchemaField("doc_id", S), bigquery.SchemaField("title", S),
    bigquery.SchemaField("doc_type", S), bigquery.SchemaField("visibility", S), bigquery.SchemaField("section", S),
    bigquery.SchemaField("position", I), bigquery.SchemaField("text", S), bigquery.SchemaField("embed_text", S),
    bigquery.SchemaField("embedding", F, mode=A), bigquery.SchemaField("ingested_at", "TIMESTAMP"),
]


def build_rows() -> tuple[list[dict], list[dict], int]:
    """Read local files, build table rows with embeddings. Returns (product_rows, chunk_rows, api_calls)."""
    now = datetime.now(timezone.utc).isoformat()
    products = json.loads((DATA / "products.json").read_text())
    texts = [product_text(p) for p in products]
    pvecs, n1 = embed(texts)
    prows = [{**p, "search_text": t, "embedding": v, "ingested_at": now} for p, t, v in zip(products, texts, pvecs)]

    chunks = load_kb(DATA / "kb")
    cvecs, n2 = embed([c.embed_text for c in chunks])
    crows = [{"chunk_id": c.chunk_id, "doc_id": c.doc_id, "title": c.title, "doc_type": c.doc_type,
              "visibility": c.visibility, "section": c.section, "position": c.position, "text": c.text,
              "embed_text": c.embed_text, "embedding": v, "ingested_at": now}
             for c, v in zip(chunks, cvecs)]
    return prows, crows, n1 + n2


def load(bq: bigquery.Client, table: str, rows: list[dict], schema) -> int:
    """Replace a table's content with `rows` via a load job; returns the row count BigQuery now reports."""
    ref = f"{config.PROJECT}.{config.BQ_DATASET}.{table}"
    job = bq.load_table_from_json(rows, ref, job_config=bigquery.LoadJobConfig(
        schema=schema, write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE))
    job.result()                                   # blocks until the job finishes; raises on load errors
    return bq.get_table(ref).num_rows


def create_public_tables(bq: bigquery.Client) -> None:
    """Materialize the customer-visible subset as PHYSICAL tables, rebuilt on every ingest.

    Why tables and not views: BigQuery VECTOR_SEARCH rejects logical views as its base table ("Logical views
    are not supported yet"; found when running the first vector query). A filter written in the chatbot's code
    (WHERE visibility = 'public') would put the security boundary back in application code. Physical tables
    keep it in the data layer: the advisor's service account is granted ONLY these tables (table-level IAM,
    or a separate dataset), so it cannot select internal rows or columns at all.
    Cost: data duplicated (negligible here) and the copy is only as fresh as the last ingest (same as the index).
    """
    ds = f"`{config.PROJECT}.{config.BQ_DATASET}`"
    for name in ("products_public", "chunks_public"):   # drop the old views of the same name if present
        ref = f"{config.PROJECT}.{config.BQ_DATASET}.{name}"
        try:
            if bq.get_table(ref).table_type == "VIEW":
                bq.delete_table(ref)
        except Exception:  # noqa: BLE001 - not found = nothing to drop
            pass
    hidden = ", ".join(INTERNAL_PRODUCT_FIELDS)
    bq.query(f"CREATE OR REPLACE TABLE {ds}.products_public AS "
             f"SELECT * EXCEPT({hidden}) FROM {ds}.products WHERE visibility = 'public'").result()
    # Customer reviews are public but USER-WRITTEN: they stay out of the advice index (chunks_public) and go to
    # their own table. Measured (R3): the cross-encoder ranked the review carrying an injected instruction #1 for
    # all 3 promo-code questions (score 0.68 on one, above the no-answer threshold) -> it reached the model.
    # Injected text is written to LOOK relevant, so a better ranker makes the exposure worse, not better.
    bq.query(f"CREATE OR REPLACE TABLE {ds}.chunks_public AS "
             f"SELECT * FROM {ds}.chunks WHERE {ADVICE_INDEX_FILTER}").result()
    bq.query(f"CREATE OR REPLACE TABLE {ds}.reviews_public AS "
             f"SELECT * FROM {ds}.chunks WHERE visibility = 'public' AND doc_type = 'avis'").result()


def main() -> None:
    t0 = time.perf_counter()
    prows, crows, calls = build_rows()
    t_embed = time.perf_counter() - t0
    assert len({r["chunk_id"] for r in crows}) == len(crows), "duplicate chunk ids"
    assert all(len(r["embedding"]) == config.EMBED_DIM for r in prows + crows), "wrong vector size"

    bq = bigquery.Client(project=config.PROJECT, location=config.BQ_LOCATION)
    ds = bigquery.Dataset(f"{config.PROJECT}.{config.BQ_DATASET}")
    ds.location = config.BQ_LOCATION            # EU multi-region: data at rest stays in the EU
    bq.create_dataset(ds, exists_ok=True)
    n_p = load(bq, "products", prows, PRODUCTS_SCHEMA)
    n_c = load(bq, "chunks", crows, CHUNKS_SCHEMA)
    assert (n_p, n_c) == (len(prows), len(crows)), f"row count mismatch: {(n_p, n_c)} vs {(len(prows), len(crows))}"
    create_public_tables(bq)

    pub = list(bq.query(f"SELECT COUNT(*) n FROM `{config.PROJECT}.{config.BQ_DATASET}.chunks_public`").result())[0].n
    rev = list(bq.query(f"SELECT COUNT(*) n FROM `{config.PROJECT}.{config.BQ_DATASET}.reviews_public`").result())[0].n
    cols = [f.name for f in bq.get_table(f"{config.PROJECT}.{config.BQ_DATASET}.products_public").schema]
    assert not set(INTERNAL_PRODUCT_FIELDS) & set(cols), "internal columns visible in products_public"
    print(f"INGEST_OK products={n_p} chunks={n_c} chunks_public={pub} reviews_public={rev} docs={len({r['doc_id'] for r in crows})} "
          f"embed_api_calls={calls} embed_s={t_embed:.1f} total_s={time.perf_counter() - t0:.1f} "
          f"dim={config.EMBED_DIM} model={config.EMBED_MODEL}")


if __name__ == "__main__":
    main()
