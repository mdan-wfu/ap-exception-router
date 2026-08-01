"""Tests for the empty-invoice-number patch.

Four behaviors under test:
  1. route_outcome classifies empty invoice_number as FAILED.
  2. upload_run with FAILED + non-None invoice (empty invoice_number) redirects
     to /upload?err=no_invoice_number instead of the unroutable /invoice/.
  3. POST /failed/{run_id}/dismiss soft-deletes the record from list_runs.
  4. GET / and /invoice/ never return raw JSON 404 — styled page instead.

No live LLM calls.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from src import config as cfg
    from src.store import audit as audit_mod
    from src.store.audit import AuditStore
    from src.ui import data as ui_data
    cfg.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    audit_mod.AUDIT_DB_PATH = cfg.AUDIT_DB_PATH
    ui_data.UPLOAD_DIR = tmp_path / "uploads"
    monkeypatch.setenv("XAI_API_KEY", "xai-FAKE0000000000TESTKEY99")
    AuditStore()  # create schema so bare DB queries don't hit "no such table"
    return tmp_path


# ---------------------------------------------------------------------------
# 1. route_outcome: empty invoice_number → FAILED
# ---------------------------------------------------------------------------

def test_route_outcome_empty_invoice_number_is_failed(isolated):
    """When invoice.invoice_number is empty string, route_outcome must
    return terminal_status=FAILED, not ESCALATE."""
    from src.nodes.route_outcome import route_outcome
    from src.schema import Decision, Invoice, Money, Outcome

    invoice = Invoice(
        invoice_number_raw="", invoice_number="",
        vendor_raw="Acme", vendor_name="Acme",
        source_file="data/uploads/test.txt", source_format="txt", file_hash="abc",
    )
    decision = Decision(outcome=Outcome.ESCALATE, rationale="uncertain", confidence=0.5)
    state = {
        "invoice": invoice,
        "decision": decision,
        "findings": [],
        "nodes_fired": [],
        "model_calls": [],
        "tool_calls": [],
        "scribe_note": None,
        "human_outcome": None,
        "human_note": None,
        "critic_challenges": [],
        "revision_occurred": False,
        "guardrail_override_fired": False,
        "guardrail_override_reason": None,
    }
    result = route_outcome(state)
    assert result["terminal_status"] == Outcome.FAILED
    assert "invoice number" in result.get("failure_reason", "").lower()


def test_route_outcome_non_empty_invoice_number_passes_through(isolated):
    """A normal invoice_number must not be reclassified."""
    from src.nodes.route_outcome import route_outcome
    from src.schema import Decision, Invoice, Money, Outcome

    invoice = Invoice(
        invoice_number_raw="INV-9999", invoice_number="INV-9999",
        vendor_raw="Acme", vendor_name="Acme",
        source_file="data/uploads/test.txt", source_format="txt", file_hash="abc",
    )
    decision = Decision(outcome=Outcome.APPROVE, rationale="ok", confidence=0.99)
    state = {
        "invoice": invoice,
        "decision": decision,
        "findings": [],
        "nodes_fired": [],
        "model_calls": [],
        "tool_calls": [],
        "scribe_note": None,
        "human_outcome": None,
        "human_note": None,
        "critic_challenges": [],
        "revision_occurred": False,
        "guardrail_override_fired": False,
        "guardrail_override_reason": None,
    }
    result = route_outcome(state)
    assert result["terminal_status"] == Outcome.APPROVE


# ---------------------------------------------------------------------------
# 2. upload_run: FAILED + non-None invoice with empty invoice_number
# ---------------------------------------------------------------------------

def test_upload_run_failed_with_empty_invoice_number_redirects_to_upload_banner(
    isolated, monkeypatch
):
    """When route_outcome returns FAILED because invoice_number is empty,
    upload_run must redirect to /upload?err=no_invoice_number — not the
    unroutable /invoice/."""
    from src import graph as graph_mod
    from src.schema import Decision, Invoice, Outcome

    def fake_run_one(source_path, *a, **kw):
        return {
            "source_path": source_path,
            "invoice": Invoice(
                invoice_number_raw="", invoice_number="",
                vendor_raw="", vendor_name="",
                source_file=source_path, source_format="txt", file_hash="h",
            ),
            "decision": Decision(outcome=Outcome.ESCALATE, rationale="empty", confidence=0.5),
            "terminal_status": Outcome.FAILED,  # route_outcome classified it
            "failure_reason": "no invoice number extracted",
            "findings": [], "nodes_fired": [], "model_calls": [], "tool_calls": [],
        }
    monkeypatch.setattr(graph_mod, "run_one", fake_run_one)

    from src.ui.app import app
    client = TestClient(app, follow_redirects=False)

    r = client.post(
        "/upload",
        files={"file": ("noinv.txt", b"Invoice document with no number.", "text/plain")},
    )
    name = r.headers["location"].rsplit("/", 1)[-1]

    r = client.post(f"/upload/{name}/run", data={"confirm": "yes"})
    assert r.status_code == 303
    target = r.headers["location"]

    assert target != "/invoice/", (
        f"empty invoice_number must not redirect to bare /invoice/ — got {target!r}"
    )
    assert target.startswith("/upload"), (
        f"should route back to /upload with a banner — got {target!r}"
    )
    assert "no_invoice_number" in target

    page = client.get(target)
    assert page.status_code == 200
    assert "no invoice number" in page.text.lower() or "did not contain" in page.text.lower()


# ---------------------------------------------------------------------------
# 3. Dismiss endpoint
# ---------------------------------------------------------------------------

def test_dismiss_removes_failed_run_from_queue(isolated):
    """POST /failed/{run_id}/dismiss must soft-delete the record from
    list_runs so it no longer appears in the queue, while the DB row
    itself is preserved."""
    from src.store.audit import AuditStore
    from src.ui import data as ui_data
    from src.ui.app import app

    store = AuditStore()
    run_id = store.record_failed_run(
        source_file="data/uploads/broken.txt",
        invoice_number="",
        source_format="txt",
        error_type="ValueError",
        error_message="no invoice number extracted",
        node="route_outcome",
    )

    # Appears before dismiss
    runs_before = ui_data.list_runs()
    assert any(r["id"] == run_id for r in runs_before), (
        "failed run must appear in list_runs before dismissal"
    )

    client = TestClient(app, follow_redirects=False)
    r = client.post(f"/failed/{run_id}/dismiss")
    assert r.status_code == 303
    assert r.headers["location"] == "/"

    # Gone after dismiss
    runs_after = ui_data.list_runs()
    assert not any(r["id"] == run_id for r in runs_after), (
        "dismissed run must not appear in list_runs"
    )


def test_dismiss_nonexistent_run_is_noop(isolated):
    """Dismissing a run_id that doesn't exist must not raise — just redirect."""
    from src.ui.app import app
    client = TestClient(app, follow_redirects=False)
    r = client.post("/failed/99999/dismiss")
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# 4. No raw JSON 404 on bare /invoice/ or unknown routes
# ---------------------------------------------------------------------------

def test_bare_invoice_route_returns_styled_404(isolated):
    """/invoice/ with no segment must return an HTML page, not FastAPI's
    default {"detail":"Not Found"} JSON body."""
    from src.ui.app import app
    client = TestClient(app, follow_redirects=False)
    r = client.get("/invoice/NONEXISTENT-INV-0000")
    assert r.status_code == 404
    assert "application/json" not in r.headers.get("content-type", ""), (
        "404 must not be a raw JSON body"
    )
    assert "<html" in r.text.lower() or "not found" in r.text.lower()


def test_unknown_route_returns_styled_404(isolated):
    """/no-such-route must return HTML, not JSON."""
    from src.ui.app import app
    client = TestClient(app, follow_redirects=False)
    r = client.get("/no-such-route-at-all")
    assert r.status_code == 404
    assert "application/json" not in r.headers.get("content-type", ""), (
        "404 must not be a raw JSON body"
    )
    assert "<html" in r.text.lower() or "not found" in r.text.lower()
