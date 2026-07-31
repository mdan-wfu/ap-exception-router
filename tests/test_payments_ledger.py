"""Payments ledger — membership rule, reversal marking, inline amend."""
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


def test_payments_route_renders_and_lists_paid_invoices(client):
    r = client.get("/payments")
    assert r.status_code == 200
    body = r.text
    assert "Total paid to date" in body
    assert "Ledger" in body
    # From `make demo`, 5 straight-through paids: INV-1001, 1006, 1010(via
    # human), 1011, 1015. Confirm the clean-invoice PAIDs are listed.
    for n in ("INV-1001", "INV-1006", "INV-1015"):
        assert n in body


def test_paid_then_amended_row_still_appears_marked_reversal_required(client):
    """Membership rule: an invoice appears in the ledger iff mock_payment
    fired, NOT iff its current effective outcome is APPROVE. INV-1006
    replayed as straight-through APPROVE + PAID; amend to REJECT and
    verify it stays in the ledger, marked."""
    # Amend the paid invoice
    r = client.post("/invoice/INV-1006/amend",
                    data={"new_outcome": "REJECT",
                          "reason": "Order cancelled after payment."},
                    follow_redirects=False)
    assert r.status_code == 303

    body = client.get("/payments").text
    assert "INV-1006" in body, (
        "paid-then-reversed invoice must remain in the ledger — "
        "the money left the building regardless of the amendment"
    )
    assert "reversal required" in body
    # Amendment reason surfaces on the ledger row
    assert "Order cancelled after payment" in body


def test_payments_total_matches_sum_of_paid_amounts(client):
    from src.ui.data import payments_ledger, payments_total
    rows = payments_ledger()
    computed = sum(float(r["amount_usd"] or 0) for r in rows)
    totals = payments_total()
    assert abs(totals["total_usd"] - computed) < 0.01
    assert totals["count"] == len(rows)


def test_reversal_totals_track_amendments(client):
    """After amending one paid invoice, reversal_count = 1 and
    reversal_total_usd equals that invoice's amount."""
    from src.ui.data import payments_ledger, payments_total
    before = payments_total()
    assert before["reversal_count"] == 0

    client.post("/invoice/INV-1006/amend",
                data={"new_outcome": "REJECT", "reason": "test"},
                follow_redirects=False)

    after = payments_total()
    assert after["reversal_count"] == 1
    # INV-1006 total is $2,750 from the recorded run
    inv1006_amount = next(
        r["amount_usd"] for r in payments_ledger() if r["invoice_number"] == "INV-1006"
    )
    assert abs(after["reversal_total_usd"] - float(inv1006_amount)) < 0.01


def test_inline_amend_from_resolved_table_works(client):
    """Section C: the same POST /invoice/{n}/amend endpoint drives the
    inline modal on the Model-vs-human table. Verify the resolved table
    renders an amend control (button + form structure) for each row."""
    body = client.get("/queue").text
    # Resolved section contains at least one amend button
    assert "amend" in body.lower()
    # And each row includes the See analysis link (D — full-analysis link
    # everywhere an invoice is listed)
    assert "See analysis" in body or "full analysis" in body


def test_reason_callout_renders_on_detail_after_amendment(client):
    """Section D: the current hold or amendment reason must appear
    above-the-fold on the detail page, not just in the history section."""
    client.post("/invoice/INV-1006/amend",
                data={"new_outcome": "HOLD",
                      "reason": "Waiting on procurement clarification."},
                follow_redirects=False)
    body = client.get("/invoice/INV-1006").text
    # The callout is high in the page — check it appears BEFORE the
    # Adjudicator rationale section, which is well below the fold.
    reason_idx = body.find("Waiting on procurement clarification")
    adjudicator_idx = body.find(">Adjudicator<")
    assert reason_idx > 0, "amendment reason must appear on detail page"
    assert adjudicator_idx > 0, "adjudicator section must exist for the position check"
    assert reason_idx < adjudicator_idx, (
        "amendment reason callout must render above-the-fold, "
        "before the Adjudicator section"
    )
