"""Regression tests for scripts/check_docs.py (SPEC-009 / W0)."""
from __future__ import annotations

from pathlib import Path

from scripts.check_docs import (
    check_forbidden_terms,
    check_relative_links,
    check_test_counts,
)


def test_test_counts_pass_when_matching(tmp_path: Path):
    (tmp_path / "README.md").write_text("Runs 42 unit tests and 42 pytest tests.\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PROJECT_HANDOVER.md").write_text(
        "Verified: 42 pytest tests passing.\n", encoding="utf-8"
    )
    assert check_test_counts(tmp_path, actual_count=42) == []


def test_test_counts_fail_on_mismatch(tmp_path: Path):
    (tmp_path / "README.md").write_text("Includes 106 unit tests.\n", encoding="utf-8")
    errors = check_test_counts(tmp_path, actual_count=137)
    assert len(errors) == 1
    assert "106" in errors[0] and "137" in errors[0]


def test_relative_links_pass_with_anchors_and_ignore_code_blocks(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[Guide](docs/guide.md#intro)\n"
        "[External](https://example.com)\n"
        "[Anchor](#top)\n"
        "```markdown\n"
        "[Fake](docs/does_not_exist.md)\n"
        "```\n",
        encoding="utf-8",
    )
    assert check_relative_links(tmp_path) == []


def test_relative_links_fail_on_dead_target(tmp_path: Path):
    (tmp_path / "README.md").write_text("See [Missing](docs/missing.md) for details.\n", encoding="utf-8")
    errors = check_relative_links(tmp_path)
    assert len(errors) == 1
    assert "docs/missing.md" in errors[0]


def test_forbidden_terms_detect_old_brand_and_overclaims(tmp_path: Path):
    f1 = tmp_path / "doc.md"
    f1.write_text(
        "Contact user@loreal.com for a production-grade system with zero hallucinations.\n",
        encoding="utf-8",
    )
    errors = check_forbidden_terms(tmp_path, files=[f1])
    assert len(errors) == 3
    joined = "\n".join(errors)
    assert "old corporate brand" in joined
    assert "production-grade" in joined
    assert "zero hallucination" in joined


def test_forbidden_terms_pass_on_clean_files(tmp_path: Path):
    f1 = tmp_path / "clean.md"
    f1.write_text(
        "Contact ops@retail-group.com for the offline-verified reference architecture.\n",
        encoding="utf-8",
    )
    assert check_forbidden_terms(tmp_path, files=[f1]) == []
