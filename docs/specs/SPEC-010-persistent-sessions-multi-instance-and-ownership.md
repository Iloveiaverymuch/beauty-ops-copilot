# SPEC-010: Persistent Sessions, Multi-Instance Continuity & Ownership (W1)

## 1. Problem Statement & Motivation
- **Context**: Session persistence (`backend/engines.py`), API authentication & authorization (`backend/app.py`, `backend/auth.py`), and order/claim ownership (`advisor/claims.py`, `claims/tools.py`, `mock_api/stock_api.py`).
- **Root Cause / Limitation**:
  1. `backend/engines.py` only wires `SqliteSessionService` (local file) or `InMemorySessionService`; neither works across horizontally scaled Cloud Run instances.
  2. `backend/engines.py` hardcodes `user_id="web"` for every request, and `backend/app.py` exposes `/chat`, `/chat/confirm`, `/chat/sync`, `/claims/pending`, and `/claims/{claim_id}/decision` without caller authentication or role checks.
  3. `advisor/claims.py` and `claims/tools.py` do not verify that the authenticated customer owns the order or claim, creating an IDOR vulnerability.
- **Evidence**: `docs/PROJECT_HANDOVER.md` P1 backlog item ("ownership and reviewer authorization"); `backend/engines.py:44-55`.

## 2. Current Behavior vs. Proposed Behavior
- **Current Behavior**: Any unauthenticated client can resume any `thread_id` under `user_id="web"`, open/read claims on any order, and approve/reject pending claims with an arbitrary `reviewer` string.
- **Proposed Behavior**:
  1. **Database-backed Sessions**: Support `SESSIONS=database` with `DATABASE_URL` (PostgreSQL `postgresql+asyncpg://...` via Docker/Cloud SQL or `sqlite+aiosqlite:///...` for offline tests) using ADK 2.13.0's `DatabaseSessionService(db_url=...)`. Retain `SESSIONS=sqlite` (local default) and `SESSIONS=memory` (unit tests). Add `docker-compose.yml` with `postgres:16-alpine`.
  2. **Multi-Instance Continuity**: Two distinct `AdkEngine` / `Runner` instances connected to the same `DatabaseSessionService` share conversation history and `user:` state seamlessly across turns.
  3. **Signed-Token Identity & RBAC (`backend/auth.py`)**:
     - HMAC-SHA256 bearer tokens carrying `{sub, role: "customer"|"reviewer", exp}`.
     - `/chat`, `/chat/confirm`, `/chat/sync` require a valid token; `principal.user_id` is passed into `AdkEngine` and seeded into `session.state["user:id"]`.
     - Session ownership: each `thread_id` is bound to its creating `user_id`. Cross-customer access returns HTTP `403`.
     - Reviewer RBAC: `GET /claims/pending` and `POST /claims/{claim_id}/decision` require `role == "reviewer"` (`401` unauthenticated, `403` customer). Reviewer identity comes from the verified token, not the request body.
  4. **Order & Claim Ownership**:
     - Orders in `mock_api/stock_api.py` carry `customer_id`.
     - `claims` table stores `owner_id` (auto-migrated via `ALTER TABLE` if missing).
     - `open_claim(..., owner_id=...)` rejects orders belonging to another customer (`status="forbidden"`).
     - `status(claim_id, requester_id=...)` returns `status="forbidden"` when `requester_id != owner_id`.
     - `claims/tools.py` injects `user:id` from `tool_context.state` (never exposed as an LLM parameter).
  5. **ADR**: `docs/ADR-003-sessions-and-ownership.md` comparing `DatabaseSessionService` (Cloud SQL) vs `VertexAiSessionService` vs `SqliteSessionService`.

## 3. Interfaces & Contracts
- **Environment variables**: `SESSIONS=sqlite|memory|database`, `DATABASE_URL` (required when `SESSIONS=database`), `AUTH_SECRET` (HMAC signing key).
- **Authorization header**: `Authorization: Bearer <base64url(payload)>.<base64url(sig)>`.
- **Engine signatures**: `AdkEngine.stream(thread_id, message, user_id)` and `AdkEngine.resume_confirmation(thread_id, call_id, confirmed, user_id)`.
- **Error codes**: `401` missing/expired/tampered token; `403` wrong role or cross-customer `thread_id`; `{"status": "forbidden", ...}` on cross-customer order/claim tool calls.

## 4. Failure Modes & Edge Cases
- `SESSIONS=database` without `DATABASE_URL` → raises `ValueError` at initialization (fail fast).
- Missing, malformed, tampered, or expired token → HTTP `401` before reaching engine or DB.
- Customer token calling reviewer endpoints → HTTP `403`.
- Customer B sending Customer A's `thread_id` → HTTP `403` (never creates a colliding shadow session).
- Customer B calling `open_return_claim` on Customer A's order or `get_claim_status` on Customer A's claim → `status="forbidden"`, zero data leaked.
- Pre-existing SQLite `claims.db` without `owner_id` → automatically migrated on connect.

## 5. Verification & Acceptance Criteria
- [ ] `tests/test_sessions_and_auth.py` proves multi-instance continuity across two independent `AdkEngine` / `Runner` instances sharing `DatabaseSessionService`.
- [ ] Negative authorization tests cover `401`, `403` (role & session takeover), and cross-customer order/claim IDOR rejection.
- [ ] `docs/ADR-003-sessions-and-ownership.md` written and self-contained.
- [ ] `uv run scripts/verify.py` passes all checks offline with zero network egress.
- [ ] `docs/PROJECT_HANDOVER.md` and `docs/learning/W01-sessions-and-ownership.md` updated.

## 6. Rollback & Migration Plan
- `SESSIONS` defaults to `sqlite`; `owner_id` column migration is additive (`DEFAULT ''`). Reverting the commit restores prior state cleanly.
