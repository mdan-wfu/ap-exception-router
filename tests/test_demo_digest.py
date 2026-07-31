"""Enforceable regression check: the canonical projection of the audit
store after `make demo` must match the committed baseline.

Locks per-invoice semantics — outcome, findings, cost (2dp), node
sequence, scribe conclusion, settlement — WITHOUT locking CLI
presentation. Any deliberate semantic change must update
docs/demo-digest.txt and be recorded in DECISIONS.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
BASELINE_FILE = REPO / "docs" / "demo-digest.txt"


def test_baseline_file_exists_and_looks_like_an_md5():
    assert BASELINE_FILE.exists(), (
        "docs/demo-digest.txt must exist as the committed baseline; "
        "regenerate with `make demo-digest`"
    )
    baseline = BASELINE_FILE.read_text().strip()
    assert len(baseline) == 32 and all(c in "0123456789abcdef" for c in baseline), (
        f"baseline must be a 32-char lowercase hex md5, got {baseline!r}"
    )


def test_current_digest_matches_baseline():
    """The current audit store (already populated by whatever the last
    `make demo` produced — or, in CI, populated fresh) must digest to
    the same md5 as the committed baseline. If this test fails, either:
      (a) a run bug landed — revert and investigate, or
      (b) the semantic change was deliberate — update the baseline
          and add a DECISIONS entry naming what changed and why."""
    from src.config import AUDIT_DB_PATH
    if not Path(AUDIT_DB_PATH).exists():
        pytest.skip("no audit store yet — run `make demo` first")

    from scripts.demo_digest import _project
    _, actual = _project()
    expected = BASELINE_FILE.read_text().strip()
    assert actual == expected, (
        f"demo-digest mismatch\n"
        f"  expected: {expected}  (docs/demo-digest.txt)\n"
        f"  actual:   {actual}\n"
        f"If deliberate: update docs/demo-digest.txt via `make demo-digest`, "
        f"add a DECISIONS entry."
    )


def test_two_consecutive_projections_produce_identical_digest():
    """Determinism sanity — running the projection twice against the
    same audit store must give the same md5. Sorting must be stable."""
    from src.config import AUDIT_DB_PATH
    if not Path(AUDIT_DB_PATH).exists():
        pytest.skip("no audit store yet — run `make demo` first")

    from scripts.demo_digest import _project
    _, first = _project()
    _, second = _project()
    assert first == second, (
        f"projection is non-deterministic — sort keys may be unstable "
        f"({first} vs {second})"
    )
