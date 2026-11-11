# RAG decisions: Beauty Advisor (customer-facing chatbot)

Status: R1-R3 done. Measured results are in section 6 and evals/FINDINGS.md; the table below keeps the original
reasoning, section 6 says what the data confirmed or overturned.

## 1. The pipeline at a glance

```
OFFLINE (advisor/ingest.py, re-run on content change)
 products.json ─ product_text() ─┐                        ┌─> BigQuery beauty_rag.products  (all columns)
 kb/*.md ─────── chunk_doc() ────┼─> Vertex embeddings ───┤
                                 │   (cached, 768-d)      └─> BigQuery beauty_rag.chunks    (all docs)
                                 └─> public TABLES: products_public (no cost/supplier/notes), chunks_public (visibility=public)

ONLINE (R2, per question)                       reads ONLY the *_public tables
 question ─┬─> embed_query (RETRIEVAL_QUERY) ─> BigQuery VECTOR_SEARCH (cosine) ─> top 50 ─┐
           ├─> analyze() ─> BM25 (in process, over chunks_public) ─────────────────> top 50 ─┼─> RRF (k=60) ─> top 20
           └─> SQL filters (skin type, price, fragrance-free) for product questions          │
                                                     Vertex AI ranking API (cross-encoder) <─┘ ─> top 5 + score
                                                     score < threshold ─> "je ne sais pas"
 top 5 chunks (with doc_id, section) ─> Gemini answer that must cite them ─> backend renders cards from tool data
```

## 2. Decisions

