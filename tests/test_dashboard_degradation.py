"""Dashboard degrades gracefully when a run's source_file is missing.

Two shapes matter:
  - Detail view: return 200 with an inline note where the enrichment would
    be, rendering everything the audit store DID persist (findings,
    rationale, tool trace, cost).
  - Queue/list view: degrade per row, not per page. One unreadable
    source file must not blank out the other 19 invoices' rows.

The likely trigger is a reviewer processing an invoice from `/tmp/…`
and later cloning the repo elsewhere — the audit-store row's
absolute-path source_file no longer resolves.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _seed_populated_store(monkeypatch, tmp_path):
    """Run make demo's replay to populate a per-test audit DB, then swap
    AUDIT_DB_PATH to the temp file so the dashboard reads it."""
    from src import config as cfg_mod
    from src.store import audit as audit_mod
    isolated = tmp_path / "audit.sqlite"
    monkeypatch.setattr(cfg_mod, "AUDIT_DB_PATH", isolated)
    monkeypatch.setattr(audit_mod, "AUDIT_DB_PATH", isolated)

    # Copy the real committed demo DB (if present) so we get realistic rows.
    real = Path("runs/audit.sqlite")
    if real.exists():
        import shutil
        shutil.copy(str(real), str(isolated))
    else:
        # Build one deterministically from a mini fixture.
        from src.store.audit import AuditStore
        AuditStore(path=isolated)
    return isolated


def _insert_orphan_row(db_path: Path, invoice_number: str, source_file: str):
    """Add a runs row whose source_file does NOT exist on disk."""
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO runs (
            invoice_number, vendor_name, source_file, source_format,
            stated_total_usd, currency, outcome, rationale,
            nodes_fired, finished_at, terminal_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        invoice_number, "Missing Vendor Inc.", source_file, "txt",
        1234.56, "USD", "ESCALATE",
        "Test row with an unreadable source_file — the dashboard must "
        "render this without 500-ing.",
        '["triage", "validate", "route_outcome"]', now, "ESCALATE",
    ))
    conn.commit()
    conn.close()


@pytest.fixture
def client(monkeypatch, tmp_path):
    _seed_populated_store(monkeypatch, tmp_path)
    from src.ui.app import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Detail view — 200 with a note when the source file is gone
# ---------------------------------------------------------------------------

def test_detail_view_renders_when_source_file_missing(monkeypatch, tmp_path):
    db = _seed_populated_store(monkeypatch, tmp_path)
    _insert_orphan_row(db, "INV-9998", "/tmp/does_not_exist/ghost.txt")

    from src.ui.app import app
    client = TestClient(app)
    r = client.get("/invoice/INV-9998")
    assert r.status_code == 200
    body = r.text
    # The amber degradation note appears
    assert "Line-item detail unavailable" in body
    # The audit-store record IS rendered — rationale text is present
    assert "unreadable source_file" in body
    # And the source-file placeholder from read_source_text also renders
    assert "source file not found" in body


# ---------------------------------------------------------------------------
# Queue view — degrade per row, not per page
# ---------------------------------------------------------------------------

def test_queue_view_renders_when_one_source_file_missing(monkeypatch, tmp_path):
    db = _seed_populated_store(monkeypatch, tmp_path)
    _insert_orphan_row(db, "INV-9997", "/tmp/does_not_exist/orphan.txt")

    from src.ui.app import app
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    # The orphan row appears
    assert "INV-9997" in body
    # AND at least a handful of the real corpus rows are still there
    for n in ("INV-1001", "INV-1013", "INV-1004"):
        assert n in body, f"expected {n} in queue view — a broken row should not blank the page"


# ---------------------------------------------------------------------------
# Dashboard forces replay mode unconditionally
# ---------------------------------------------------------------------------

def test_importing_ui_app_forces_llm_mode_replay(monkeypatch):
    """Reviewer with LLM_MODE=live in her shell must not have the dashboard
    silently inherit it. See DECISIONS 2026-07-31 dashboard-forced-replay."""
    monkeypatch.setenv("LLM_MODE", "live")
    # Re-import: FastAPI app module sets LLM_MODE at import time.
    import importlib
    import src.ui.app as ui_app
    importlib.reload(ui_app)
    import os
    assert os.environ["LLM_MODE"] == "replay", (
        "src.ui.app must set LLM_MODE=replay unconditionally, not via setdefault"
    )
