"""Settlement + idempotency tests. Mocked provider — no LLM."""
from decimal import Decimal
from pathlib import Path

import pytest

from src.nodes.settle import mock_payment, settle
from src.schema import (
    Decision, Invoice, LineItem, Money, Outcome,
)
from src.store.audit import AuditStore


def _inv(invoice_number: str = "INV-2001", vendor: str = "Widgets Inc.",
         total: str = "500.00") -> Invoice:
    return Invoice(
        invoice_number_raw=invoice_number, invoice_number=invoice_number,
        vendor_raw=vendor, vendor_name=vendor,
        source_file=f"{invoice_number.lower()}.json", source_format="json",
        file_hash="test-hash",
        line_items=[LineItem(
            raw_item_name="WidgetA", canonical_item="WidgetA", quantity=2,
            unit_price=Money(amount_native=Decimal("250"), currency="USD"),
        )],
        stated_total=Money(amount_native=Decimal(total), currency="USD"),
    )


@pytest.fixture
def audit(tmp_path, monkeypatch):
    """Isolated audit store per test."""
    from src import config as cfg_mod
    from src.store import audit as audit_mod
    p = tmp_path / "audit.sqlite"
    monkeypatch.setattr(cfg_mod, "AUDIT_DB_PATH", p)
    monkeypatch.setattr(audit_mod, "AUDIT_DB_PATH", p)
    return AuditStore(path=p)


def test_settle_approve_calls_mock_payment(audit):
    inv = _inv()
    dec = Decision(outcome=Outcome.APPROVE, rationale="clean", confidence=1.0)
    state = {"invoice": inv, "decision": dec}
    result = settle(state)
    assert "PAID" in result["settlement_result"]
    assert result["mock_payment_reference"].startswith("MOCK-")

    # Persisted in the store
    prior = audit.prior_paid_settlement(inv.invoice_number, inv.vendor_name)
    assert prior is not None
    assert prior.settlement_type == "PAID"
    assert prior.amount_usd == Decimal("500.00")


def test_settle_idempotent_refuses_second_pay(audit):
    """The corpus's INV-1011 duplicate pair is real. If the same invoice hits
    settle twice, the second call must refuse — not silently double-pay."""
    inv = _inv()
    dec = Decision(outcome=Outcome.APPROVE, rationale="clean", confidence=1.0)
    state = {"invoice": inv, "decision": dec}

    first = settle(state)
    assert "PAID" in first["settlement_result"]

    second = settle(state)
    assert "REFUSED" in second["settlement_result"]
    assert "double-pay" in second["settlement_result"]


def test_settle_reject_logs_and_persists(audit):
    inv = _inv()
    dec = Decision(outcome=Outcome.REJECT, rationale="fraud signals", confidence=0.9)
    state = {"invoice": inv, "decision": dec}
    result = settle(state)
    assert "REJECTED" in result["settlement_result"]
    # PAID lookup returns None — this was a reject, not a pay
    assert audit.prior_paid_settlement(inv.invoice_number, inv.vendor_name) is None


def test_settle_escalate_unresolved_does_nothing(audit):
    inv = _inv()
    dec = Decision(outcome=Outcome.ESCALATE, rationale="needs review", confidence=0.5)
    state = {"invoice": inv, "decision": dec}
    result = settle(state)
    assert "ESCALATE unresolved" in result["settlement_result"]
    assert audit.prior_paid_settlement(inv.invoice_number, inv.vendor_name) is None


def test_settle_escalate_with_human_approve_pays(audit):
    """Human APPROVE on an ESCALATE triggers mock_payment. The model's
    decision.outcome remains ESCALATE — never overwritten."""
    inv = _inv()
    dec = Decision(outcome=Outcome.ESCALATE, rationale="needs review", confidence=0.5)
    state = {"invoice": inv, "decision": dec, "human_outcome": "APPROVE"}
    result = settle(state)
    assert "PAID" in result["settlement_result"]
    # Model's decision is unchanged
    assert dec.outcome == Outcome.ESCALATE


def test_settle_queued_skips_settlement(audit):
    inv = _inv()
    dec = Decision(outcome=Outcome.ESCALATE, rationale="needs review", confidence=0.5)
    state = {"invoice": inv, "decision": dec, "human_queued": True}
    result = settle(state)
    assert "QUEUED" in result["settlement_result"]


def test_mock_payment_returns_reference():
    payment = mock_payment("Widgets Inc.", Decimal("100.00"))
    assert payment["reference"].startswith("MOCK-")
    assert payment["status"] == "sent"
    assert payment["vendor"] == "Widgets Inc."
    assert payment["amount_usd"] == "100.00"
