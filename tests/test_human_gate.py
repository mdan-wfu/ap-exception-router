"""Human gate — three modes. No LLM."""
from decimal import Decimal

import pytest

from src.nodes.human_gate import human_gate
from src.schema import Decision, Invoice, Money, Outcome


def _inv(invoice_number: str = "INV-1012") -> Invoice:
    return Invoice(
        invoice_number_raw=invoice_number, invoice_number=invoice_number,
        vendor_raw="QuickShip Distributers", vendor_name="QuickShip Distributers",
        source_file="test.txt", source_format="txt", file_hash="h",
        stated_total=Money(amount_native=Decimal("9975"), currency="USD"),
    )


def test_human_gate_skips_when_not_escalate():
    dec = Decision(outcome=Outcome.APPROVE, rationale="clean", confidence=1.0)
    state = {"invoice": _inv(), "decision": dec}
    result = human_gate(state)
    assert result["nodes_fired"] == ["human_gate:skipped_not_escalate"]
    assert "human_outcome" not in result


def test_human_gate_demo_mode_uses_fixture(monkeypatch):
    """INV-1012 has 'HOLD' in the fixture. HOLD means human_queued=True."""
    monkeypatch.setenv("HUMAN_GATE_MODE", "demo")
    dec = Decision(outcome=Outcome.ESCALATE, rationale="needs review", confidence=0.5)
    state = {"invoice": _inv("INV-1012"), "decision": dec}
    result = human_gate(state)
    assert result["human_queued"] is True
    assert result["human_outcome"] is None
    assert "demo fixture" in result["human_note"]


def test_human_gate_demo_mode_approve(monkeypatch):
    """INV-1010 in the fixture returns APPROVE."""
    monkeypatch.setenv("HUMAN_GATE_MODE", "demo")
    dec = Decision(outcome=Outcome.ESCALATE, rationale="needs review", confidence=0.5)
    state = {"invoice": _inv("INV-1010"), "decision": dec}
    result = human_gate(state)
    assert result["human_outcome"] == "APPROVE"
    assert result.get("human_queued", False) is False


def test_human_gate_demo_mode_reject(monkeypatch):
    """INV-1003 in the fixture returns REJECT."""
    monkeypatch.setenv("HUMAN_GATE_MODE", "demo")
    dec = Decision(outcome=Outcome.ESCALATE, rationale="needs review", confidence=0.5)
    state = {"invoice": _inv("INV-1003"), "decision": dec}
    result = human_gate(state)
    assert result["human_outcome"] == "REJECT"


def test_human_gate_queue_mode_records_and_exits(monkeypatch):
    monkeypatch.setenv("HUMAN_GATE_MODE", "queue")
    dec = Decision(outcome=Outcome.ESCALATE, rationale="needs review", confidence=0.5)
    state = {"invoice": _inv(), "decision": dec}
    result = human_gate(state)
    assert result["human_queued"] is True
    assert result["human_outcome"] is None
    assert result["nodes_fired"] == ["human_gate:queued"]


def test_human_outcome_never_overwrites_decision(monkeypatch):
    """The model's decision.outcome must stay pristine even after a human
    chooses differently. Both are recorded, distinct — that disagreement
    is the most useful data the system produces."""
    monkeypatch.setenv("HUMAN_GATE_MODE", "demo")
    original_dec = Decision(outcome=Outcome.ESCALATE, rationale="model said escalate", confidence=0.5)
    state = {"invoice": _inv("INV-1010"), "decision": original_dec}
    result = human_gate(state)
    assert result["human_outcome"] == "APPROVE"
    # Decision object is UNCHANGED
    assert original_dec.outcome == Outcome.ESCALATE
    assert original_dec.rationale == "model said escalate"
