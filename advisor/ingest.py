"""Offline ingestion with an index LIFECYCLE: plan -> incremental apply -> log -> rollback.

    uv run --env-file copilot/.env python -m advisor.ingest --dry-run          # plan only: what would change
    uv run --env-file copilot/.env python -m advisor.ingest                    # apply incrementally
    uv run --env-file copilot/.env python -m advisor.ingest --rollback-minutes 15   # restore the state of 15 min ago

Flow
----
products.json --product_text()--+                                          +--> beauty_rag.products (all columns)
kb/*.md ------chunk_doc()-------+--> embed() (Vertex, cached by content) -> staging tables -> MERGE
                                                                           +--> beauty_rag.chunks   (all docs, all versions)
then rebuild the PHYSICAL customer tables (VECTOR_SEARCH rejects views):
    products_public = products minus cost/supplier/internal_note, visibility = 'public'
    chunks_public   = chunks WHERE public AND not a user review AND status = 'current'
    reviews_public  = public user reviews (untrusted, outside the advice index)
and append one row to beauty_rag.index_runs (who/when/what changed, model, dim, git commit).

Why incremental (MERGE) and not "delete everything, reload":
- chunk_id is a hash of (doc, version, section, text): an edited paragraph = a NEW id. So a MERGE on chunk_id
  inserts new/edited chunks, DELETES chunks whose source disappeared or changed (WHEN NOT MATCHED BY SOURCE), and
  leaves unchanged ones untouched: deletions propagate, nothing is duplicated, and only new text is embedded.
- products are keyed by SKU and updated only when their content hash changes.
- The run is idempotent: re-running with no source change = 0 inserted, 0 updated, 0 deleted, 0 embedding calls.

Rollback: BigQuery time travel (FOR SYSTEM_TIME AS OF, 7-day window by default) restores the base tables to an
earlier instant, then the public tables are rebuilt. No backup job to maintain for short-horizon mistakes.

Embedding migration (new model or dimension): every row records embed_model + embed_dim; the cache key includes
both, so vectors are never mixed. A migration = ingest into a new dataset (blue/green), run the eval on both,
switch the serving config, keep the old dataset for rollback.

Library: google-cloud-bigquery (load jobs = batch, no query cost; MERGE = one atomic DML statement).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery

from advisor import config
from advisor.chunking import load_kb, product_text
from advisor.embeddings import embed

DATA = Path(__file__).resolve().parent / "data"
F, S, I, B, A, TS = "FLOAT64", "STRING", "INT64", "BOOL", "REPEATED", "TIMESTAMP"
INTERNAL_PRODUCT_FIELDS = ("cost_price_eur", "supplier", "internal_note")
# What the advice retriever may read: brand-authored, customer-visible, CURRENT content only.
ADVICE_INDEX_FILTER = "visibility = 'public' AND doc_type != 'avis' AND status = 'current'"
DS = f"{config.PROJECT}.{config.BQ_DATASET}"

PRODUCTS_SCHEMA = [
    bigquery.SchemaField("sku", S, mode="REQUIRED"), bigquery.SchemaField("name", S), bigquery.SchemaField("brand", S),
    bigquery.SchemaField("category", S), bigquery.SchemaField("concerns", S, mode=A),
    bigquery.SchemaField("skin_types", S, mode=A),
    bigquery.SchemaField("actives", "RECORD", mode=A, fields=[bigquery.SchemaField("name", S), bigquery.SchemaField("pct", F)]),
    bigquery.SchemaField("fragrance_free", B), bigquery.SchemaField("price_eur", F), bigquery.SchemaField("promo_price_eur", F),
    bigquery.SchemaField("size_ml", F), bigquery.SchemaField("usage_ml_per_day", F), bigquery.SchemaField("routine_moment", S),
    bigquery.SchemaField("description", S), bigquery.SchemaField("image", S), bigquery.SchemaField("visibility", S),
    bigquery.SchemaField("cost_price_eur", F), bigquery.SchemaField("supplier", S), bigquery.SchemaField("internal_note", S),
    bigquery.SchemaField("search_text", S), bigquery.SchemaField("content_sha", S),
    bigquery.SchemaField("embedding", F, mode=A), bigquery.SchemaField("embed_model", S), bigquery.SchemaField("embed_dim", I),
    bigquery.SchemaField("ingested_at", TS),
]
CHUNKS_SCHEMA = [
    bigquery.SchemaField("chunk_id", S, mode="REQUIRED"), bigquery.SchemaField("doc_id", S), bigquery.SchemaField("title", S),
    bigquery.SchemaField("doc_type", S), bigquery.SchemaField("visibility", S), bigquery.SchemaField("version", S),
    bigquery.SchemaField("status", S), bigquery.SchemaField("effective_date", S), bigquery.SchemaField("section", S),
    bigquery.SchemaField("position", I), bigquery.SchemaField("text", S), bigquery.SchemaField("embed_text", S),
    bigquery.SchemaField("embedding", F, mode=A), bigquery.SchemaField("embed_model", S), bigquery.SchemaField("embed_dim", I),
    bigquery.SchemaField("ingested_at", TS),
]
RUNS_SCHEMA = [bigquery.SchemaField(n, t) for n, t in (
    ("run_id", S), ("ts", TS), ("git_commit", S), ("embed_model", S), ("embed_dim", I), ("embed_api_calls", I),
    ("chunks_inserted", I), ("chunks_deleted", I), ("products_inserted", I), ("products_updated", I),
    ("products_deleted", I), ("chunks_public", I), ("note", S))]


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def build_rows(with_embeddings: bool = True) -> tuple[list[dict], list[dict], int]:
    """Read local files, build table rows with embeddings. Returns (product_rows, chunk_rows, api_calls).
    with_embeddings=False (dry run): ids and hashes only, NO embedding calls and no cache writes. A plan must be
    side-effect free; the first version embedded during --dry-run (and cached the vectors, hiding them from the
    apply's embed_api_calls count)."""
    now = datetime.now(timezone.utc).isoformat()
    meta = {"embed_model": config.EMBED_MODEL, "embed_dim": config.EMBED_DIM, "ingested_at": now}
    products = json.loads((DATA / "products.json").read_text())
    texts = [product_text(p) for p in products]
    pvecs, n1 = embed(texts) if with_embeddings else ([[0.0] * config.EMBED_DIM] * len(texts), 0)
    prows = [{**p, "search_text": t, "content_sha": _sha({**p, "_t": t, "_m": config.EMBED_MODEL, "_d": config.EMBED_DIM}),
              "embedding": v, **meta} for p, t, v in zip(products, texts, pvecs)]
    chunks = load_kb(DATA / "kb")
    cvecs, n2 = embed([c.embed_text for c in chunks]) if with_embeddings else ([[0.0] * config.EMBED_DIM] * len(chunks), 0)
    crows = [{"chunk_id": c.chunk_id, "doc_id": c.doc_id, "title": c.title, "doc_type": c.doc_type,
              "visibility": c.visibility, "version": c.version, "status": c.status, "effective_date": c.effective_date,
              "section": c.section, "position": c.position, "text": c.text, "embed_text": c.embed_text,
              "embedding": v, **meta} for c, v in zip(chunks, cvecs)]
    return prows, crows, n1 + n2


def _table_fields(bq, ref) -> list[str] | None:
    try:
        return [f.name for f in bq.get_table(ref).schema]
    except Exception:  # noqa: BLE001 - not found
        return None


def _load(bq, ref, rows, schema) -> None:
    bq.load_table_from_json(rows, ref, job_config=bigquery.LoadJobConfig(
        schema=schema, write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)).result()


def _merge(bq, table: str, key: str, schema, rows, update_when_changed: bool) -> dict:
    """Stage rows, then ONE atomic MERGE. If the target is missing or its schema changed, it's created from the
    staging table (a schema migration is a full rebuild, logged as such)."""
    target, staging = f"{DS}.{table}", f"{DS}.{table}_staging"
    _load(bq, staging, rows, schema)
    names = [f.name for f in schema]
    if _table_fields(bq, target) != names:
        bq.query(f"CREATE OR REPLACE TABLE `{target}` AS SELECT * FROM `{staging}`").result()
        return {"inserted": len(rows), "updated": 0, "deleted": 0, "migrated": True}
    upd = (f"WHEN MATCHED AND T.content_sha != S.content_sha THEN UPDATE SET "
           + ", ".join(f"{n} = S.{n}" for n in names if n != key) + " ") if update_when_changed else ""
    job = bq.query(f"MERGE `{target}` T USING `{staging}` S ON T.{key} = S.{key} {upd}"
                   f"WHEN NOT MATCHED THEN INSERT ROW WHEN NOT MATCHED BY SOURCE THEN DELETE")
    job.result()
    st = job.dml_stats
    return {"inserted": st.inserted_row_count if st else None, "updated": st.updated_row_count if st else None,
            "deleted": st.deleted_row_count if st else None, "migrated": False}


def rebuild_public(bq) -> int:
    hidden = ", ".join(INTERNAL_PRODUCT_FIELDS)
    for name in ("products_public", "chunks_public", "reviews_public"):       # drop legacy views of the same name
        try:
            if bq.get_table(f"{DS}.{name}").table_type == "VIEW":
                bq.delete_table(f"{DS}.{name}")
        except Exception:  # noqa: BLE001
            pass
    bq.query(f"CREATE OR REPLACE TABLE `{DS}.products_public` AS "
             f"SELECT * EXCEPT({hidden}) FROM `{DS}.products` WHERE visibility = 'public'").result()
    bq.query(f"CREATE OR REPLACE TABLE `{DS}.chunks_public` AS SELECT * FROM `{DS}.chunks` WHERE {ADVICE_INDEX_FILTER}").result()
    bq.query(f"CREATE OR REPLACE TABLE `{DS}.reviews_public` AS SELECT * FROM `{DS}.chunks` "
             f"WHERE visibility = 'public' AND doc_type = 'avis' AND status = 'current'").result()
    cols = [f.name for f in bq.get_table(f"{DS}.products_public").schema]
    assert not set(INTERNAL_PRODUCT_FIELDS) & set(cols), "internal columns visible in products_public"
    return list(bq.query(f"SELECT COUNT(*) n FROM `{DS}.chunks_public`").result())[0].n


def plan(bq, prows, crows) -> dict:
    """Dry run: compare ids/hashes with what's in BigQuery, change nothing."""
    def ids(sql):
        try:
            return {tuple(r.values()) for r in bq.query(sql).result()}
        except Exception:  # noqa: BLE001 - table missing on first run
            return set()
    cur_c = {r[0] for r in ids(f"SELECT chunk_id FROM `{DS}.chunks`")}
    cur_p = dict(ids(f"SELECT sku, content_sha FROM `{DS}.products`")) if _table_fields(bq, f"{DS}.products") and \
        "content_sha" in _table_fields(bq, f"{DS}.products") else {}
    new_c = {r["chunk_id"] for r in crows}
    new_p = {r["sku"]: r["content_sha"] for r in prows}
    return {"chunks_insert": len(new_c - cur_c), "chunks_delete": len(cur_c - new_c), "chunks_keep": len(new_c & cur_c),
            "products_insert": len(set(new_p) - set(cur_p)), "products_delete": len(set(cur_p) - set(new_p)),
            "products_update": sum(1 for k in set(new_p) & set(cur_p) if new_p[k] != cur_p[k])}


def rollback(bq, minutes: int) -> None:
    """Restore base tables as they were `minutes` ago (time travel), then rebuild the customer tables."""
    for t in ("products", "chunks"):
        bq.query(f"CREATE OR REPLACE TABLE `{DS}.{t}` AS SELECT * FROM `{DS}.{t}` "
                 f"FOR SYSTEM_TIME AS OF TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {int(minutes)} MINUTE)").result()
    n = rebuild_public(bq)
    log_run(bq, {"note": f"rollback {minutes} min", "chunks_public": n})
    print(f"ROLLBACK_OK to -{minutes} min, chunks_public={n}")


def log_run(bq, rec: dict) -> None:
    ref = f"{DS}.index_runs"
    if _table_fields(bq, ref) is None:
        bq.create_table(bigquery.Table(ref, schema=RUNS_SCHEMA))
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = ""
    row = {"run_id": uuid.uuid4().hex[:12], "ts": datetime.now(timezone.utc).isoformat(), "git_commit": commit,
           "embed_model": config.EMBED_MODEL, "embed_dim": config.EMBED_DIM, **rec}
    bq.load_table_from_json([row], ref, job_config=bigquery.LoadJobConfig(
        schema=RUNS_SCHEMA, write_disposition=bigquery.WriteDisposition.WRITE_APPEND)).result()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback-minutes", type=int)
    args = ap.parse_args()
    bq = bigquery.Client(project=config.PROJECT, location=config.BQ_LOCATION)
    ds = bigquery.Dataset(DS)
    ds.location = config.BQ_LOCATION            # EU multi-region: data at rest stays in the EU
    bq.create_dataset(ds, exists_ok=True)
    if args.rollback_minutes:
        return rollback(bq, args.rollback_minutes)

    t0 = time.perf_counter()
    prows, crows, calls = build_rows(with_embeddings=not args.dry_run)
    assert len({r["chunk_id"] for r in crows}) == len(crows), "duplicate chunk ids"
    assert all(len(r["embedding"]) == config.EMBED_DIM for r in prows + crows), "wrong vector size"
    p = plan(bq, prows, crows)
    if args.dry_run:
        from advisor.embeddings import _key, _load_cache   # count what WOULD be embedded, without calling the API
        cache = _load_cache()
        need = sum(1 for t in [r["search_text"] for r in prows] + [r["embed_text"] for r in crows]
                   if _key(t, "RETRIEVAL_DOCUMENT") not in cache)
        print(f"PLAN {p} embed_api_calls_needed={need}")
        return
    mp = _merge(bq, "products", "sku", PRODUCTS_SCHEMA, prows, update_when_changed=True)
    mc = _merge(bq, "chunks", "chunk_id", CHUNKS_SCHEMA, crows, update_when_changed=False)
    pub = rebuild_public(bq)
    log_run(bq, {"embed_api_calls": calls, "chunks_inserted": mc["inserted"], "chunks_deleted": mc["deleted"],
                 "products_inserted": mp["inserted"], "products_updated": mp["updated"], "products_deleted": mp["deleted"],
                 "chunks_public": pub, "note": "schema migration" if mc["migrated"] or mp["migrated"] else "incremental"})
    print(f"INGEST_OK chunks +{mc['inserted']} -{mc['deleted']}{' (migration)' if mc['migrated'] else ''} | "
          f"products +{mp['inserted']} ~{mp['updated']} -{mp['deleted']}{' (migration)' if mp['migrated'] else ''} | "
          f"chunks_public={pub} | embed_api_calls={calls} | {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
