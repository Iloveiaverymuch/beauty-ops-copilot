---
name: verify-change
description: Runs the canonical offline verification suite and confirms repository invariants.
---

# Verify Change Skill

Use this skill to verify code changes before submitting work for review, updating documentation, or requesting user feedback.

## Verification Checklist

1. **Working Tree Cleanliness**:
   - Verify modified files:
     ```bash
     git status -s
     ```
   - Ensure no unintended files, caches, or temporary artifacts were created.

2. **Run Canonical Verification Script**:
   - Execute the single verification entry point:
     ```bash
     uv run scripts/verify.py
     ```
   - Verification suite requirements:
     - Runtime duration: < 10 seconds.
     - Network egress: 0 bytes (zero cloud calls, zero Vertex AI charges).
     - Exit status: 0 on complete pass, non-zero on any failure.

3. **Verify Checks Executed**:
   - **Pytest**: All unit, integration, and mock tests pass.
   - **ADK Offline Evaluation**: 7/7 golden cases pass custom evaluators.
   - **Type & Schema Validation**: Pydantic models reject invalid inputs.

4. **Review Deprecation Warnings**:
   - Inspect captured warnings to ensure no critical backward-incompatible changes are triggered unexpectedly.

