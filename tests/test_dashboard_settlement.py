"""Dashboard settlement — the bug this locks:

Reproduction was: approve an escalated invoice from Human Review →
invoice does NOT appear in Payments; Settlement panel on detail page
stays empty; if you then amend the (never-really-settled) approval to
REJECT, there's still nothing in Payments to flag for reversal.

Cause: the dashboard's /queue POST wrote human_outcome to runs but
never invoked the settle logic. mock_payment never fired.

Fix: record_human_decision now calls the settle node's logic directly
(the graph doesn't use LangGraph interrupt() — human_gate returns
synchronously in every mode, so completed runs have no paused state
to resume). The settle node's idempotency check is still the guardrail
against double payment.
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


def _paid_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE settlement_type='PAID'"
    ).fetchone()[0]
    conn.close()
    return n


def _has_paid(db_path: Path, invoice_number: str) -> tuple[bool, str | None]:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT mock_payment_ref FROM settlements "
        "WHERE settlement_type='PAID' AND invoice_number=?",
        (invoice_number,),
    ).fetchone()
    conn.close()
    return (row is not None, row[0] if row else None)


# ---------------------------------------------------------------------------
# 1. Approve from Human Review → settlement row + payment reference
# ---------------------------------------------------------------------------

def test_approve_from_human_review_creates_settlement(client, tmp_path):
    """The bug reproduction. INV-1004 defaults to fixture-HOLD in demo
    mode, so it's on the awaiting-review list with no prior settlement."""
    db = tmp_path / "audit.sqlite"
    before = _paid_count(db)

    r = client.post("/queue/INV-1004",
                    data={"action": "APPROVE", "note": "dashboard approval"},
                    follow_redirects=False)
    assert r.status_code == 303

    after = _paid_count(db)
    assert after == before + 1, "dashboard APPROVE must create a PAID settlement row"
    has_paid, ref = _has_paid(db, "INV-1004")
    assert has_paid
    assert ref and ref.startswith("MOCK-"), (
        f"mock_payment must fire and issue a reference; got ref={ref!r}"
    )


# ---------------------------------------------------------------------------
# 2. The invoice then appears in the Payments ledger
# ---------------------------------------------------------------------------

def test_approved_invoice_appears_in_payments_ledger(client):
    client.post("/queue/INV-1004", data={"action": "APPROVE", "note": "x"},
                follow_redirects=False)
    body = client.get("/payments").text
    assert "INV-1004" in body


# ---------------------------------------------------------------------------
# 3. Amending that approval to REJECT keeps it in Payments, marked reversal
# ---------------------------------------------------------------------------

def test_amend_dashboard_approval_to_reject_shows_reversal_required(client):
    client.post("/queue/INV-1004", data={"action": "APPROVE", "note": "x"},
                follow_redirects=False)
    r = client.post("/invoice/INV-1004/amend",
                    data={"new_outcome": "REJECT",
                          "reason": "duplicate detected after payment"},
                    follow_redirects=False)
    assert r.status_code == 303

    body = client.get("/payments").text
    assert "INV-1004" in body
    assert "reversal required" in body
    assert "duplicate detected after payment" in body


# ---------------------------------------------------------------------------
# 4. Approving from the Held view settles identically
# ---------------------------------------------------------------------------

def test_approve_from_held_view_settles(client, tmp_path):
    """First put an invoice on Held via a clerk action, then approve from
    the Held page — the POST endpoint is the same, but this locks that the
    Held flow also hits the settlement invocation."""
    db = tmp_path / "audit.sqlite"

    # Move INV-1005 to Held (fixture-HOLD → clerk HOLD)
    client.post("/queue/INV-1005", data={"action": "HOLD", "note": "waiting"},
                follow_redirects=False)
    assert not _has_paid(db, "INV-1005")[0]

    # Now approve from Held
    client.post("/queue/INV-1005", data={"action": "APPROVE", "note": "cleared"},
                follow_redirects=False)
    has_paid, ref = _has_paid(db, "INV-1005")
    assert has_paid
    assert ref and ref.startswith("MOCK-")


# ---------------------------------------------------------------------------
# 5. Holding produces no settlement and remains resumable
# ---------------------------------------------------------------------------

def test_hold_does_not_create_settlement(client, tmp_path):
    db = tmp_path / "audit.sqlite"
    before = _paid_count(db)

    client.post("/queue/INV-1007",
                data={"action": "HOLD", "note": "need procurement input"},
                follow_redirects=False)

    # No PAID row appeared
    assert _paid_count(db) == before
    # Not on Payments page either
    body = client.get("/payments").text
    assert "INV-1007" not in body


# ---------------------------------------------------------------------------
# 6. Amending REJECT → APPROVE on unsettled invoice fires settlement once,
#    and only once (idempotency guardrail)
# ---------------------------------------------------------------------------

def test_amend_reject_to_approve_settles_once_and_only_once(client, tmp_path):
    """First reject INV-1004 via the review action (no settlement PAID
    row — settle records REJECTED). Then amend to APPROVE — expect exactly
    one PAID row after, not two. Amend again to APPROVE — still one PAID."""
    db = tmp_path / "audit.sqlite"

    client.post("/queue/INV-1004", data={"action": "REJECT", "note": "no"},
                follow_redirects=False)
    assert not _has_paid(db, "INV-1004")[0]

    client.post("/invoice/INV-1004/amend",
                data={"new_outcome": "APPROVE", "reason": "vendor confirmed"},
                follow_redirects=False)
    assert _has_paid(db, "INV-1004")[0], (
        "amendment REJECT→APPROVE on unsettled invoice must fire mock_payment"
    )

    # Count PAID rows for this invoice specifically
    conn = sqlite3.connect(str(db))
    n = conn.execute(
        "SELECT COUNT(*) FROM settlements "
        "WHERE settlement_type='PAID' AND invoice_number=?",
        ("INV-1004",),
    ).fetchone()[0]
    conn.close()
    assert n == 1

    # Amend to APPROVE AGAIN — the settle node's idempotency check must
    # prevent a second PAID row.
    client.post("/invoice/INV-1004/amend",
                data={"new_outcome": "APPROVE", "reason": "double-check"},
                follow_redirects=False)
    conn = sqlite3.connect(str(db))
    n = conn.execute(
        "SELECT COUNT(*) FROM settlements "
        "WHERE settlement_type='PAID' AND invoice_number=?",
        ("INV-1004",),
    ).fetchone()[0]
    conn.close()
    assert n == 1, (
        "settle node's prior_paid_settlement idempotency must prevent double-pay"
    )
