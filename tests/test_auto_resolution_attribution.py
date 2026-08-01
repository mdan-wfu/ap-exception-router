"""The audit trail must distinguish a system placeholder from a clerk's
own HOLD. Two auto-paths both write a synthetic human_note today:

  - demo mode      → "demo fixture — <reason>"    (fixture resolution)
  - queue mode     → "queued for review — awaiting clerk decision"  (runner default)

Both must flag as auto_resolved so the dashboard never renders them as
a real judgment, but the source label must differ so a reader can tell
which auto-path was responsible."""
from __future__ import annotations

from src.ui.data import _auto_source_label, _is_auto_resolved


def test_demo_fixture_note_is_auto_resolved():
    assert _is_auto_resolved("demo fixture — duplicate pair; needs comparison")
    assert _auto_source_label("demo fixture — anything") == "demo fixture"


def test_queue_placeholder_note_is_auto_resolved():
    assert _is_auto_resolved("queued for review — awaiting clerk decision")
    assert _auto_source_label("queued for review — anything") == (
        "queue placeholder · awaiting clerk"
    )


def test_clerk_note_is_not_auto_resolved():
    # A real clerk's note — has neither prefix, so it's a genuine human decision.
    assert not _is_auto_resolved("Waiting on procurement to confirm rush order")
    assert not _is_auto_resolved("approved after verification with vendor")


def test_case_insensitive():
    # Notes have varied casing in prod (typo-tolerant); both prefixes recognized.
    assert _is_auto_resolved("Demo Fixture — WHATEVER")
    assert _is_auto_resolved("Queued for Review — awaiting")


def test_empty_and_none_are_not_auto():
    assert not _is_auto_resolved(None)
    assert not _is_auto_resolved("")
    assert not _is_auto_resolved("   ")
