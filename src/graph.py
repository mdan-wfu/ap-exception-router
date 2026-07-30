"""LangGraph assembly.

Reads like the flow diagram. If you are adding logic here beyond wiring,
it belongs in a node or a routing predicate — not in this file.

Node sequence:

    triage → validate → policy_gate → adjudicate →
        route_after_adjudicate:
            → critique → adjudicate  (up to MAX_CRITIC_ROUNDS)
            → scribe (if ESCALATE / REJECT) → route_outcome → END
            → route_outcome (if APPROVE) → END

The critic conditional fires when CLAUDE.md §4 CRITIC_TRIGGER holds
(stated_total > threshold OR any HIGH+ finding) and the rounds cap is
not exhausted. The scribe runs only for ESCALATE / REJECT — APPROVE
needs no human-facing note.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from src.config import APPROVAL_THRESHOLD_USD, MAX_CRITIC_ROUNDS
from src.graph_state import GraphState
from src.nodes.adjudicate import adjudicate
from src.nodes.critique import critique
from src.nodes.policy_gate import policy_gate
from src.nodes.route_outcome import route_outcome
from src.nodes.scribe import scribe
from src.nodes.triage import triage
from src.nodes.validate import validate
from src.schema import Outcome, Severity


_THRESHOLD = Decimal(str(APPROVAL_THRESHOLD_USD))


def route_after_adjudicate(state: GraphState) -> str:
    """CRITIC_TRIGGER: stated_total > threshold OR any HIGH+ finding.
    Cap at MAX_CRITIC_ROUNDS. After the cap (or if no trigger), route to
    the scribe when the outcome is ESCALATE / REJECT, otherwise straight
    to route_outcome for APPROVE.
    """
    if state.get("critic_rounds", 0) < MAX_CRITIC_ROUNDS and _critic_trigger(state):
        return "critique"

    decision = state.get("decision")
    if decision is not None and decision.outcome in (Outcome.ESCALATE, Outcome.REJECT):
        return "scribe"
    return "route_outcome"


def _critic_trigger(state: GraphState) -> bool:
    invoice = state.get("invoice")
    over = (
        invoice is not None
        and invoice.stated_total is not None
        and invoice.stated_total.amount_usd > _THRESHOLD
    )
    has_high = any(
        f.severity in (Severity.HIGH, Severity.CRITICAL)
        for f in state.get("findings", [])
    )
    return over or has_high


def build_graph(checkpointer_path: Path | str | None = "runs/checkpoints.sqlite"):
    g = StateGraph(GraphState)

    g.add_node("triage", triage)
    g.add_node("validate", validate)
    g.add_node("policy_gate", policy_gate)
    g.add_node("adjudicate", adjudicate)
    g.add_node("critique", critique)
    g.add_node("scribe", scribe)
    g.add_node("route_outcome", route_outcome)

    g.add_edge(START, "triage")
    g.add_edge("triage", "validate")
    g.add_edge("validate", "policy_gate")
    g.add_edge("policy_gate", "adjudicate")

    g.add_conditional_edges(
        "adjudicate",
        route_after_adjudicate,
        {
            "critique": "critique",
            "scribe": "scribe",
            "route_outcome": "route_outcome",
        },
    )
    g.add_edge("critique", "adjudicate")
    g.add_edge("scribe", "route_outcome")
    g.add_edge("route_outcome", END)

    if checkpointer_path is None:
        return g.compile()

    checkpointer_path = Path(checkpointer_path)
    checkpointer_path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(str(checkpointer_path), check_same_thread=False)
    return g.compile(checkpointer=SqliteSaver(conn))


def run_one(source_path: str, graph=None, thread_id: str | None = None) -> GraphState:
    graph = graph if graph is not None else build_graph()
    config = {"configurable": {"thread_id": thread_id or source_path}}
    initial: GraphState = {
        "source_path": source_path,
        "findings": [],
        "nodes_fired": [],
        "model_calls": [],
        "tool_calls": [],
        "critic_challenges": [],
        "critic_rounds": 0,
    }
    return graph.invoke(initial, config=config)
