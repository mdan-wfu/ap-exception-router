"""Two guardrails on the dashboard's live-upload flow. Both were bugs
in prior verification — a prose file bypassed the yellow advisory (any
digit was enough to satisfy the heuristic), and a failed live run
dead-ended at a raw FastAPI 404 (`{"detail":"Not Found"}`) because the
redirect target was `/invoice/` with an empty invoice_number when
extraction produced no usable ID."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Bug 1: the advisory heuristic must NOT be satisfied by lone digits
# ---------------------------------------------------------------------------

from src.ui.data import looks_like_invoice


def test_prose_review_with_digits_does_not_look_like_invoice():
    prose = (
        "Supplier performance review Q3 2026. "
        "Widgets Inc. delivered 94.2 percent on time this quarter, "
        "up from 89.1 previously. No major incidents. "
        "Recommended for continued approval. Signed: procurement lead."
    )
    assert looks_like_invoice(prose) is False


def test_prose_with_dates_and_percentages_does_not_look_like_invoice():
    memo = (
        "Meeting notes 2026-02-05. Discussed Q3 targets: 100 units per week, "
        "reviewed 3 open items. Next review 2026-03-05."
    )
    assert looks_like_invoice(memo) is False


def test_real_invoice_shape_still_detected():
    for txt in [
        "INVOICE #INV-1234\nTotal: $500.00",
        "Invoice Number: 1002",
        "Line: WidgetA 5 @ $250",
        "Line: WidgetA 5 × 250",
        "Subtotal $9,500.00",
    ]:
        assert looks_like_invoice(txt), f"should read as invoice-shaped: {txt!r}"


def test_upload_detail_page_gates_run_live_for_prose(tmp_path, monkeypatch):
    """End-to-end: upload a prose file, verify that:
    - The detail page shows the 'doesn't look like an invoice' gate message
    - The normal Run-live button is absent (would burn a call with no result)
    - The 'Run anyway' escape hatch is present for deliberate edge-case testing
    """
    from src import config as cfg
    from src.store import audit as audit_mod
    from src.ui import data as ui_data
    cfg.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    audit_mod.AUDIT_DB_PATH = cfg.AUDIT_DB_PATH
    ui_data.UPLOAD_DIR = tmp_path / "uploads"
    monkeypatch.setenv("XAI_API_KEY", "xai-FAKE0000000000TESTKEY99")

    from src.ui.app import app
    client = TestClient(app)

    prose = (
        b"Supplier performance review Q3 2026. Widgets Inc. delivered 94.2 "
        b"percent on time this quarter, up from 89.1 previously. No major "
        b"incidents. Recommended for continued approval. Signed: lead."
    )
    r = client.post("/upload", files={"file": ("review.txt", prose, "text/plain")},
                    follow_redirects=False)
    name = r.headers["location"].rsplit("/", 1)[-1]

    detail = client.get(f"/upload/{name}").text
    assert "doesn't look like an invoice" in detail, (
        "prose file must show the gate message"
    )
    assert "Run live — I authorize this call" not in detail, (
        "normal Run-live button must be absent for non-invoice files"
    )
    assert "Run anyway" in detail, (
        "escape hatch must be present for reviewers testing edge cases"
    )


def test_upload_detail_page_offers_run_live_for_real_invoice(tmp_path, monkeypatch):
    """A file with invoice-shaped content must show the normal Run-live button,
    not the gate message."""
    from src import config as cfg
    from src.store import audit as audit_mod
    from src.ui import data as ui_data
    cfg.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    audit_mod.AUDIT_DB_PATH = cfg.AUDIT_DB_PATH
    ui_data.UPLOAD_DIR = tmp_path / "uploads"
    monkeypatch.setenv("XAI_API_KEY", "xai-FAKE0000000000TESTKEY99")

    from src.ui.app import app
    client = TestClient(app)

    invoice = b"INVOICE #INV-9999\nVendor: Acme Corp\nTotal: $1,250.00\nDue: 2026-08-15"
    r = client.post("/upload", files={"file": ("invoice.txt", invoice, "text/plain")},
                    follow_redirects=False)
    name = r.headers["location"].rsplit("/", 1)[-1]

    detail = client.get(f"/upload/{name}").text
    assert "Run live — I authorize this call" in detail, (
        "real invoice must show the normal Run-live button"
    )
    assert "doesn't look like an invoice" not in detail, (
        "real invoice must not show the gate message"
    )


# ---------------------------------------------------------------------------
# Bug 2: a failed live run must land somewhere that resolves and explains
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Every test gets its own audit + uploads dir."""
    from src import config as cfg
    from src.store import audit as audit_mod
    from src.ui import data as ui_data
    cfg.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    audit_mod.AUDIT_DB_PATH = cfg.AUDIT_DB_PATH
    ui_data.UPLOAD_DIR = tmp_path / "uploads"
    monkeypatch.setenv("XAI_API_KEY", "xai-FAKE0000000000TESTKEY99")
    return tmp_path


