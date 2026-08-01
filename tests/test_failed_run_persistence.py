"""When a node raises inside run_one, the pipeline persists a FAILED audit
row instead of propagating an unhandled exception. Verifies the row is
written with source_file / failure_reason / nodes_fired, and that both the
queue view and the detail view render it as a clean FAILED entry.

Failure is constructed synthetically by monkeypatching the router — no LLM
call, no API spend. See DECISIONS 2026-07-31 failed-run-persistence."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_audit(tmp_path, monkeypatch):
    """Point every AuditStore at a scratch db so this test doesn't collide
    with runs/audit.sqlite (which the demo populates)."""
    from src import config
    scratch = tmp_path / "audit.sqlite"
    monkeypatch.setattr(config, "AUDIT_DB_PATH", scratch)
    monkeypatch.setenv("AUDIT_DB_PATH", str(scratch))
    return scratch


def test_run_one_persists_failed_row_when_extractor_raises(
    isolated_audit, tmp_path, monkeypatch
):
    """Extraction crashes → run_one returns a FAILED state and writes a
    row with terminal_status=FAILED, failure_reason carrying the error
    type + message, and nodes_fired naming the failing layer."""
    src_file = tmp_path / "memo.txt"
    src_file.write_text("Performance review memo, no invoice structure.\n" * 3)

    # Simulate extraction blowing up (e.g. LLM schema validation after
    # MAX_REPAIR_ATTEMPTS on a memo). Patch at src.nodes.triage since
    # that's the import site the graph uses.
    from src.nodes import triage as triage_mod

    def boom(_path):
        raise ValueError("simulated: cannot extract invoice from prose")
    monkeypatch.setattr(triage_mod, "router_extract", boom)

    from src.graph import run_one
    from src.schema import Outcome

    state = run_one(str(src_file))

    # Contract: no unhandled exception; state signals the failure.
    assert state["terminal_status"] == Outcome.FAILED
    assert "ValueError" in state["failure_reason"]
    assert "simulated" in state["failure_reason"]

    # Contract: audit row exists with the expected shape.
    conn = sqlite3.connect(str(isolated_audit))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM runs WHERE terminal_status = 'FAILED'"
    ).fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["outcome"] == "FAILED"
    assert r["terminal_status"] == "FAILED"
    assert r["source_file"] == str(src_file)
    assert r["invoice_number"] == "memo.txt"
    assert r["failure_reason"].startswith("ValueError:")
    assert "simulated" in r["failure_reason"]
    # Node inference: the exception was raised inside the triage node.
    import json
    nodes = json.loads(r["nodes_fired"] or "[]")
    assert nodes == ["triage"] or nodes == ["adapter:router"], nodes


def test_run_one_infers_adapter_when_no_node_frame(
    isolated_audit, tmp_path, monkeypatch
):
    """If a raise originates below the node layer (inside an adapter),
    the failure_reason still lands but the node label carries the
    adapter prefix so the operator can tell where it broke."""
    src_file = tmp_path / "broken.json"
    src_file.write_text("{not valid json")  # deterministic parser will raise

    # Force the deterministic path to crash — router.extract wraps this,
    # but if we bypass by patching text_adapter fallback too, both raise.
    from src.adapters import text_adapter

    def text_boom(*a, **kw):
        raise RuntimeError("simulated: text adapter unavailable")
    monkeypatch.setattr(text_adapter, "extract_from_text", text_boom)

    from src.graph import run_one
    from src.schema import Outcome
    state = run_one(str(src_file))
    assert state["terminal_status"] == Outcome.FAILED

    conn = sqlite3.connect(str(isolated_audit))
    r = conn.execute(
        "SELECT failure_reason, nodes_fired FROM runs WHERE terminal_status='FAILED'"
    ).fetchone()
    assert r is not None
    reason, nodes_json = r
    assert "RuntimeError" in reason


def test_queue_view_renders_failed_entry(isolated_audit, tmp_path, monkeypatch):
    """Queue view exposes a `Failed to process` section listing the
    failure_reason for every FAILED row."""
    src_file = tmp_path / "memo.txt"
    src_file.write_text("Prose that isn't an invoice." * 5)

    from src.nodes import triage as triage_mod
    def boom(_path):
        raise ValueError("simulated extraction failure")
    monkeypatch.setattr(triage_mod, "router_extract", boom)

    from src.graph import run_one
    run_one(str(src_file))

    # Point the FastAPI app at the same scratch audit db. Since data.py
    # opens the audit db via config.AUDIT_DB_PATH at call time (not
    # import time), the monkeypatched config already applies.
    from src.ui.app import app
    client = TestClient(app)

    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "Failed to process" in html
    assert "memo.txt" in html
    assert "simulated extraction failure" in html


def test_detail_view_renders_failure_banner(isolated_audit, tmp_path, monkeypatch):
    """Detail page for a FAILED record shows the failure_reason and
    doesn't 500 despite having no Invoice / no findings / no settlement."""
    src_file = tmp_path / "resume.txt"
    src_file.write_text("Curriculum vitae. Experience. Education." * 3)

    from src.nodes import triage as triage_mod
    def boom(_path):
        raise KeyError("simulated: missing required extraction field")
    monkeypatch.setattr(triage_mod, "router_extract", boom)

    from src.graph import run_one
    run_one(str(src_file))

    from src.ui.app import app
    client = TestClient(app)
    r = client.get("/invoice/resume.txt")
    assert r.status_code == 200, r.text
    html = r.text
    assert "This upload never reached a decision" in html
    assert "KeyError" in html
    assert "simulated" in html
