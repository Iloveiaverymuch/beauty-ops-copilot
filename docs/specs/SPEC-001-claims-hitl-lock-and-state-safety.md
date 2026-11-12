# SPEC-001: Claims HITL Lock Lifecycle, Input Validation, and Error Resilience

## 1. Problem Statement & Motivation
- **Context**: The `/chat/confirm` endpoint (`backend/app.py:219`) handles human reviewer approvals for sensitive claims arbitration and refund execution.
- **Root Cause / Limitation**:
  1. `ConfirmRequest` accepted an unconstrained `call_id: str` without minimum length, maximum length, or format validation, permitting empty strings or malformed payloads to reach the underlying ADK engine.
  2. In the event of an unhandled exception or engine failure during confirmation, the endpoint yielded user-facing error text but did not guarantee that KPI records and telemetry properly flagged `failed: True` with explicit structured error details.
  3. Session lock acquisition lacked defensive cleanup in edge cases where thread initialization could race.
- **Evidence**:
  - `backend/app.py:82-87`: `class ConfirmRequest(BaseModel): thread_id: str = ...; call_id: str; confirmed: bool`.
  - Contrast with `ChatRequest` which enforces `min_length=1, max_length=2000`.

## 2. Current Behavior vs. Proposed Behavior
- **Current Behavior**:
  - Request with `call_id=""` or oversized garbage is accepted by FastAPI/Pydantic validation.
  - When the engine raises an error during `resume_confirmation`, the frontend receives fallback French text, but no explicit `error` event is emitted if the stream abruptly terminates.
- **Proposed Behavior**:
  - Request with empty or blank `call_id` is rejected at the HTTP edge with HTTP 422 Unprocessable Entity (`min_length=1, max_length=128`).
  - `chat_confirm` wraps the resume stream in a guaranteed structured error emission (`event: error`), marks KPI records with `failed: True`, and ensures session lock release.

## 3. Interfaces & Contracts
- **Input Schema (`ConfirmRequest`)**:
  ```python
  class ConfirmRequest(BaseModel):
      thread_id: str = Field(pattern=r"^[a-f0-9]{32}$")
      call_id: str = Field(min_length=1, max_length=128)
      confirmed: bool
      engine: str = "copilot"
  ```
- **Output / Event Stream**:
  - Happy path: `meta` $\rightarrow$ `progress` $\rightarrow$ `claim_reviewed` / `text` $\rightarrow$ `done` (`failed: false`).
  - Error path: `meta` $\rightarrow$ `error` $\rightarrow$ `done` (`failed: true`).

## 4. Failure Modes & Edge Cases
- Invalid engine name: Returns SSE error immediately (`engine {name} not available`).
- Engine crash during resumption: Logs stack trace, yields structured SSE error event, marks KPI `failed: True`, sets span error attributes.
- Missing or empty `call_id`: Rejected at FastAPI boundary with HTTP 422.

## 5. Verification & Acceptance Criteria
- [x] Pydantic validation unit test verifying `call_id=""` raises `ValidationError`.
- [x] End-to-end endpoint test verifying HTTP 422 on empty `call_id`.
- [x] Endpoint test verifying engine error in `/chat/confirm` emits fallback text, KPI failure status, and clean completion.
- [x] Full offline suite passes (`uv run pytest -q && uv run python -m evals.adk.run_adk_eval --offline`).

## 6. Rollback & Migration Plan
- Reverting `ConfirmRequest.call_id` validation in `backend/app.py` restores previous lax validation without database schema changes.

