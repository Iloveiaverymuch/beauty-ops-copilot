#!/usr/bin/env python3
"""Canonical offline verification script for Beauty Ops Copilot.

Runs all offline regression suites in <10 seconds without cloud calls or network egress:
1. Python unit, integration, and mock test suite (pytest).
2. ADK native offline behavioral evaluation (7 golden cases).

Returns exit code 0 if all checks pass; non-zero if any check fails.
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_step(name: str, cmd: list[str]) -> bool:
    print(f"\n{'=' * 65}")
    print(f"▶ Running: {name}")
    print(f"  Command: {' '.join(cmd)}")
    print(f"{'=' * 65}")
    t0 = time.perf_counter()
    res = subprocess.run(cmd, cwd=ROOT)
    elapsed = time.perf_counter() - t0
    if res.returncode == 0:
        print(f"✔ {name} PASSED in {elapsed:.2f}s")
        return True
    else:
        print(f"✖ {name} FAILED with exit code {res.returncode} in {elapsed:.2f}s")
        return False


def main() -> int:
    total_t0 = time.perf_counter()
    print("Beauty Ops Copilot — Fast Offline Verification")
    print("Mode: OFFLINE (Mock fixtures, Zero cloud API calls, Zero network egress)")

    steps = [
        ("Python Regression & Evaluator Tests", ["uv", "run", "--no-sync", "pytest", "-q"]),
        ("ADK Offline Behavioral Evaluation", [
            "uv", "run", "--no-sync", "python", "-m", "evals.adk.run_adk_eval", "--offline"
        ]),
    ]

    for name, cmd in steps:
        if not run_step(name, cmd):
            print(f"\n❌ Verification suite failed. Aborting.")
            return 1

    total_elapsed = time.perf_counter() - total_t0
    print(f"\n{'=' * 65}")
    print(f"✅ ALL VERIFICATION CHECKS PASSED in {total_elapsed:.2f}s (Budget: <10.0s)")
    print(f"{'=' * 65}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

