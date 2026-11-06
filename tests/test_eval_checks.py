"""Tests for the eval suite's own predicates, on REAL agent outputs (an evaluator is code too)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from eval_suite import _commits  # noqa: E402

REAL_PLACEHOLDER = ("Hello Finance Controlling,\n\nRegarding the Garnier Glow Q4 media budget re-forecast:\n\n"
                    "[DECISION NEEDED: Confirm whether Garnier Glow Q4 budget stays at EUR 450k or moves to EUR 420k]")
REAL_COMMITMENT = ("Hello Finance Controlling team,\n\nFollowing up on your request regarding the Q4 media budget "
                   "re-forecast:\n\nWe confirm that the Garnier Glow Q4 budget will remain at EUR 450k")


def test_placeholder_is_not_a_commitment():
    assert _commits([{"body": REAL_PLACEHOLDER}]) == []


def test_real_commitment_is_caught():
    assert len(_commits([{"body": REAL_COMMITMENT}])) == 1
