---
name: handover
description: Generates or updates project handover documentation and summarizes verified repository state.
---

# Handover Skill

Use this skill when preparing or updating `docs/PROJECT_HANDOVER.md` after completing technical milestones or preparing project transfer.

## Handover Structure

1. **Verification Status**:
   - Date and commit hash of verification.
   - Exact test and evaluation counts (e.g., 129 tests passed, 7/7 ADK offline evals passed).
   - Execution command used for verification (`uv run scripts/verify.py`).

2. **Categorized Repository State**:
   - **Implemented & Verified**: Fully tested features with automated test coverage.
   - **Partially Implemented / Mocks**: Features backed by deterministic mocks or fixtures (e.g. Workspace MCP, retail stock mock).
   - **Planned / Next Milestones**: Documented backlog items and production hardening tasks.

3. **Active Specifications**:
   - Reference all completed or in-progress specs in `docs/specs/`.

4. **Environment & Safe Runbook**:
   - Step-by-step instructions for running locally using mock/offline modes.
   - Explicit instructions on secret management and GCP configuration without exposing sensitive tokens.