def test_failed_live_run_redirects_to_detail_page_that_resolves(isolated, monkeypatch):
    """Extractor raises → run_one persists FAILED keyed on basename →
    upload_run redirects to /invoice/{basename} → detail page renders
    with the failure banner. No raw 404 anywhere on this path."""
    from src.adapters import text_adapter

    def fake_boom(*a, **kw):
        raise ValueError("simulated: cannot extract invoice from prose")
    monkeypatch.setattr(text_adapter, "_extract_via_llm", fake_boom)

    from src.ui.app import app
    client = TestClient(app, follow_redirects=False)

    r = client.post(
        "/upload",
        files={"file": ("review.txt",
                        b"Supplier review. Widgets Inc. delivered 94.2 percent. "
                        b"Signed: lead.", "text/plain")},
    )
    name = r.headers["location"].rsplit("/", 1)[-1]

    r = client.post(f"/upload/{name}/run", data={"confirm": "yes"})
    assert r.status_code == 303, r.text
    target = r.headers["location"]
    assert target.startswith("/invoice/"), (
        f"failed run must redirect to /invoice/... — got {target!r}"
    )
    assert target != "/invoice/", (
        "must not redirect to empty invoice_number (FastAPI would 404 with "
        f"raw JSON body); got {target!r}"
    )

    # The redirect target must resolve to a page that explains the failure.
    page = client.get(target)
    assert page.status_code == 200, (
        f"failed-run detail page must render, not 404; got {page.status_code} "
        f"body={page.text[:200]!r}"
    )
    body = page.text
    assert "Run failed" in body or "FAILED" in body, (
        "detail page must surface the failure prominently"
    )
    assert "simulated" in body, "failure reason from the exception must show up"


def test_run_with_empty_invoice_number_redirects_to_upload_with_banner(
    isolated, monkeypatch
):
    """Extraction "succeeds" but yields an empty invoice_number. Prior
    behavior: upload_run redirected to /invoice/ → FastAPI raw 404
    ({"detail":"Not Found"}). Fixed behavior: redirect to
    /upload?err=no_invoice_number&file=... which resolves and explains.

    Faking `run_one` here rather than driving the graph through a
    monkeypatched extractor — that would still fire the Adjudicator's
    LLM call in `mode="auto"` and, absent a fresh cassette, would go
    live against whichever XAI_API_KEY the test environment carries.
    We're testing upload_run's post-graph routing, not the graph."""
    from src import graph as graph_mod
    from src.schema import Decision, Invoice, Money, Outcome
    from decimal import Decimal

    def fake_run_one(source_path, *a, **kw):
        # Simulated "graph completed with an empty invoice_number". The
        # audit row from route_outcome would be keyed on "" — unreachable
        # via /invoice/.
        return {
            "source_path": source_path,
            "invoice": Invoice(
                invoice_number_raw="", invoice_number="",
                vendor_raw="", vendor_name="",
                source_file=source_path, source_format="txt", file_hash="h",
            ),
            "decision": Decision(outcome=Outcome.ESCALATE, rationale="empty", confidence=0.5),
            "terminal_status": Outcome.ESCALATE,
            "findings": [], "nodes_fired": [], "model_calls": [], "tool_calls": [],
        }
    monkeypatch.setattr(graph_mod, "run_one", fake_run_one)

    # Also make re_extract return an invoice with empty invoice_number
    # — matches what upload_run sees on the second-pass extraction.
    from src.ui import data as ui_data
    def fake_re_extract(source_file):
        from src.adapters.router import ExtractionResult
        return ExtractionResult(
            invoice=Invoice(
                invoice_number_raw="", invoice_number="",
                vendor_raw="", vendor_name="",
                source_file=source_file, source_format="txt", file_hash="h",
            ),
            adapter_used="text", llm_fallback=True, fallback_reason=None,
        ), None
    monkeypatch.setattr(ui_data, "re_extract", fake_re_extract)

    from src.ui.app import app
    client = TestClient(app, follow_redirects=False)

    r = client.post(
        "/upload",
        files={"file": ("review.txt", b"Prose content, no invoice structure at all here folks.", "text/plain")},
    )
    name = r.headers["location"].rsplit("/", 1)[-1]

    r = client.post(f"/upload/{name}/run", data={"confirm": "yes"})
    assert r.status_code == 303
    target = r.headers["location"]

    # The bug we're locking: never /invoice/ with an empty ID.
    assert target != "/invoice/", (
        f"empty-invoice-number must not redirect to /invoice/ — got {target!r}"
    )
    assert target.startswith("/upload"), (
        f"empty-invoice-number should route back to /upload with a banner — got {target!r}"
    )

    page = client.get(target)
    assert page.status_code == 200, (
        f"landing page must resolve; got {page.status_code}"
    )
    body = page.text
    assert "no invoice number" in body.lower() or "did not contain" in body.lower(), (
        f"banner must explain what happened; body head: {body[:300]!r}"
    )
