# ADR-003: Session Persistence & Caller Ownership Architecture

Status: accepted · Date: 2026-11-23 · Spec: `docs/specs/SPEC-010-persistent-sessions-multi-instance-and-ownership.md`

Each decision: context → decision → alternatives compared → consequences / when to revisit.

---

## 1. Session Service Backend for Multi-Instance Cloud Run

- **Context.** On Cloud Run with `min-instances >= 1` and horizontal autoscaling, consecutive HTTP requests from the same customer routinely land on different container instances. Using `InMemorySessionService` loses conversation history on every container switch or scale-to-zero event; using `SqliteSessionService` ties state to an ephemeral container filesystem and breaks under concurrent multi-instance writes.
- **Decision.** Wire ADK 2.13.0's **`DatabaseSessionService`** (`google-adk[db]`, SQLAlchemy 2.x asyncio + `asyncpg`) targeting **Cloud SQL for PostgreSQL** in production (and local Docker Postgres via `docker-compose.yml`), while keeping `SqliteSessionService` for single-process local dev and `InMemorySessionService` for unit tests.
- **Comparison Matrix:**

| Dimension | `DatabaseSessionService` (Cloud SQL PostgreSQL) | `VertexAiSessionService` (Agent Engine Sessions) | `SqliteSessionService` (Local File) |
|---|---|---|---|
| **Multi-instance Cloud Run** | Yes — shared ACID RDBMS, row-level locking (`SELECT ... FOR UPDATE`), optimistic stale-session check | Yes — managed Google Cloud session API | No — local disk only; breaks across replicas |
| **Control & Data Residency** | Full SQL control, VPC/Private IP, custom GDPR retention/deletion SQL, EU region pinned | Managed by Vertex AI Reasoning Engine (`reasoningEngines/.../sessions`); less schema control | Local disk only |
| **Cost Model** | Predictable Cloud SQL instance cost (shared across services/workloads) | Per-API-call / managed Agent Engine billing | Zero (local file) |
| **Operational Overhead** | Manage Cloud SQL sizing, connection pool (`pool_pre_ping=True`, Cloud SQL Auth Proxy), schema upgrades | Zero DB ops; managed by GCP | Zero ops |
| **Vendor Lock-in** | Low — standard PostgreSQL + SQLAlchemy | High — coupled to Vertex AI Agent Engine | None (dev only) |
| **Best Fit** | Cloud Run microservices with strict SQL/GDPR/audit ownership | Teams deploying agents directly on Vertex AI Agent Engine | Local single-developer laptops and offline CI |

- **Consequences.**
  - Application containers remain stateless; any instance can resume any conversation or HITL approval pause.
  - Connection pooling must be sized per Cloud Run instance (`pool_size` × max instances < Cloud SQL `max_connections`) and `pool_pre_ping=True` (enabled by default in ADK 2.13.0 for non-SQLite URLs) protects against idle-connection drops.
- **When to Revisit.** If the organization standardizes on deploying agents inside Vertex AI Agent Engine rather than Cloud Run, switching to `VertexAiSessionService` removes Cloud SQL management while keeping the same ADK `BaseSessionService` interface.

---

## 2. Caller Identity, Session Ownership & Reviewer RBAC

- **Context.** ADK's `user:` state prefix (`user:id`) is a **storage scope** (shared across sessions of the same `user_id` for a given `app_name`), **not** an access-control mechanism. Hardcoding `user_id="web"` and accepting unauthenticated reviewer decisions exposes both cross-customer IDOR and unauthorized refunds.
- **Decision.**
  1. Authenticate every API request at the FastAPI boundary (`backend/auth.py`) using signed bearer tokens (HMAC-SHA256 in the lab; OIDC / IAP / API Gateway JWT in GCP).
  2. Derive `user_id` and `role` (`customer` | `reviewer`) exclusively from the verified token — never from LLM arguments or request body fields.
  3. Bind each `thread_id` to its creating `user_id` (`SessionOwnershipStore`) and return HTTP `403` on cross-customer reuse.
  4. Enforce `owner_id` checks on both `open_claim` (against order `customer_id`) and `status` (against claim `owner_id`), injecting `user:id` from `tool_context.state`.
  5. Gate `GET /claims/pending` and `POST /claims/{claim_id}/decision` behind `role == "reviewer"`, recording the verified token's `user_id` as the reviewer.
- **Consequences.**
  - Eliminates cross-customer session hijacking, cross-customer order/claim reads, and spoofed reviewer approvals, verified by negative authorization tests in `tests/test_sessions_and_auth.py`.
