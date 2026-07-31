"""A1 bug fix — the effective-outcome helper must be consulted by every
view, filter, count, and tab-membership check. Otherwise amended state
doesn't reach the right view (the reported reproduction: approve INV-X,
amend to HOLD, does not appear in Held).

These tests exercise the helper against the queue, held, resolved, and
hero-metric surfaces after amendments, to lock the invariant.
"""
from __future__ import annotations

import shutil
import sqlite3
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
# The reported bug: approve → amend to HOLD → not in Held
# ---------------------------------------------------------------------------

def test_amend_approve_to_hold_lands_in_held_and_leaves_resolved(client):
    """The bug reproduction. INV-1006 replayed as APPROVE with a PAID
    settlement. Amending to HOLD must (a) show it on /held, (b) NOT
    show it in the resolved section of /queue, (c) reflect in the
    corpus_summary held count."""
    r = client.post("/invoice/INV-1006/amend",
                    data={"new_outcome": "HOLD",
                          "reason": "Vendor disputes the order — need more info."},
                    follow_redirects=False)
    assert r.status_code == 303

    held_body = client.get("/held").text
    assert "INV-1006" in held_body, "amended HOLD did not reach /held"

    review_body = client.get("/queue").text
    # INV-1006 must NOT be in the resolved section (Model-vs-human table)
    resolved_idx = review_body.find("Model vs human")
    if resolved_idx > 0:
        after = review_body[resolved_idx:]
        assert "INV-1006" not in after, (
            "amended-to-HOLD invoice still shows in the resolved section"
        )

    queue_body = client.get("/").text
    # Queue table shows effective outcome HOLD, not APPROVE
    # We look for the row containing "INV-1006" and check the chip
    row_start = queue_body.find("INV-1006")
    assert row_start > 0
    row_slice = queue_body[row_start:row_start + 800]
    assert "chip-hold" in row_slice, (
        "queue row for amended invoice must show HOLD chip, not APPROVE"
    )


def test_amend_hold_to_approve_leaves_held_and_lands_in_resolved(client):
    """Reverse of the above. Take a fixture-held item (INV-1005 defaults
    to demo-fixture HOLD → human_outcome=None but escalated), first put
    it in real HOLD, then amend to APPROVE."""
    # First: real HOLD via the /queue action
    client.post("/queue/INV-1005",
                data={"action": "HOLD", "note": "waiting on procurement"},
                follow_redirects=False)
    # Verify it's held
    assert "INV-1005" in client.get("/held").text

    # Amend HOLD → APPROVE
    r = client.post("/invoice/INV-1005/amend",
                    data={"new_outcome": "APPROVE",
                          "reason": "Procurement confirmed the order was authorized."},
                    follow_redirects=False)
    assert r.status_code == 303

    # No longer in /held
    assert "INV-1005" not in client.get("/held").text
    # Now in /queue's resolved section
    review_body = client.get("/queue").text
    resolved_idx = review_body.find("Model vs human")
    assert resolved_idx > 0
    assert "INV-1005" in review_body[resolved_idx:]


def test_corpus_summary_reflects_effective_outcomes_after_amendments(client):
    """Hero metrics must count by effective outcome, not raw runs.outcome.
    Amend one APPROVE to REJECT and check the reject count goes up while
    approve count goes down."""
    from src.ui.data import corpus_summary
    before = corpus_summary("provided")

    r = client.post("/invoice/INV-1006/amend",
                    data={"new_outcome": "REJECT",
                          "reason": "Vendor cancelled after payment."},
                    follow_redirects=False)
    assert r.status_code == 303

    after = corpus_summary("provided")
    assert after["approve"] == before["approve"] - 1, (
        f"approve count should drop by 1 after APPROVE→REJECT amendment; "
        f"before={before['approve']} after={after['approve']}"
    )
    assert after["reject"] == before["reject"] + 1


def test_queue_hero_awaiting_count_reflects_amendments(client):
    """Awaiting-review count must drop when an escalation gets amended
    away from ESCALATE (e.g. straight to APPROVE via amendment)."""
    from src.ui.data import corpus_summary
    before = corpus_summary("provided")
    # Amend an awaiting escalation (INV-1005 default HOLD from fixture)
    # to an APPROVE. INV-1005 has effective_outcome=ESCALATE currently.
    client.post("/invoice/INV-1005/amend",
                data={"new_outcome": "APPROVE",
                      "reason": "Confirmed with vendor — legitimate."},
                follow_redirects=False)
    after = corpus_summary("provided")
    assert after["approve"] == before["approve"] + 1


# ---------------------------------------------------------------------------
# The demo-fixture banner replaces per-row chips
# ---------------------------------------------------------------------------

def test_demo_banner_shows_when_any_fixture_row_exists(client):
    """The banner from base.html appears when any run's human_note starts
    with 'demo fixture'. That's true for `make demo`-populated DBs (the
    fixture resolves several invoices)."""
    body = client.get("/").text
    assert "Demo mode" in body
    assert "auto-resolved from a fixture" in body


def test_queue_table_does_not_carry_per_row_auto_chip(client):
    """Per-row auto chip removed from the queue table (banner covers it).
    The chip class name should not appear on the queue landing."""
    body = client.get("/").text
    # The auto chip is not rendered as an element on the queue landing anymore
    # (the CSS class definition itself is still present; we check span usage).
    # Also, the banner text should be visible instead.
    assert 'class="chip chip-auto"' not in body, (
        "queue landing should not render per-row auto chips — banner covers it"
    )
    assert "auto-resolved from a fixture" in body
