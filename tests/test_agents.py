"""Adjudicator / Critic / Scribe / Guardrail — mocked-LLM unit tests.

Real corpus behaviour is exercised via cassette replay in test_graph.py and
in the manual corpus smoke. These tests lock the code paths that live
inside the node functions themselves — guardrail override, missing
Adjudicator handling, scribe skip on APPROVE — using a fake provider.
"""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.graph_state import GraphState
from src.nodes import adjudicate as adj_mod
from src.nodes import scribe as scribe_mod
from src.llm.agent_loop import set_provider
from src.nodes.route_outcome import route_outcome
from src.schema import (
    Decision, Finding, Invoice, LineItem, ModelCall, Money, Outcome, Severity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fake_model_call() -> ModelCall:
    return ModelCall(
        requested_model="grok-4.5", resolved_model="grok-4.5",
        prompt_tokens=100, cached_prompt_tokens=0,
        completion_tokens=50, reasoning_tokens=200,
        latency_ms=1000.0,
        timestamp=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )


def _make_invoice(**overrides) -> Invoice:
    defaults = dict(
        invoice_number_raw="INV-1099", invoice_number="INV-1099",
        vendor_raw="Test Vendor", vendor_name="Test Vendor",
        source_file="test.json", source_format="json",
        file_hash="test-hash",
        line_items=[
            LineItem(
                raw_item_name="WidgetA", canonical_item="WidgetA", quantity=1,
                unit_price=Money(amount_native=Decimal("250"), currency="USD"),
            ),
        ],
        stated_total=Money(amount_native=Decimal("250"), currency="USD"),
    )
    defaults.update(overrides)
    return Invoice(**defaults)


class _FakeProvider:
    """Minimal fake that returns a canned parsed response and one ModelCall."""
    def __init__(self, parsed):
        self._parsed = parsed

    def chat(self, messages, response_schema=None, tools=None, prompt_name=None):
        result = MagicMock()
        result.parsed = self._parsed
        result.content = ""
        result.tool_calls = []
        result.model_call = _fake_model_call()
        result.cache_hit = False
        return result


@pytest.fixture
def restore_provider():
    """Reset the module-level _provider after each test."""
    import src.llm.agent_loop as m
    original = m._provider
    yield
    m._provider = original


# ---------------------------------------------------------------------------
# §2.2 hard guardrail — the most important test
# ---------------------------------------------------------------------------

def test_guardrail_overrides_approve_when_critical_finding_present(restore_provider):
    """CLAUDE.md §2.2: an invoice with a CRITICAL finding cannot resolve to
    APPROVE. If the Adjudicator returns APPROVE anyway, code overrides."""
    fake_output = adj_mod.AdjudicatorOutput(
        outcome="APPROVE",
        rationale="I think this is fine actually.",
        confidence=0.9,
        finding_codes_referenced=[],
    )
    set_provider(_FakeProvider(fake_output))

    critical_finding = Finding(
        code="VN-005", severity=Severity.CRITICAL,
        message="Vendor name is empty",
        evidence="vendor_name == ''",
    )
    state: GraphState = {
        "invoice": _make_invoice(vendor_raw="", vendor_name=""),
        "findings": [critical_finding],
        "critic_rounds": 0,
        "critic_challenges": [],
        "nodes_fired": [],
        "model_calls": [], "tool_calls": [],
    }
    result = adj_mod.adjudicate(state)

    dec = result["decision"]
    assert dec.outcome == Outcome.ESCALATE, (
        "Guardrail must override APPROVE to ESCALATE"
    )
    assert result["guardrail_override_fired"] is True
    assert "§2.2 guardrail" in result["guardrail_override_reason"]
    # The original rationale is preserved in the override reason
    assert "I think this is fine actually." in result["guardrail_override_reason"]


def test_guardrail_does_not_fire_when_no_critical(restore_provider):
    """If the Adjudicator returns APPROVE and no CRITICAL finding exists,
    the outcome must not be overridden."""
    fake_output = adj_mod.AdjudicatorOutput(
        outcome="APPROVE", rationale="Clean invoice.", confidence=0.95,
        finding_codes_referenced=[],
    )
    set_provider(_FakeProvider(fake_output))

    state: GraphState = {
        "invoice": _make_invoice(),
        "findings": [],
        "critic_rounds": 0, "critic_challenges": [],
        "nodes_fired": [], "model_calls": [], "tool_calls": [],
    }
    result = adj_mod.adjudicate(state)
    assert result["decision"].outcome == Outcome.APPROVE
    assert result["guardrail_override_fired"] is False
    assert result["guardrail_override_reason"] is None


def test_guardrail_does_not_fire_when_outcome_already_reject(restore_provider):
    """A CRITICAL + REJECT combo needs no override — REJECT already respects
    the guardrail."""
    fake_output = adj_mod.AdjudicatorOutput(
        outcome="REJECT", rationale="Cannot pay.", confidence=0.95,
        finding_codes_referenced=["VN-005"],
    )
    set_provider(_FakeProvider(fake_output))
    critical = Finding(
        code="VN-005", severity=Severity.CRITICAL,
        message="Empty vendor", evidence="",
    )
    state: GraphState = {
        "invoice": _make_invoice(vendor_raw="", vendor_name=""),
        "findings": [critical],
        "critic_rounds": 0, "critic_challenges": [],
        "nodes_fired": [], "model_calls": [], "tool_calls": [],
    }
    result = adj_mod.adjudicate(state)
    assert result["decision"].outcome == Outcome.REJECT
    assert result["guardrail_override_fired"] is False


# ---------------------------------------------------------------------------
# Tool-loop cap → ESCALATE, never a crash
# ---------------------------------------------------------------------------

def test_tool_loop_cap_produces_escalate_never_crash(restore_provider):
    """A model that never returns a parseable response should escalate,
    not raise. Simulate by making the fake provider always return None
    parsed."""
    class _NeverParses(_FakeProvider):
        def __init__(self):
            super().__init__(None)
    set_provider(_NeverParses())

    state: GraphState = {
        "invoice": _make_invoice(),
        "findings": [],
        "critic_rounds": 0, "critic_challenges": [],
        "nodes_fired": [], "model_calls": [], "tool_calls": [],
    }
    result = adj_mod.adjudicate(state)
    dec = result["decision"]
    assert dec.outcome == Outcome.ESCALATE
    assert "cap" in dec.rationale.lower()


# ---------------------------------------------------------------------------
# Scribe — skip on APPROVE, produce a note otherwise
# ---------------------------------------------------------------------------

def test_scribe_skipped_on_approve(restore_provider):
    """APPROVE outcomes get no note — never call the LLM."""
    provider_would_be_called = _FakeProvider(scribe_mod.ScribeOutput(note="should not appear"))
    set_provider(provider_would_be_called)

    state: GraphState = {
        "invoice": _make_invoice(),
        "findings": [],
        "decision": Decision(outcome=Outcome.APPROVE, rationale="clean", confidence=1.0),
        "nodes_fired": [], "model_calls": [],
    }
    result = scribe_mod.scribe(state)
    assert result.get("scribe_note") is None or "should not appear" not in (result.get("scribe_note") or "")
    assert result["nodes_fired"] == ["scribe:skipped"]


def test_scribe_produces_note_on_escalate(restore_provider):
    fake_output = scribe_mod.ScribeOutput(note="Hold pending vendor confirmation.")
    set_provider(_FakeProvider(fake_output))
    state: GraphState = {
        "invoice": _make_invoice(),
        "findings": [],
        "decision": Decision(outcome=Outcome.ESCALATE, rationale="needs review", confidence=0.5),
        "nodes_fired": [], "model_calls": [],
    }
    result = scribe_mod.scribe(state)
    assert result["scribe_note"] == "Hold pending vendor confirmation."


# ---------------------------------------------------------------------------
# route_outcome publishes Decision.outcome as terminal_status
# ---------------------------------------------------------------------------

def test_route_outcome_uses_adjudicator_decision():
    dec = Decision(outcome=Outcome.ESCALATE, rationale="x", confidence=0.5)
    state: GraphState = {"decision": dec}
    result = route_outcome(state)
    assert result["terminal_status"] == Outcome.ESCALATE


def test_route_outcome_failed_when_no_decision():
    result = route_outcome({})
    assert result["terminal_status"] == Outcome.FAILED
    assert result["failure_reason"] is not None


# ---------------------------------------------------------------------------
# Cross-node investigation caching + prior-investigation summary
# ---------------------------------------------------------------------------

def test_tool_cache_key_stable_for_same_args():
    from src.llm.agent_loop import cache_key
    a = cache_key("get_vendor_record", {"name": "FastShip Ltd."})
    b = cache_key("get_vendor_record", {"name": "FastShip Ltd."})
    assert a == b


def test_tool_cache_key_differs_on_args():
    from src.llm.agent_loop import cache_key
    a = cache_key("get_vendor_record", {"name": "FastShip Ltd."})
    b = cache_key("get_vendor_record", {"name": "QuickShip"})
    assert a != b


def test_tool_cache_key_argument_order_invariant():
    """Sorted-args JSON means dict order doesn't affect the key."""
    from src.llm.agent_loop import cache_key
    a = cache_key("f", {"a": 1, "b": 2})
    b = cache_key("f", {"b": 2, "a": 1})
    assert a == b


def test_format_tool_history_deduplicates_repeats():
    """Same (name, args) shown once even if called multiple times."""
    from src.llm.agent_loop import format_tool_history
    from src.schema import ToolCall

    tc = ToolCall(
        name="get_vendor_record",
        arguments={"name": "FastShip Ltd."},
        result={"status": "inactive"},
        latency_ms=1.0,
        timestamp=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )
    out = format_tool_history([tc, tc, tc])
    # Three input calls, one line in the deduplicated history
    assert out.count("get_vendor_record") == 1


def test_format_tool_history_empty_returns_empty_string():
    from src.llm.agent_loop import format_tool_history
    assert format_tool_history([]) == ""


def test_agent_loop_serves_cache_hit_at_zero_latency(restore_provider):
    """When a tool call has already been executed this run, the cached result
    is served with latency_ms=0 — the audit marker for cache hits."""
    from src.llm.agent_loop import run_agent_loop
    from unittest.mock import MagicMock

    call_count = 0
    def make_response():
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        r.model_call = _fake_model_call()
        r.cache_hit = False
        # First and second calls: request the same tool. Third call: no tools.
        if call_count < 3:
            r.content = ""
            r.tool_calls = [{
                "id": f"call_{call_count}",
                "name": "get_item_reference",
                "arguments": {"item": "WidgetA"},
            }]
            r.parsed = None
        else:
            r.content = ""
            r.tool_calls = []
            r.parsed = adj_mod.AdjudicatorOutput(
                outcome="APPROVE", rationale="ok", confidence=1.0,
            )
        return r

    class _Repeat:
        def chat(self, messages, response_schema=None, tools=None, prompt_name=None):
            return make_response()

    set_provider(_Repeat())

    result = run_agent_loop(
        "test prompt", adj_mod.AdjudicatorOutput, prompt_name="test",
    )
    # Two tool_calls recorded — but the second is a cache hit (latency 0)
    assert len(result.tool_calls) == 2
    latencies = [tc.latency_ms for tc in result.tool_calls]
    assert 0.0 in latencies, f"expected a cache hit at latency 0.0; got {latencies}"


def test_circuit_breaker_trips_on_model_cap(restore_provider):
    from src.llm.agent_loop import CircuitBreakerTripped, run_agent_loop

    class _AnyResponse:
        def chat(self, messages, response_schema=None, tools=None, prompt_name=None):
            r = MagicMock()
            r.model_call = _fake_model_call()
            r.content = ""; r.tool_calls = []; r.cache_hit = False
            r.parsed = adj_mod.AdjudicatorOutput(
                outcome="APPROVE", rationale="x", confidence=1.0,
            ) if response_schema is adj_mod.AdjudicatorOutput else None
            return r
    set_provider(_AnyResponse())

    with pytest.raises(CircuitBreakerTripped) as exc:
        run_agent_loop(
            "test", adj_mod.AdjudicatorOutput, prompt_name="test",
            invoice_model_calls_used=8,       # at MAX_MODEL_CALLS_PER_INVOICE
            invoice_tool_calls_used=0,
        )
    assert "model-call cap" in str(exc.value)


def test_circuit_breaker_trips_on_tool_cap(restore_provider):
    from src.llm.agent_loop import CircuitBreakerTripped, run_agent_loop

    class _ToolThenAnswer:
        def __init__(self):
            self.count = 0
        def chat(self, messages, response_schema=None, tools=None, prompt_name=None):
            self.count += 1
            r = MagicMock()
            r.model_call = _fake_model_call()
            r.content = ""
            r.cache_hit = False
            r.tool_calls = [{
                "id": f"call_{self.count}",
                "name": "get_item_reference",
                "arguments": {"item": f"Item_{self.count}"},
            }]
            r.parsed = None
            return r
    set_provider(_ToolThenAnswer())

    with pytest.raises(CircuitBreakerTripped) as exc:
        run_agent_loop(
            "test", adj_mod.AdjudicatorOutput, prompt_name="test",
            invoice_tool_calls_used=12,       # at MAX_TOOL_CALLS_PER_INVOICE
            invoice_model_calls_used=0,
        )
    assert "tool-call cap" in str(exc.value)


def test_agent_loop_receives_and_returns_cache(restore_provider):
    """A caller can seed the cache; the returned cache contains seeded entries."""
    from src.llm.agent_loop import run_agent_loop, cache_key

    class _NoTools:
        def chat(self, messages, response_schema=None, tools=None, prompt_name=None):
            r = MagicMock()
            r.model_call = _fake_model_call()
            r.content = ""
            r.tool_calls = []
            r.cache_hit = False
            r.parsed = adj_mod.AdjudicatorOutput(
                outcome="APPROVE", rationale="ok", confidence=1.0,
            ) if response_schema is adj_mod.AdjudicatorOutput else None
            return r

    set_provider(_NoTools())

    seed_key = cache_key("get_item_reference", {"item": "WidgetA"})
    seed_cache = {seed_key: {"found": True, "canonical_name": "WidgetA"}}

    result = run_agent_loop(
        "test", adj_mod.AdjudicatorOutput, prompt_name="t",
        tool_cache=seed_cache,
    )
    assert seed_key in result.tool_cache
