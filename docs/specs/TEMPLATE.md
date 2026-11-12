# SPEC-XXX: [Short Title]

## 1. Problem Statement & Motivation
- **Context**: What component or user journey is affected?
- **Root Cause / Limitation**: What specific defect, vulnerability, or architectural bottleneck exists?
- **Evidence**: Provide log traces, test failures, or code references demonstrating the issue.

## 2. Current Behavior vs. Proposed Behavior
- **Current Behavior**: Step-by-step description of how the system currently responds.
- **Proposed Behavior**: Exact desired behavior, contracts, and error responses.

## 3. Interfaces & Contracts
- **Input Schema / Signatures**: Exact parameter types, validators, and defaults.
- **Output / Event Schema**: SSE events, return payloads, or exceptions raised.
- **State Mutations**: Any changes to `session.state`, `EventActions`, or scoped prefixes (`user:id`, `temp:`).

## 4. Failure Modes & Edge Cases
- Invalid / malformed inputs (empty strings, malicious types, oversized payloads).
- Concurrency & race conditions (timeouts, lock acquisition, deadlocks).
- Downstream / engine failures (service unavailable, unhandled exceptions).

## 5. Verification & Acceptance Criteria
- [ ] Fast offline unit/regression test added under `tests/`.
- [ ] No regression across existing test suite (`uv run pytest -q`).
- [ ] Offline ADK behavioral evaluation passes (`uv run python -m evals.adk.run_adk_eval --offline`).
- [ ] Zero paid cloud API calls or network egress during test execution.

## 6. Rollback & Migration Plan
- How to safely revert the change if an unforeseen regression is detected.