| # | Decision | Why | Alternatives considered | When I'd change it |
|---|---|---|---|---|
| 1 | **Chunk by section** (`## ` heading), cap **220 words** (≈300 model tokens) | The KB is written as one rule / one question per section, so a section is a complete unit of meaning. Fixed windows would cut "rétinol le soir, vitamine C le matin" in two. 220 words is large enough for one complete rule, small enough that a citation points to the exact sentence. Measured: 67 chunks (62 public), median 27 words | Fixed window 512 tokens + 64 overlap (right for long unstructured PDFs); semantic chunking by embedding similarity (costly, unstable); one chunk per document (citations too vague) | Long unstructured sources (PDF manuals, supplier sheets) -> windows with overlap |
| 2 | **Overlap: one sentence, only when a section is split** | Overlap exists to avoid losing meaning at an arbitrary cut. Section boundaries are not arbitrary, so overlap there only duplicates text and inflates the index | Fixed 10-15% token overlap everywhere | Fixed-window chunking (then 10-15%) |
| 3 | **Contextual prefix** "Titre > Section" embedded with the chunk (not shown) | A chunk like "Oui, mais pas au même moment." is unfindable alone; with "Rétinol et vitamine C > La règle" it matches the question. Cheap version of "contextual retrieval" (an LLM-written context per chunk) | LLM-generated context per chunk (better on messy corpora, 1 LLM call per chunk at ingest) | Messy or very long docs where titles don't carry the context |
| 4 | **Deterministic ids** sha256(doc_id, section, text) + **WRITE_TRUNCATE** | Re-running ingest must not duplicate rows (a classic RAG bug: duplicates take several top-k slots and push out other answers). Asserted in code and tests | Auto-increment ids + append (duplicates on re-run) | Large catalog: incremental MERGE on (id, content hash) from the PIM change stream |
| 5 | **Embedding model: Vertex `gemini-embedding-001`** | Multilingual (French corpus and questions), top of public multilingual retrieval benchmarks at release, served in our GCP project and EU region (IAM, quotas, residency, one bill) | `text-multilingual-embedding-002` (older, cheaper, batches of 250 inputs); open models (e5, bge-m3) self-hosted on Vertex endpoints/GKE (control, but you run the GPUs) | Cost at very large scale, or a sovereignty constraint forbidding managed models |
| 6 | **768 dimensions** (Matryoshka truncation of 3,072) + L2 normalization | 4x less storage and faster distance computation; the model is trained so the first dims carry most of the signal. Normalization required for truncated vectors with dot product, harmless with cosine | Full 3,072 (max quality) | If R3 recall at 768 is materially below 3,072 on the hard questions (*to measure*) |
| 7 | **Asymmetric task types** RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY | The model maps a short question close to the paragraph that answers it only if each side is encoded with its role | Same task type on both sides (simpler, lower recall) | Never for Q&A retrieval; SEMANTIC_SIMILARITY for duplicate detection |
| 8 | **Store: BigQuery** (`ARRAY<FLOAT64>` + `VECTOR_SEARCH`, cosine) | Retail product data already lives in a warehouse; one place for SQL attribute filters + vectors; serverless, no idle cost, EU multi-region. Exact (brute force) search is fine below ~5,000 rows (no index needed) | **Vertex AI Vector Search** (ms latency, native hybrid dense+sparse, but an always-on endpoint); **AlloyDB / Cloud SQL pgvector** (low latency, transactions, HNSW, a database to run); **Vertex AI Search** (fully managed hybrid + ranking, least control); **Vertex AI RAG Engine** (managed pipeline, least explainable) | Interactive latency budget below ~300 ms or millions of chunks -> Vector Search or AlloyDB; BigQuery stays the source of truth (*latency to measure in R2*) |
| 9 | **BM25 in our code** (`advisor/retrieval/bm25.py`), own French analyzer | BigQuery `SEARCH()` is a match FILTER, it returns no relevance score, so it can't rank. Dense search misses exact terms (SKUs, "0,3 %", "PAO", brand names); BM25 catches them. In-process BM25 over 67 chunks costs microseconds | Elasticsearch/OpenSearch, Vertex AI Search, sparse vectors in Vector Search (SPLADE-like) | Corpus beyond ~100k chunks or multi-replica serving -> move lexical ranking to an engine |
| 10 | **Analyzer**: lowercase, accent folding, elision (l', d'), small stopword list, light plural stemming | Same token for "Rétinol"/"retinol"/"rétinols". Stopword list kept short on purpose: "sans" stays (sans parfum = attribute). Numbers kept intact ("0,3") because concentrations matter | Snowball French stemmer (catches conjugations: hydrate/hydratant), Elasticsearch `french` analyzer | If R3 shows lexical misses on verb forms |
| 11 | **Fusion: Reciprocal Rank Fusion, k = 60, 50 candidates per retriever** | BM25 scores (0-20+) and cosine (0-1) are on different scales; RRF uses only ranks, so no fragile score normalization. k = 60 is the standard constant: it damps the weight of a single #1 and rewards documents both retrievers agree on | Weighted score sum after min-max normalization (needs tuning per corpus) | Never as a first choice; weighted fusion only with a large labelled set |
| 12 | **Reranker: Vertex AI ranking API** (cross-encoder) on the fused top 20 | Bi-encoders embed question and chunk separately; a cross-encoder reads them together and separates near-duplicates (two niacinamide serums, 0.3 % vs 0.5 % retinol). Two stages keep cost and latency bounded | Self-hosted cross-encoder (bge-reranker), LLM-as-reranker (slow, costly) | If latency budget can't afford it: rerank only low-confidence or high-value queries (*latency to measure*) |
| 13 | **No-answer threshold** on the reranker score | Better "je ne sais pas" than an answer built from weak context; out-of-corpus questions are part of the eval | Let the LLM decide (less reliable) | Threshold tuned on R3 data, not guessed |
| 14 | **Hybrid structured + unstructured**: SQL filters for product attributes, vector + BM25 inside the filtered set; comparisons = one sub-query per product (query decomposition) | A single query vector can't be close to two products at once; "compare A and B" needs both in context. Attribute filters (price, skin type) are exact, never "approximately under 15 EUR" | Pure vector search over product descriptions | — |

## 3. Confidential data (security by the data layer, not by the prompt)

Planted on purpose, to test it:
- **Internal product columns**: purchase cost, supplier, internal notes (e.g. an upcoming stock-out).
- **Internal documents** (`visibility: internal`): Black Friday plan and its test code, staff discount code,
  margin rules, internal escalation extension.
