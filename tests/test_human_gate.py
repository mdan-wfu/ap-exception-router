"""Human gate — genuinely suspends via LangGraph `interrupt()`.

The node no longer resolves synchronously in any mode. We test it by
building a minimal graph (triage-free) and driving it through
`run_with_human_resume`, which is the same helper that `run_one` and
the batch loop use. That gives us end-to-end coverage of both the
suspend-and-resume mechanics AND the three modes' resume-value
production.

For queue-mode's dashboard resume path, we invoke `Command(resume=...)`
directly to prove the checkpointed graph accepts a supplied answer
without going through the runner. That mirrors what
`/queue/{invoice_number}` will do when a genuinely-paused run is
resumed by a clerk.
"""
from decimal import Decimal

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.graph import run_with_human_resume
from src.graph_state import GraphState
from src.nodes.human_gate import human_gate
from src.schema import Decision, Invoice, Money, Outcome


def _inv(invoice_number: str = "INV-1012") -> Invoice:
    return Invoice(
        invoice_number_raw=invoice_number, invoice_number=invoice_number,
        vendor_raw="QuickShip Distributers", vendor_name="QuickShip Distributers",
        source_file="test.txt", source_format="txt", file_hash="h",
        stated_total=Money(amount_native=Decimal("9975"), currency="USD"),
    )


def _mini_graph():
    """A graph that jumps straight to human_gate — nothing else."""
    from langgraph.checkpoint.memory import MemorySaver
    g = StateGraph(GraphState)
    g.add_node("human_gate", human_gate)
    g.add_edge(START, "human_gate")
    g.add_edge("human_gate", END)
    return g.compile(checkpointer=MemorySaver())


def _initial(inv: Invoice, outcome: Outcome = Outcome.ESCALATE, rationale: str = "needs review"):
    return {
        "invoice": inv,
        "decision": Decision(outcome=outcome, rationale=rationale, confidence=0.5),
        "findings": [],
        "nodes_fired": [],
        "model_calls": [],
        "tool_calls": [],
        "critic_challenges": [],
        "critic_rounds": 0,
        "tool_result_cache": {},
        "human_queued": False,
    }


def _config(thread: str):
    return {"configurable": {"thread_id": thread}}


# ---------------------------------------------------------------------------
# Short-circuit: non-ESCALATE decisions bypass the gate entirely
# ---------------------------------------------------------------------------

def test_human_gate_skips_when_not_escalate(monkeypatch):
    monkeypatch.setenv("HUMAN_GATE_MODE", "demo")
    g = _mini_graph()
    state = _initial(_inv(), outcome=Outcome.APPROVE, rationale="clean")
    result = run_with_human_resume(g, state, _config("skip"))
    assert "human_gate:skipped_not_escalate" in result["nodes_fired"]
    assert result.get("human_outcome") is None
    assert result.get("human_queued", False) is False


# ---------------------------------------------------------------------------
# Demo mode — fixture drives resolution
# ---------------------------------------------------------------------------

def test_demo_mode_holds_from_fixture(monkeypatch):
    """INV-1012 has HOLD in the fixture. HOLD sets human_queued=True."""
    monkeypatch.setenv("HUMAN_GATE_MODE", "demo")
    g = _mini_graph()
    result = run_with_human_resume(g, _initial(_inv("INV-1012")), _config("d-hold"))
    assert result["human_queued"] is True
    assert result["human_outcome"] is None
    assert "demo fixture" in result["human_note"]
    assert "human_gate:demo_hold" in result["nodes_fired"]


def test_demo_mode_approve(monkeypatch):
    """INV-1010 in the fixture returns APPROVE."""
    monkeypatch.setenv("HUMAN_GATE_MODE", "demo")
    g = _mini_graph()
    result = run_with_human_resume(g, _initial(_inv("INV-1010")), _config("d-app"))
    assert result["human_outcome"] == "APPROVE"
    assert result.get("human_queued", False) is False
    assert "human_gate:demo_approve" in result["nodes_fired"]


def test_demo_mode_reject(monkeypatch):
    """INV-1003 in the fixture returns REJECT."""
    monkeypatch.setenv("HUMAN_GATE_MODE", "demo")
    g = _mini_graph()
    result = run_with_human_resume(g, _initial(_inv("INV-1003")), _config("d-rej"))
    assert result["human_outcome"] == "REJECT"
    assert "human_gate:demo_reject" in result["nodes_fired"]


# ---------------------------------------------------------------------------
# Queue mode — default HOLD keeps the run auditable and resumable
# ---------------------------------------------------------------------------

def test_queue_mode_defaults_to_hold_and_writes_state(monkeypatch):
    monkeypatch.setenv("HUMAN_GATE_MODE", "queue")
    g = _mini_graph()
    result = run_with_human_resume(g, _initial(_inv("INV-NEW")), _config("q"))
    assert result["human_queued"] is True
    assert result["human_outcome"] is None
    assert "human_gate:queued_hold" in result["nodes_fired"]


# ---------------------------------------------------------------------------
# Suspend-and-resume mechanics — proves the interrupt is real, and that
# a dashboard-style resume by way of Command(resume=...) works
# ---------------------------------------------------------------------------

def test_graph_actually_suspends_and_can_be_resumed_by_command():
    """Bypass the runner. Prove the graph pauses at human_gate and
    resumes with an explicit Command supplied by the caller — that's
    the mechanism the dashboard uses to resolve queued runs."""
    g = _mini_graph()
    cfg = _config("suspend")
    first = g.invoke(_initial(_inv("INV-NEW")), config=cfg)
    # Interrupt payload is exposed on the returned state.
    interrupts = first.get("__interrupt__")
    assert interrupts, "human_gate must suspend the graph, not return synchronously"
    packet = interrupts[0].value
    assert packet["invoice_number"] == "INV-NEW"
    assert "rationale" in packet

    # Confirm the graph resumes cleanly with a dashboard-style answer.
    resumed = g.invoke(
        Command(resume={"outcome": "APPROVE", "note": "clerk cleared", "source": "dashboard"}),
        config=cfg,
    )
    assert resumed["human_outcome"] == "APPROVE"
    assert resumed["human_note"] == "clerk cleared"
    assert "human_gate:dashboard_approve" in resumed["nodes_fired"]


# ---------------------------------------------------------------------------
# The model's Decision object is never mutated by the human's answer
# ---------------------------------------------------------------------------

def test_human_outcome_never_overwrites_model_decision(monkeypatch):
    monkeypatch.setenv("HUMAN_GATE_MODE", "demo")
    g = _mini_graph()
    original = Decision(outcome=Outcome.ESCALATE, rationale="model said escalate", confidence=0.5)
    state = _initial(_inv("INV-1010"))
    state["decision"] = original
    result = run_with_human_resume(g, state, _config("preserve"))
    assert result["human_outcome"] == "APPROVE"
    assert original.outcome == Outcome.ESCALATE
    assert original.rationale == "model said escalate"
