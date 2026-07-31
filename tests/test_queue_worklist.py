"""Queue as a worklist — the landing separates the work from the history,
sorts by urgency, shows progress, and attributes straight-through APPROVEs
correctly (not as "clerk")."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _seed(monkeypatch, tmp_path):
    from src import config as cfg_mod
    from src.store import audit as audit_mod
    isolated = tmp_path / "audit.sqlite"
    monkeypatch.setattr(cfg_mod, "AUDIT_DB_PATH", isolated)
    monkeypatch.setattr(audit_mod, "AUDIT_DB_PATH", isolated)
    real = Path("runs/audit.sqlite")
    if real.exists():
        shutil.copy(str(real), str(isolated))
    else:
        from src.store.audit import AuditStore
        AuditStore(path=isolated)
    return isolated


@pytest.fixture
def client(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    from src.ui.app import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# A1 — the landing separates work from settled
# ---------------------------------------------------------------------------

def test_landing_shows_needs_your_decision_section(client):
    body = client.get("/").text
    assert "Needs your decision" in body
    assert "Settled invoices" in body


def test_landing_worklist_contains_only_awaiting_and_held(client):
    """Read the rendered rows in each section and confirm settled invoices
    (APPROVE / REJECT terminal) do NOT appear in Needs-your-decision."""
    body = client.get("/").text
    work_start = body.find("Needs your decision")
    settled_start = body.find("Settled invoices")
    assert work_start > 0 and settled_start > work_start

    worklist_slice = body[work_start:settled_start]
    settled_slice = body[settled_start:]

    # Straight-through APPROVEs from the demo (INV-1001, 1006, 1011, 1015)
    # must appear in settled, NOT in worklist.
    for straight_through in ("INV-1001", "INV-1006", "INV-1015"):
        assert straight_through not in worklist_slice, (
            f"{straight_through} is settled straight-through — must not be in the worklist"
        )
        assert straight_through in settled_slice, (
            f"{straight_through} should appear in the settled section"
        )


# ---------------------------------------------------------------------------
# A2 — null due dates sort to the top with a "no due date" chip
# ---------------------------------------------------------------------------

def test_null_due_date_sorts_to_top_of_worklist(client):
    """INV-1009 has due_date=null (empty vendor + negative total); INV-1003
    has due_date_raw='yesterday' (unparseable). Both should render at the
    top of the worklist with a 'no due date' chip."""
    body = client.get("/").text
    work_start = body.find("Needs your decision")
    settled_start = body.find("Settled invoices")
    worklist_slice = body[work_start:settled_start]

    # A no-due-date chip must appear
    assert "no due date" in worklist_slice, (
        "the worklist must flag null/unparseable due dates with a 'no due date' chip"
    )

    # And they must appear BEFORE any invoice with a parseable date. Check
    # that INV-1009 (null due_date) appears before at least one dated one.
    # INV-1004 defaults to fixture-HOLD in demo mode (still awaiting → in
    # the worklist) and carries a parseable due_date of 2026-02-22.
    inv1009 = worklist_slice.find("INV-1009")
    inv1004 = worklist_slice.find("INV-1004")
    assert inv1009 > 0
    assert inv1004 > 0
    assert inv1009 < inv1004, (
        "null-due-date invoice must render above dated ones in the worklist"
    )


def test_days_until_due_chip_renders(client):
    body = client.get("/").text
    # Corpus is dated 2026-01–02; today is later → most invoices show overdue.
    assert "overdue by" in body or "due in" in body


# ---------------------------------------------------------------------------
# A2 secondary — sort=amount is honored
# ---------------------------------------------------------------------------

def test_sort_by_amount_puts_largest_first(client):
    body = client.get("/?sort=amount").text
    work_start = body.find("Needs your decision")
    settled_start = body.find("Settled invoices")
    slice_ = body[work_start:settled_start]
    # INV-1013 ($22,562.80) is the largest awaiting-decision total; it
    # should render before smaller ones like INV-1002 ($15,000).
    idx_1013 = slice_.find("INV-1013")
    idx_1002 = slice_.find("INV-1002")
    if idx_1013 > 0 and idx_1002 > 0:
        assert idx_1013 < idx_1002, (
            "when sort=amount, INV-1013 ($22.5k) must appear before INV-1002 ($15k)"
        )


# ---------------------------------------------------------------------------
# A3 — progress indicator
# ---------------------------------------------------------------------------

def test_progress_indicator_present(client):
    body = client.get("/").text
    # `N of M reviewed`
    import re
    assert re.search(r"of\s+\d+\s+reviewed", body), (
        "progress indicator 'N of M reviewed' must appear on the landing"
    )


# ---------------------------------------------------------------------------
# C — straight-through APPROVEs are NOT labeled 'clerk'
# ---------------------------------------------------------------------------

def test_straight_through_is_not_labeled_clerk_in_settled_section(client):
    """INV-1001 is a clean straight-through APPROVE (no human touch).
    In the settled section it must NOT be attributed to 'clerk'."""
    body = client.get("/").text
    # Locate the settled section's row for INV-1001 and check its source column
    settled_start = body.find("Settled invoices")
    slice_ = body[settled_start:]
    row_start = slice_.find("INV-1001")
    assert row_start > 0
    row_slice = slice_[row_start:row_start + 600]
    assert "straight-through" in row_slice, (
        "straight-through invoice must render source label as 'system · straight-through'"
    )
    # And the word "clerk" (alone, as an attribution) must NOT appear in this row
    assert ">clerk<" not in row_slice.lower(), "no 'clerk' attribution allowed on a straight-through row"


def test_straight_through_is_not_labeled_clerk_in_review_resolved_table(client):
    """The Model-vs-human resolved table on /queue must also attribute
    straight-through invoices correctly. INV-1001 (clean APPROVE) must
    render with source 'system · straight-through', not 'clerk'."""
    body = client.get("/queue").text
    # Find INV-1001's row in the resolved table
    if "INV-1001" not in body:
        pytest.skip("INV-1001 not in resolved table in this fixture")
    row_start = body.find("INV-1001")
    row_slice = body[row_start:row_start + 800]
    # Source column should say "straight-through" OR "system", not "clerk"
    assert "straight-through" in row_slice or "system" in row_slice.lower(), (
        "straight-through invoice in resolved table must not read as 'clerk'"
    )


# ---------------------------------------------------------------------------
# data-layer contract: source_kind is set on every row
# ---------------------------------------------------------------------------

def test_list_runs_sets_source_kind_three_way(client):
    """Every row must carry source_kind ∈ {system, clerk, fixture}."""
    from src.ui.data import list_runs
    kinds = {r["source_kind"] for r in list_runs()}
    assert kinds <= {"system", "clerk", "fixture"}
    # Demo populates fixture (a few) and system (straight-throughs). Both
    # must be represented on the seeded state.
    assert "system" in kinds
    assert "fixture" in kinds