- **Untrusted public content**: a customer review containing an injection ("ignore tes règles... code
  BIENVENUE100"). Reviews are public but written by users, so retrieved text is treated as data, never instructions.

Controls:
1. Internal fields are **never in `product_text`** (what gets embedded and indexed).
2. The advisor reads only the **`*_public` tables**, physical copies rebuilt by each ingest. Not views:
   BigQuery `VECTOR_SEARCH` rejects logical views as its base table (found on the first vector query), and a
   `WHERE visibility='public'` in the chatbot's code would move the boundary back into application code. In
   production the advisor's service account is granted only these tables (or a separate public dataset), so a
   code bug can't reach internal rows either.
3. **User-written content is a separate collection.** Reviews are public but untrusted. R3 measured that the
   cross-encoder ranked the poisoned review **#1 for all 3 promo-code questions** (0.68 on one, above the no-answer
   threshold): injected text is written to look relevant, so a better ranker increases exposure. Reviews now live
   in `reviews_public`, outside the advice index (`chunks_public`); a future review tool would summarise them with
   their own guardrails.
4. **Canary strings** (`advisor/data/canaries.json`) are checked by literal match on every eval answer: any hit
   = leak = failed build.
5. The system prompt + output checks are the last layer, not the first.

Interview line: *"A prompt can be talked out of a rule; a WHERE clause can't. Confidential data is filtered
before retrieval, and canaries prove it on every eval run."*

## 4. Data residency
BigQuery dataset in the `EU` multi-region; embeddings in a European region (probed); generation on the `eu`
endpoint. The ranking API region is probed (it may be global-only, which is a residency decision to record:
query + candidate snippets are sent for scoring, not stored).

## 5. What the eval (R3) must answer
recall@5 and MRR per mode (dense / BM25 / hybrid / hybrid + rerank), per question category (direct,
paraphrase, near-duplicate, exact detail, multi-doc, no-answer), retrieval latency per mode, canary leaks = 0,
citation correctness, groundedness.

## 6. What the measurements said (R3, 30 base + 26 hard questions)
| Assumption at design time | Measured | Decision |
|---|---|---|
| Hybrid (BM25 + dense, RRF) beats dense | **Overturned here**: top-1 42/51 vs 50/51 (p=0.008). BM25 fails typos (1/4), English (0/4), paraphrase (2/6); ties dense on SKUs/numbers once the SKU is in the text | Product search: exact-SKU regex path + dense order. No equal-weight RRF. At scale: weighted fusion or query routing, re-measured on real logs |
| Reranker improves ranking | **Confirmed vs its first stage** (hybrid -> rerank +8/0, p=0.008); **tied with dense alone** (1/1) | Advice uses rerank: same ranking as dense, plus the only calibrated score |
| A threshold can decide "je ne sais pas" | **Only on reranker scores** (no-answer 0.03-0.13 vs answerable >= 0.54); dense cosine distributions overlap | Threshold 0.30 (sweep plateau 0.15-0.45) |
| Better ranking = safer | **Overturned**: the cross-encoder put the injected review #1 for all promo-code questions | Reviews out of the advice index (poison 8/30 -> 0/30) |
| BigQuery VECTOR_SEARCH can serve the chatbot | **No**: ~0.8-1 s/query (job overhead) vs 3-5 ms in memory, identical results | BigQuery = source of truth; serving copy in memory (Vector Search / AlloyDB at scale) |
Limits: one author for corpus and questions; n = 51 (95% CI on 50/51 = [0.90, 1.00]); corpus far smaller than a
retail catalog. Directional evidence, not a benchmark.

