#!/usr/bin/env python3
"""Offline documentation and business-card consistency gate for Beauty Ops Copilot.

Checks:
1. Test-count consistency: current-state test counts in README.md and docs/PROJECT_HANDOVER.md
   must match the actual test count collected by pytest.
2. Dead relative links: every relative Markdown link in README.md and docs/**/*.md must resolve
   to an existing file in the repository.
3. Forbidden terms: tracked text files must not contain leftover corporate brand terms or
   overstated claims ("zero hallucination", "production-grade", "production ready").

Returns exit code 0 on pass, 1 on any violation. Zero network, zero cloud calls.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files excluded from the forbidden-term scan because they define or document the rules.
FORBIDDEN_SCAN_EXCLUDES = {
    "scripts/check_docs.py",
    "tests/test_docs_consistency.py",
    "docs/specs/SPEC-009-business-card-cleanup-and-docs-consistency.md",
}

BINARY_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
    ".db", ".sqlite", ".pyc", ".whl", ".zip", ".gz", ".tar",
}

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("old corporate brand", re.compile(r"\bloreal\b|l['’]or[eé]al", re.IGNORECASE)),
    ("overclaim (zero hallucination)", re.compile(r"zero\s+hallucinations?|z[eé]ro\s+hallucinations?", re.IGNORECASE)),
    ("overclaim (production-grade)", re.compile(r"production-grade", re.IGNORECASE)),
    ("overclaim (production ready)", re.compile(r"production\s+ready", re.IGNORECASE)),
]

TEST_COUNT_RE = re.compile(
    r"\b(\d+)\s+(?:unit\s+(?:&\s+integration\s+)?tests|comprehensive\s+tests|pytest\s+tests|tests\s+passing)\b",
    re.IGNORECASE,
)

MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def collect_pytest_count(root: Path) -> int:
    """Collect the number of pytest tests offline without executing them."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout + "\n" + proc.stderr
    m = re.search(r"(\d+)\s+tests?\s+collected", output)
    if not m:
        raise RuntimeError(f"Could not parse pytest collected test count:\n{output}")
    return int(m.group(1))


def check_test_counts(root: Path, actual_count: int) -> list[str]:
    """Verify current-state test counts in README.md and the top of docs/PROJECT_HANDOVER.md."""
    errors: list[str] = []
    targets: list[tuple[Path, int | None]] = [
        (root / "README.md", None),
        (root / "docs" / "PROJECT_HANDOVER.md", 40),  # Top verified-state summary only
    ]
    for path, max_lines in targets:
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        subset = lines[:max_lines] if max_lines is not None else lines
        for lineno, line in enumerate(subset, start=1):
            for match in TEST_COUNT_RE.finditer(line):
                claimed = int(match.group(1))
                if claimed != actual_count:
                    rel = path.relative_to(root)
                    errors.append(
                        f"{rel}:{lineno}: test count mismatch (doc claims {claimed}, pytest collects {actual_count})"
                    )
    return errors


def _strip_fenced_code(lines: list[str]) -> list[tuple[int, str]]:
    """Return (1-based lineno, line) outside fenced ``` code blocks."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for idx, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append((idx, line))
    return out


def check_relative_links(root: Path) -> list[str]:
    """Verify that every relative Markdown link in README.md and docs/**/*.md resolves."""
    errors: list[str] = []
    md_files: list[Path] = []
    readme = root / "README.md"
    if readme.exists():
        md_files.append(readme)
    docs_dir = root / "docs"
    if docs_dir.exists():
        md_files.extend(sorted(docs_dir.rglob("*.md")))

    for md in md_files:
        lines = md.read_text(encoding="utf-8").splitlines()
        for lineno, line in _strip_fenced_code(lines):
            for match in MD_LINK_RE.finditer(line):
                raw_target = match.group(1).strip().split()[0]
                if (
                    not raw_target
                    or raw_target.startswith(("http://", "https://", "mailto:", "#", "file://"))
                ):
                    continue
                target_path_str = raw_target.split("#", 1)[0].split("?", 1)[0]
                if not target_path_str:
                    continue
                resolved = (md.parent / target_path_str).resolve()
                if not resolved.exists():
                    rel = md.relative_to(root)
                    errors.append(f"{rel}:{lineno}: dead relative link '{raw_target}'")
    return errors


def _tracked_files(root: Path) -> list[Path]:
    """Return tracked files via git ls-files, falling back to rglob when not in a git repo."""
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return [root / p for p in proc.stdout.splitlines() if p.strip() and (root / p).is_file()]
    return [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]


def check_forbidden_terms(root: Path, files: list[Path] | None = None) -> list[str]:
    """Scan text files for leftover brand names or overstated claims."""
    errors: list[str] = []
    scan_files = files if files is not None else _tracked_files(root)
    for path in scan_files:
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        if rel in FORBIDDEN_SCAN_EXCLUDES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for label, pattern in FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    errors.append(f"{rel}:{lineno}: forbidden term [{label}] -> {line.strip()[:100]}")
    return errors


def main() -> int:
    actual_count = collect_pytest_count(ROOT)
    errors: list[str] = []
    errors.extend(check_test_counts(ROOT, actual_count))
    errors.extend(check_relative_links(ROOT))
    errors.extend(check_forbidden_terms(ROOT))

    if errors:
        print(f"Documentation consistency check FAILED ({len(errors)} issue(s)):")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(
        f"Documentation consistency check PASSED "
        f"(pytest={actual_count} tests, 0 dead links, 0 forbidden terms)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
