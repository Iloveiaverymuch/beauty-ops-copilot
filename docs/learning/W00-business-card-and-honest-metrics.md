# W0 — Business-Card Cleanup & Honest Metrics

## Problem
Engineering repositories used as technical references or onboarding kits silently accumulate drift: stale test counts, dead relative links, leftover client/brand domains, and unmeasured marketing claims (e.g., citing theoretical `-75%` caching savings when only `-40%` compaction was measured, or reporting `5.0/5.0` without sample size `n`).

## Mental Model (One Line)
Treat documentation claims as testable invariants: every metric needs a source file and sample size `n`, and CI must enforce doc hygiene automatically.

## How It Fails
- **Credibility erosion**: A Tech Lead reading `5.0/5.0` without `n = 7` or seeing unqualified perfection claims immediately distrusts the rest of the engineering.
- **Silent drift**: Test counts and relative links rot the moment tests are added or files are renamed, unless checked in `verify.py`.
- **Confidentiality / brand leakage**: Hardcoded corporate domains in allowlists or eval fixtures survive refactors unless guarded by a static scan.

## How It Is Measured
- `scripts/check_docs.py` runs offline inside `uv run scripts/verify.py`:
  1. Compares current-state test counts in `README.md` and `docs/PROJECT_HANDOVER.md` against `pytest --collect-only -q`.
  2. Verifies every relative Markdown link in `README.md` and `docs/**/*.md` resolves to an existing file.
  3. Scans tracked text files for forbidden brand terms and overclaim phrases.
- Regression tests in `tests/test_docs_consistency.py` prove each failure mode triggers an error.

## Where It Lives in the Code
- Gate script: `scripts/check_docs.py`
- Verification pipeline: `scripts/verify.py`
- Regression tests: `tests/test_docs_consistency.py`
- Spec: `docs/specs/SPEC-009-business-card-cleanup-and-docs-consistency.md`
- Measured evidence log: `evals/FINDINGS.md`
