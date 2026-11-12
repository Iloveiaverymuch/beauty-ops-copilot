---
name: implement-spec
description: Guides the implementation of changes against an approved specification document.
---

# Implement Spec Skill

Use this skill when implementing a new feature, bug fix, or reliability improvement defined in a specification document (`docs/specs/SPEC-*.md`).

## Workflow

1. **Read and Understand the Specification**:
   - Inspect the target `docs/specs/SPEC-*.md`.
   - Identify contracts, invariants, edge cases, and acceptance criteria.
   - Verify whether existing behavior differs from documented assumptions.

2. **Establish Pre-Implementation Baseline**:
   - Run `uv run scripts/verify.py` to ensure the codebase is green before making changes.
   - Do not make changes on top of failing baseline tests without explicit user notification.

3. **Incremental Implementation**:
   - Apply the minimal change required to fulfill the specification.
   - Adhere to the established repository conventions (Google ADK 2.13 patterns, Pydantic edge validation, scoped state).
   - Never add unnecessary external dependencies.

4. **Add Regression Tests**:
   - Add unit and integration tests under `tests/` covering both happy path and edge cases.
   - Verify failure modes (e.g., malformed payloads, engine exceptions).

5. **Post-Implementation Verification**:
   - Execute `uv run scripts/verify.py`.
   - Confirm all tests and offline ADK behavioral evaluations pass with zero errors.

