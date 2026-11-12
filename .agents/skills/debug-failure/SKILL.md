---
name: debug-failure
description: Diagnoses and isolates test failures, eval regressions, and runtime exceptions.
---

# Debug Failure Skill

Use this skill when a pytest test, offline ADK evaluation case, or runtime endpoint encounters a failure.

## Diagnostic Protocol

1. **Reproduce in Isolation**:
   - For pytest failures: Run the specific failing test with verbose output:
     ```bash
     uv run pytest -vv -k "<test_function_name>"
     ```
   - For ADK evaluation failures: Run the evaluator in detailed mode:
     ```bash
     uv run python -m evals.adk.run_adk_eval --offline --detailed
     ```

2. **Categorize the Failure**:
   - **Contract Regression**: Schema mismatch, missing field, or unexpected SSE event name.
   - **Concurrency/Locking**: Deadlock or unreleased `asyncio.Lock` across conversation turns.
   - **State Pollution / IDOR**: Leaked session state across invocations (check scoped state prefixes `user:id`, `temp:`).
   - **Tool Execution**: Missing mock, unhandled network exception, or timeout in external integration.
   - **Evaluator Assertion**: Deterministic canary, prompt leakage, citation grounding, or refund guard failure.

3. **Inspect the Call Stack**:
   - Follow the stack trace to the exact line of execution.
   - Check input parameters, mocks, and fixture setup.

4. **Formulate Minimal Fix**:
   - Fix the defect at the root cause.
   - Never weaken evaluation assertions or silence test errors just to pass.

5. **Verify Resolution**:
   - Re-run the isolated test, followed by `uv run scripts/verify.py`.