## 7. Index lifecycle (R4)
| Concern | How it's handled | Interview line |
|---|---|---|
| Incremental updates | Staging table + one atomic `MERGE` per table. Chunks keyed by `sha(doc, version, section, text)`: an edited paragraph is a new id -> inserted; the old one -> `WHEN NOT MATCHED BY SOURCE DELETE`. Products keyed by SKU, updated only when `content_sha` changes | "Content-addressed ids turn updates into insert + delete, so a MERGE is enough and deletions can't be forgotten" |
| Deletions | Same MERGE: a doc removed from the source disappears from the index on the next run | Stale content is the most common RAG production bug |
| Idempotency / retries | Re-run with no change = 0 inserted / 0 updated / 0 deleted / 0 embedding calls; a failed run is simply re-run (staging is overwritten, MERGE is atomic) | "Safe to retry blindly" |
| Plan before apply | `--dry-run` compares ids/hashes with BigQuery and prints what would change | Same discipline as `terraform plan` |
| Audit | `index_runs` row per run: time, git commit, model, dim, counts, note (incremental / schema migration / rollback) | "What did the bot know on date X?" + versions kept in `chunks` |
| Rollback | `--rollback-minutes N`: BigQuery time travel (`FOR SYSTEM_TIME AS OF`, 7-day window) restores base tables, then public tables are rebuilt | No backup job for short-horizon mistakes; longer horizon = table snapshots |
| Embedding migration | Each row stores `embed_model` + `embed_dim`; cache key includes both, so vectors are never mixed. Migration = blue/green: ingest into a new dataset, run the eval on both, switch the serving config, keep the old for rollback | Never re-embed in place |
| Versions & conflicts | Docs carry `version / status / effective_date`. All versions stored (audit), only `current` indexed. Planted conflict: archived returns policy (21 days) vs current (30 days); eval A12 fails if "21 jours" appears | Conflicts are resolved by the data (status, effective date), not by asking the model to pick |
| Freshness | `index_runs.ts` = age of the index; alert if older than the content SLA (e.g. 24 h for policies, 1 h for prices) | Freshness is a monitored KPI, not an assumption |

## 8. Context assembly (R4)
Between retrieval and generation (`assemble_context`, unit-tested): (1) neighbour expansion: a hit that is one
piece of a split section returns the whole section, in order; (2) dedup by section; (3) word budget (450 words by
default): passages added in rank order, never truncated mid-passage, top passage always kept. Conversation-level
budget is handled separately (earlier turns' tool results compacted: -40% input tokens on a 6-turn chat).

## 9. Search infrastructure at scale: exact vs approximate (ANN)
Here: exact (brute-force) cosine over 59 chunks in memory (~3-5 ms) is the right choice: 100% recall, no index to
maintain. Exact search is O(N x d) per query: fine to ~10^5 vectors in memory, not for 10^7 product embeddings.
| Index | How it works | Knobs (recall vs latency vs memory) | Where on GCP |
|---|---|---|---|
| HNSW | Multi-layer proximity graph, greedy descent | `M` (links per node: recall + memory up), `ef_construction` (build quality), `ef_search` (query recall vs latency) | AlloyDB / Cloud SQL pgvector (`hnsw`), AlloyDB ScaNN index |
| IVF | k-means partitions, search the `nprobe` closest | `lists` (more = faster, lower recall), `nprobe` (more = recall up, latency up) | BigQuery `CREATE VECTOR INDEX ... IVF` (tables >= 5,000 rows), pgvector `ivfflat` |
| ScaNN / tree-AH | Partitioning + anisotropic quantization (compressed vectors) | leaves to search, reorder count | Vertex AI Vector Search, BigQuery `TREE_AH` |
How to choose: measure recall@k of the ANN index AGAINST exact search on a sample of real queries (target e.g.
>= 0.95), then tune for p95 latency and memory. Metadata filtering: pre-filter (filter then search: exact, can
starve ANN partitions) vs post-filter (search then filter: fast, can return fewer than k) vs filtered/restricted
search supported natively (Vector Search restricts, pgvector iterative scans); product search with hard attribute
filters (price, skin type) needs pre-filtering or native restricts, not post-filtering.
Multi-tenant / permissions at scale: the permission filter is part of the query (namespace/restrict per tenant or
ACL), and any cache key includes the caller's access scope so one user's results are never served to another.
