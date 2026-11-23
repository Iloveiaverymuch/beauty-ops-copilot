# SPEC-009: Business-Card Cleanup and Docs-Consistency Gate (W0)

## 1. Problem Statement & Motivation
- **Context**: Public-facing documentation (`README.md`, `docs/`), evaluation fixtures (`evals/`, `scripts/eval_suite.py`), governance config (`copilot/governance.py`), and the offline verification entry point (`scripts/verify.py`).
- **Root Cause / Limitation**:
  1. Leftover corporate domains (`loreal.com`, `loreal-groupe.com`) and an entity-linked brand label (`L'Oréal Paris`) remain in 6 tracked files, contradicting repository neutrality.
  2. Several claims in documentation are unbacked or overstated: `-75%` input token savings from context caching in `docs/DEMO_15MIN.md` (whereas `evals/FINDINGS.md` measured `0` cached tokens on single-turn evals and `-40%` input tokens from tool-result compaction), `5.0/5.0` Vertex AI judge without sample size `n = 7` or source link in `README.md`, `Zero Hallucinations!` and `VERIFIED PRODUCTION READY` in `docs/demo/system_architecture.svg`, unlabelled demo-only capabilities, and a stale `106` test count in `docs/ADR-002.md`.
  3. `scripts/verify.py` does not check documentation consistency, allowing test-count drift, dead relative links, or forbidden terms to re-enter silently.
- **Evidence**:
  - `git grep -inE "loreal|l'oreal|l'oréal"` matched 6 files (`copilot/governance.py`, `copilot/data/products.json`, `copilot/tools/catalog.py`, `evals/build_evalset.py`, `evals/core/copilot_core.evalset.json`, `scripts/eval_suite.py`).
  - `docs/DEMO_15MIN.md:226` claimed `-75%` from `ContextCacheConfig`; `docs/demo/system_architecture.svg:68,283` contained `VERIFIED PRODUCTION READY` and `Zero Hallucinations!`.

## 2. Current Behavior vs. Proposed Behavior
- **Current Behavior**: `uv run scripts/verify.py` runs `pytest` and `evals.adk.run_adk_eval --offline` only; documentation drift, dead links, and leftover brand domains are not detected.
- **Proposed Behavior**:
  1. **Neutralize corporate/brand residue**:
     - `copilot/governance.py`: `DEFAULT_INTERNAL_DOMAINS = "retail-group.com,retail-ops.com,beauty-corp.com"`.
     - `evals/build_evalset.py`, `evals/core/copilot_core.evalset.json`, `scripts/eval_suite.py`: replace `@loreal.com` with `@retail-group.com`.
     - `copilot/data/products.json`, `copilot/tools/catalog.py`: replace `"L'Oréal Paris"` with `"Lumière Paris"`.
  2. **Honest, traceable documentation**:
     - `README.md`: replace `"enterprise-grade"` with reference-architecture/lab wording; link every measured number (`5.0/5.0` with `n = 7`, `4/4` exfiltration blocks with `n = 3` runs, `-40%` input tokens from tool-result compaction) to `evals/FINDINGS.md`; explicitly label demo-only components (mock stock/delivery API, local SQLite sessions, ticket-only `handoff_to_human`, simulated Workspace fixtures).
     - `docs/DEMO_15MIN.md`: replace the unmeasured `-75%` caching claim with the measured `-40%` compaction figure from `evals/FINDINGS.md` and state `n = 7` on the `5.0/5.0` judge result.
     - `docs/demo/system_architecture.svg`: replace `VERIFIED PRODUCTION READY` and `Zero Hallucinations!` with accurate labels.
     - `docs/ADR-002.md`: state `n = 7` and align test count wording.
     - `.gitignore`: add `output/`.
  3. **Automated docs-consistency gate**:
     - Add `scripts/check_docs.py` and execute it as Step 3 of `scripts/verify.py`.
     - Fails on: (a) test-count mismatch vs `pytest --collect-only -q` in `README.md` and `docs/PROJECT_HANDOVER.md` summary, (b) dead relative Markdown links in `README.md` and `docs/**/*.md`, (c) forbidden terms (old brand names, `zero hallucination`, `production-grade`, `production ready`) in tracked text files.

## 3. Interfaces & Contracts
- **CLI**: `uv run --no-sync python scripts/check_docs.py`
  - Exit code `0` when all 3 checks pass.
  - Exit code `1` with `path:line: reason` diagnostics on any violation.
- **Python Functions (`scripts/check_docs.py`)**:
  - `check_test_counts(root: Path, actual_count: int) -> list[str]`
  - `check_relative_links(root: Path) -> list[str]`
  - `check_forbidden_terms(root: Path, files: list[Path] | None = None) -> list[str]`
- **Verification Pipeline (`scripts/verify.py`)**:
  - Step 1: `pytest -q`
  - Step 2: `python -m evals.adk.run_adk_eval --offline`
  - Step 3: `python scripts/check_docs.py`
- **State Mutations**: None (pure read-only verification).

## 4. Failure Modes & Edge Cases
- **Self-matching**: `scripts/check_docs.py`, `tests/test_docs_consistency.py`, and `docs/specs/SPEC-009-business-card-cleanup-and-docs-consistency.md` reference the forbidden terms by design and are excluded from the forbidden-term scan.
- **Binary files**: `.pdf`, `.png`, `.jpg`, `.sqlite`, `.db`, `.pyc` are skipped to avoid decode errors.
- **Markdown link anchors & code blocks**: `#section` fragments are stripped before checking file existence; links inside fenced code blocks (` ``` `) are ignored; external URLs (`http://`, `https://`, `mailto:`, `file://`) are ignored.
- **Historical log entries**: `check_test_counts` validates current-state test counts in `README.md` and the top summary block of `docs/PROJECT_HANDOVER.md`, avoiding false positives on dated historical entries in `evals/FINDINGS.md`.

## 5. Verification & Acceptance Criteria
- [ ] `git grep -inE "loreal|l'oreal|l'oréal"` returns 0 matches across tracked text files (excluding `SPEC-009`, `check_docs.py`, `test_docs_consistency.py`).
- [ ] Every numeric claim in `README.md` links to `evals/FINDINGS.md` with sample size `n`.
- [ ] Regression tests in `tests/test_docs_consistency.py` verify pass/fail behavior for test-count mismatch, dead relative links, anchor stripping, fenced code blocks, and forbidden terms.
- [ ] `uv run scripts/verify.py` passes all 3 steps offline with exit code `0`.
- [ ] `docs/PROJECT_HANDOVER.md` updated and `docs/learning/W00-business-card-and-honest-metrics.md` written.

## 6. Rollback & Migration Plan
- Purely documentation, fixture neutralization, `.gitignore`, and an additive static check script. Reverting the commit restores prior state with zero runtime or schema impact.
