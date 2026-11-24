# W2 — Advisor Appointment Booking, Idempotency & Retry Policy

## Problem
Booking an in-store advisor appointment is a stateful write with real-world side effects. Without explicit confirmation, two-phase hold/confirm, idempotency keys, session-state identity injection, and a strict retry policy, an agent can book without asking, double-book on network timeouts, book for the wrong customer/store, or hammer a downstream API on `401`/`403` errors.

## Mental Model (One Line)
Never let an LLM execute an irreversible write silently: hold → confirm with HITL (`require_confirmation=True`) → commit with a stable `Idempotency-Key`, take identity/store from state, and retry transient errors only.

## How It Fails
- **No HITL**: The model misunderstands an exploratory question ("is 14:00 free?") and books an appointment unasked.
- **No `Idempotency-Key`**: A `ReadTimeout` happens after the server committed the booking; retrying creates a duplicate appointment for the same customer.
- **Identity in LLM arguments**: Prompt injection or hallucination passes another customer's ID or wrong store.
- **Blind retries**: Retrying `401` / `403` / `404` wastes latency and triggers auth lockout; retrying a non-idempotent POST creates duplicates.

## How It Is Measured
- `tests/test_appointments.py` verifies:
  1. Double call with same `(session_id, slot_id)` produces **1 booking**, `duplicate=True` on call 2, and **1** `appointment.confirmed` event.
  2. Real ADK `InMemoryRunner` + `FunctionTool(..., require_confirmation=True)`: refusing confirmation (`confirmed=False`) writes **0 bookings** and emits **0 events**; approving (`confirmed=True`) writes **1 booking**.
  3. `401` and `403` execute **1 attempt only** (never retried); `429`, `503`, and `ReadTimeout` retry up to `API_RETRIES + 1` and succeed when the transient fault clears.
  4. `customer_id`, `store`, and `contact` are absent from the LLM tool signature and injected strictly from `tool_context.state`.

## Where It Lives in the Code
- Appointment tools & retry helper: `advisor/appointments.py`
- Agent registration (`require_confirmation=True`): `beauty_advisor/agent.py`
- Two-phase mock API (`/appointments/slots`, `/hold`, `/confirm`): `mock_api/stock_api.py`
- Spec & `review-adk-tool` audit table: `docs/specs/SPEC-011-advisor-appointment-booking.md`
- Regression tests: `tests/test_appointments.py`
