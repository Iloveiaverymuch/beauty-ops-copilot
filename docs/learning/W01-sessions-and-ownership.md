# W1 — Persistent Sessions, Multi-Instance Continuity & Ownership

## Problem
An agent running on horizontally scaled Cloud Run instances cannot keep sessions in RAM (`InMemorySessionService`) or local disk (`SqliteSessionService`), because turn 2 can land on a different replica than turn 1. Separately, hardcoding `user_id="web"` and trusting client-supplied IDs allows cross-customer session takeover and unauthorized claim approvals (IDOR).

## Mental Model (One Line)
Externalize session state to a shared database (`DatabaseSessionService`), authenticate the caller at the API boundary, and check resource ownership in backend code—never trust the LLM or a storage prefix for authorization.

## How It Fails
- **In-memory / local SQLite on Cloud Run**: Turn 2 hits Instance B → session not found, conversation resets, or pending HITL confirmations vanish.
- **Hardcoded `user_id="web"`**: Every customer shares the same `user:` state namespace; guessing or reusing a `thread_id` exposes another customer's conversation.
- **Confusing `user:` scope with access control**: `user:id` in ADK state stops the *LLM* from fabricating an ID, but does nothing if the HTTP endpoint never verifies who the caller is or whether they own the requested `thread_id`, `order_id`, or `claim_id`.
- **Unauthenticated reviewer endpoints**: Anyone can `POST /claims/{id}/decision` with `{"reviewer": "admin"}` and trigger a refund.

## How It Is Measured
- **Multi-instance test** (`tests/test_sessions_and_auth.py`): Instance A (`AdkEngine` #1) starts a session and writes events + `user:id` via `DatabaseSessionService`; Instance B (`AdkEngine` #2) resumes the exact same `session_id` and observes Instance A's history and state.
- **Negative authorization tests**:
  - Missing / expired / tampered token → HTTP `401`.
  - Customer token on `/claims/pending` or `/claims/{id}/decision` → HTTP `403`.
  - Customer B sending Customer A's `thread_id` → HTTP `403`.
  - Customer B calling `open_return_claim` on Customer A's order or `get_claim_status` on Customer A's claim → `status == "forbidden"`.

## Where It Lives in the Code
- Auth & ownership binding: `backend/auth.py`
- Session service wiring: `backend/engines.py`
- Protected API routes: `backend/app.py`
- Claim & order ownership: `advisor/claims.py`, `claims/tools.py`, `mock_api/stock_api.py`
- Local Postgres compose: `docker-compose.yml`
- Architecture decision: `docs/ADR-003-sessions-and-ownership.md`
- Spec & tests: `docs/specs/SPEC-010-persistent-sessions-multi-instance-and-ownership.md`, `tests/test_sessions_and_auth.py`
