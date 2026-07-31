"""Audit store schema + get_prior_invoice `store_populated` behavior."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.store.audit import AuditStore, RunHistoryRow


@pytest.fixture
def audit(tmp_path):
    return AuditStore(path=tmp_path / "audit.sqlite")


# ---------------------------------------------------------------------------
# Schema roundtrip
# ---------------------------------------------------------------------------

def test_schema_creates_all_tables(audit):
    """runs, findings, model_calls, tool_calls, settlements must exist."""
    import sqlite3
    conn = sqlite3.connect(str(audit.path))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert {"runs", "findings", "model_calls", "tool_calls", "settlements"} <= tables


def test_empty_store_reports_empty(audit):
    assert audit.has_any_runs() is False


def test_record_compat_shim_populates_store(audit):
    row = RunHistoryRow(
        invoice_number="INV-9001", vendor_name="Test Vendor",
        stated_total_usd=Decimal("100"), semantic_hash="hash",
        source_file="x.json", outcome="APPROVE",
        finished_at="2026-07-31T00:00:00Z",
    )
    audit.record(row)
    assert audit.has_any_runs() is True


def test_vendor_history_aggregates(audit):
    for i in range(3):
        audit.record(RunHistoryRow(
            invoice_number=f"INV-{i:04d}", vendor_name="Widgets Inc.",
            stated_total_usd=Decimal("100"), semantic_hash="h",
            source_file="x.json", outcome="APPROVE",
            finished_at=f"2026-01-0{i+1}T00:00:00Z",
        ))
    rows = audit.vendor_history_rows("Widgets Inc.")
    assert len(rows) == 3


def test_prior_paid_settlement_locks_double_pay(audit):
    audit.record_settlement(
        run_id=None, invoice_number="INV-DUP", vendor_name="Vendor A",
        settlement_type="PAID", amount_usd=Decimal("100"),
        mock_payment_ref="MOCK-ABC", reason=None,
    )
    prior = audit.prior_paid_settlement("INV-DUP", "Vendor A")
    assert prior is not None
    assert prior.mock_payment_ref == "MOCK-ABC"


# ---------------------------------------------------------------------------
# get_prior_invoice: store_populated distinguishes states
# ---------------------------------------------------------------------------

def test_get_prior_invoice_reports_empty_store(audit, monkeypatch):
    """The bug fix: found=False AND store_populated=False signals
    'infrastructure not evidence' to the Adjudicator."""
    from src.tools.invoice_tools import get_prior_invoice
    from src.tools.models import PriorInvoiceQuery

    result = get_prior_invoice(
        PriorInvoiceQuery(invoice_number="INV-1004"), audit_store=audit,
    )
    assert result.found is False
    assert result.store_populated is False


def test_get_prior_invoice_reports_populated_but_not_this_invoice(audit):
    from src.tools.invoice_tools import get_prior_invoice
    from src.tools.models import PriorInvoiceQuery

    audit.record(RunHistoryRow(
        invoice_number="INV-0500", vendor_name="Other Vendor",
        stated_total_usd=Decimal("50"), semantic_hash="h",
        source_file="x", outcome="APPROVE",
        finished_at="2026-01-01T00:00:00Z",
    ))
    result = get_prior_invoice(
        PriorInvoiceQuery(invoice_number="INV-1004"), audit_store=audit,
    )
    # This invoice's number is not present, BUT the store has other records.
    # The Adjudicator can now treat this as evidence: system has run, just not
    # for this specific number.
    assert result.found is False
    assert result.store_populated is True


def test_get_prior_invoice_finds_matching_settled_run(audit):
    from src.tools.invoice_tools import get_prior_invoice
    from src.tools.models import PriorInvoiceQuery

    audit.record(RunHistoryRow(
        invoice_number="INV-1004", vendor_name="Precision Parts Ltd.",
        stated_total_usd=Decimal("1890"), semantic_hash="hash",
        source_file="invoice_1004.json", outcome="APPROVE",
        finished_at="2026-01-01T00:00:00Z",
    ))
    result = get_prior_invoice(
        PriorInvoiceQuery(invoice_number="INV-1004"), audit_store=audit,
    )
    assert result.found is True
    assert result.store_populated is True
    assert result.stated_total_usd == Decimal("1890")
    assert result.prior_outcome == "APPROVE"
