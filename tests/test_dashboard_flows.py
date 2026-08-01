"""Dashboard revision flows: held state, amendment audit trail, upload
without a key. Each locks a policy from the Phase 11 revision spec."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _seed_populated_store(monkeypatch, tmp_path):
    """Copy the real committed demo audit DB into a per-test path."""
    from src import config as cfg_mod
    from src.store import audit as audit_mod
    isolated = tmp_path / "audit.sqlite"
    monkeypatch.setattr(cfg_mod, "AUDIT_DB_PATH", isolated)
    monkeypatch.setattr(audit_mod, "AUDIT_DB_PATH", isolated)
    real = Path("runs/audit.sqlite")
    if real.exists():
        import shutil
        shutil.copy(str(real), str(isolated))
    else:
        from src.store.audit import AuditStore
        AuditStore(path=isolated)
    return isolated


@pytest.fixture
def client(monkeypatch, tmp_path):
    _seed_populated_store(monkeypatch, tmp_path)
    from src.ui.app import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# B6 — Held is a THIRD state, not filed with resolved
# ---------------------------------------------------------------------------

def test_hold_action_lands_in_held_view_not_resolved(client):
    """A dashboard HOLD writes human_outcome='HOLD'. That's actionable, not
    resolved. It must appear on /held, must NOT appear in the Model-vs-human
    resolved table on /queue."""
    r = client.post("/queue/INV-1005",
                    data={"action": "HOLD", "note": "Need procurement clarity."},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/held"

    held = client.get("/held").text
    assert "INV-1005" in held
    assert "HELD" in held

    review = client.get("/queue").text
    # INV-1005 must NOT be in the resolved table (that's for APPROVE/REJECT)
    # Search for the model-vs-human section header + INV-1005 in a resolved row.
    # A pragmatic check: the /held href appears (nav tab). And INV-1005 does
    # not appear inside the Model-vs-human table as an APPROVE/REJECT row.
    resolved_section_idx = review.find("Model vs human")
    if resolved_section_idx >= 0:
        after = review[resolved_section_idx:]
        # INV-1005 shouldn't be in the resolved section (it's HELD, not resolved)
        assert "INV-1005" not in after, (
            "Held item wrongly listed in the resolved section"
        )


# ---------------------------------------------------------------------------
# B7 — Amendment appends; never overwrites; payment-reversal on APPROVE→other
# ---------------------------------------------------------------------------

def test_amendment_appends_never_overwrites(client):
    """After amending an APPROVE to REJECT, the run's original outcome
    (approve) and the amendment (reject) both remain visible on the
    detail page. Original decision is never overwritten."""
    r = client.post("/invoice/INV-1006/amend",
                    data={"new_outcome": "REJECT",
                          "reason": "Vendor cancelled the order after payment."},
                    follow_redirects=False)
    assert r.status_code == 303

    detail = client.get("/invoice/INV-1006").text
    # Original model outcome (APPROVE) still visible via "model: APPROVE" chip
    assert "model: APPROVE" in detail
    # Amendment surfaced
    assert "amended → REJECT" in detail
    # Decision history section shows both
    assert "Decision history" in detail
    assert ">model<" in detail.lower() or "chip-info" in detail
    assert ">amendment<" in detail.lower() or "chip-amended" in detail


def test_amendment_on_paid_approve_flags_payment_reversal(client):
    """Amending an APPROVE that already settled PAID must surface a
    payment-reversal-required flag. INV-1006 in the recorded demo has
    a PAID settlement (approve → mock_payment fired)."""
    r = client.post("/invoice/INV-1006/amend",
                    data={"new_outcome": "REJECT",
                          "reason": "Order cancelled after payment."},
                    follow_redirects=False)
    assert r.status_code == 303
    detail = client.get("/invoice/INV-1006").text
    assert "PAYMENT REVERSAL REQUIRED" in detail
    assert "cannot un-call a payment" in detail


def test_amendment_without_reason_is_rejected(client):
    """Amendments require a reason. The audit trail is a compliance
    artifact; a silent amendment is exactly what it exists to prevent."""
    # Empty reason — form validation returns 422 (missing required Form field).
    r = client.post("/invoice/INV-1007/amend",
                    data={"new_outcome": "REJECT"},
                    follow_redirects=False)
    # FastAPI's Form(...) with no default returns 422 on missing field.
    assert r.status_code in (303, 422)
    # In either case, no amendment was recorded (test the strong invariant)
    detail = client.get("/invoice/INV-1007").text
    assert "amended → REJECT" not in detail


# ---------------------------------------------------------------------------
# B8 — Upload works without a key; run-live requires both key AND confirm
# ---------------------------------------------------------------------------

def test_upload_without_key_saves_and_shows_cli_command(client, monkeypatch):
    """Without XAI_API_KEY, /upload/{name} shows the file was saved
    and gives the CLI command to run it live. Does not attempt to
    process (would fail — dashboard is replay-mode-forced)."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "")
    # Upload
    r = client.post(
        "/upload",
        files={"file": ("test.txt", b"INVOICE #INV-9999\nAmount: $123.45\n" * 2, "text/plain")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/upload/")

    detail = client.get(location).text
    assert "XAI_API_KEY" in detail
    assert "python main.py --invoice_path" in detail
    assert "--live" in detail
    # And: the Run-live button should NOT be present (no key → no live path)
    assert 'action="/upload/' not in detail or "Run live" not in detail


def test_upload_run_requires_both_confirm_and_key(client, monkeypatch):
    """/upload/{name}/run without confirm=yes or with no key must NOT
    fire a live call. Redirects back with an error param."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    # First upload a file
    r = client.post(
        "/upload",
        files={"file": ("t.txt", b"INVOICE #INV-8888\nAmount: $10.00\n" * 2, "text/plain")},
        follow_redirects=False,
    )
    name = r.headers["location"].rsplit("/", 1)[-1]

    # No key at all → redirect with nokey error
    r = client.post(f"/upload/{name}/run",
                    data={"confirm": "yes"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "err=nokey" in r.headers["location"]

    # Key present but no confirm → redirect with notconfirmed error
    monkeypatch.setenv("XAI_API_KEY", "xai-test-fake-key-not-used")
    r = client.post(f"/upload/{name}/run",
                    data={"confirm": "no"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "err=notconfirmed" in r.headers["location"]
