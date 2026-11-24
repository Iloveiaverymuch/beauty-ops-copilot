# SPEC-011: In-Store Advisor Appointment Booking (W2)

## 1. Problem Statement & Motivation
- **Context**: Customer-facing beauty advisor (`beauty_advisor/agent.py`), new appointment tool module (`advisor/appointments.py`), and retail mock API (`mock_api/stock_api.py`).
- **Root Cause / Limitation**:
  - Léa currently has no write-safe tool to book an in-store consultation with a beauty advisor.
  - Naive write tools risk unconfirmed bookings, double-booking on retries, IDOR if `customer_id`/`store`/`contact` are passed as LLM arguments, and blindly retrying non-transient `4xx` errors (`401`, `403`, `404`, `409`).
- **Evidence**: Mirror of the target retail assistant's advisor-booking flow; closes §2.3 gaps on idempotent confirmed writes and transient-only retry policy.

## 2. Current Behavior vs. Proposed Behavior
- **Current Behavior**: No appointment endpoints or booking tools exist.
- **Proposed Behavior**:
  1. **Mock Retail API (`mock_api/stock_api.py`)**:
     - `GET /appointments/slots?store=<store>&date=<YYYY-MM-DD>`: returns open 30-min slots (excluding held or confirmed slots).
     - `POST /appointments/hold`: creates a temporary slot hold (`hold_id`, `expires_at`) for `(store, slot_id, customer_id)`. Returns `409` if already held by another customer or confirmed; idempotent if the same customer already holds that slot.
     - `POST /appointments/confirm` (header `Idempotency-Key` required): converts the hold into a confirmed booking (`appointment_id`, `status="confirmed"`, `duplicate=False`). Re-sending the same `Idempotency-Key` returns the original booking with `duplicate=True` (guaranteed no double booking).
  2. **ADK Tools (`advisor/appointments.py`, wired in `beauty_advisor/agent.py`)**:
     - `list_appointment_slots(date: str, tool_context: ToolContext)`: reads `store` from `tool_context.state["user:store"]` and lists available slots.
     - `book_advisor_appointment(slot_id: str, topic: str, tool_context: ToolContext)` wrapped with **`FunctionTool(func=book_advisor_appointment, require_confirmation=True)`**:
       - Reads `user:id`, `user:store`, `user:contact` exclusively from `tool_context.state` (never LLM arguments).
       - Pauses for explicit user confirmation via ADK native HITL. Refusal executes zero writes and emits zero events.
       - Executes `hold_slot` → `confirm_slot` with deterministic `Idempotency-Key = sha256(f"{session_id}|{slot_id}")[:24]`.
       - Emits domain event `appointment.confirmed` on `claims.bus.bus()` (`appointments-events` topic) on first non-duplicate booking.
  3. **Transient-Only Retry Policy (`_request_with_retry`)**:
     - Retries **only** `httpx.TimeoutException`, `httpx.ConnectError`, and HTTP `429`, `502`, `503`, `504` with bounded exponential backoff (`API_RETRIES = 2`).
     - **Never** retries `400`, `401`, `403`, `404`, `409`, `422`.

## 3. Interfaces & Contracts
- **LLM-Visible Tool Signatures**:
  - `list_appointment_slots(date: str) -> dict`
  - `book_advisor_appointment(slot_id: str, topic: str = "consultation beauté") -> dict` (`require_confirmation=True`)
- **State Reads / Writes**:
  - Reads: `user:id`, `user:store`, `user:contact`.
  - Writes: `temp:last_hold_id`, `temp:last_appointment_id`.
- **Domain Event (`appointments-events`)**:
  - `{"type": "appointment.confirmed", "appointment_id": str, "slot_id": str, "store": str, "customer_id": str, "topic": str, "idempotency_key": str}`

## 4. `review-adk-tool` Audit Table

| Invariant (`.agents/skills/review-adk-tool`) | Status | Implementation Evidence |
|---|---|---|
| Deterministic signatures & explicit types | PASS | `slot_id: str`, `topic: str`, `date: str`; validated against strict regex / length bounds before HTTP calls |
| Scoped state & anti-IDOR (`user:` / `temp:`) | PASS | `user:id`, `user:store`, `user:contact` read strictly from `tool_context.state`; never exposed as LLM params; ephemeral refs stored in `temp:` |
| Graceful degradation & retry policy | PASS | Bounded timeout + exponential backoff on `Timeout`/`ConnectError`/`429`/`502`/`503`/`504` only; `401`/`403`/`404`/`409` fail fast on attempt 1; structured dict returned (never raises) |
| HITL & idempotency for writes | PASS | Wrapped in `FunctionTool(..., require_confirmation=True)`; `confirm_slot` sends stable `Idempotency-Key = sha256(session_id|slot_id)[:24]` |
| Pure Python & zero leaks | PASS | Returns only `appointment_id`, `slot_id`, `store`, `topic`, `status`, `duplicate`; never echoes contact PII or internal headers |

## 5. Failure Modes & Edge Cases
- Duplicate call with same `session_id` + `slot_id` → 1 booking in API, `duplicate=True` on second call, 1 `appointment.confirmed` event.
- HITL confirmation refused (`confirmed=False`) → 0 bookings written, 0 events emitted.
- `401` / `403` / `404` / `409` → 1 HTTP attempt only (not retried), structured error returned.
- `429` / `503` / `ReadTimeout` → retried up to `API_RETRIES + 1` times; recovers if transient fault clears.
- Missing `user:id` / `user:store` / `user:contact` → returns `status="error", error_code="missing_user_context"` before any HTTP call.

## 6. Verification & Acceptance Criteria
- [ ] `tests/test_appointments.py` verifies double-call idempotency, ADK Runner HITL refuse vs approve, retry policy (`401`/`403` not retried; `429`/`503`/`ReadTimeout` retried), state-only identity injection, and `appointment.confirmed` emission.
- [ ] `uv run scripts/verify.py` passes (all pytest + 7/7 offline ADK evals + `scripts/check_docs.py`).
- [ ] `docs/PROJECT_HANDOVER.md` and `docs/learning/W02-appointment-booking-and-retries.md` updated.

## 7. Rollback & Migration Plan
- Additive module (`advisor/appointments.py`), additive mock API routes, and 2 tools registered on `beauty_advisor/agent.py`. Reverting restores prior state with zero breaking changes.
