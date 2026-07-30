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
    """Route the graph after each `adjudicate` call.

    Three gates on continuing to `critique`:
      1. Rounds cap: never above MAX_CRITIC_ROUNDS.
      2. Convergence check: after the first critic round, only continue if
         the adjudicator actually REVISED its outcome. A conclusion that
         survived one challenge unchanged is unlikely to be moved by a
         second challenge of the same shape — running the loop again is
         wasted spend and, more importantly, a signal the loop is not
         doing its job. This is convergence, not cost optimisation.
      3. Trigger: CRITIC_TRIGGER (total > threshold OR any HIGH+ finding).

    Otherwise route to `scribe` (ESCALATE / REJECT) or directly to
    `route_outcome` (APPROVE).
    """
    rounds_so_far = state.get("critic_rounds", 0)

    # (2) convergence — checked BEFORE the cap so the reason is visible
    if rounds_so_far >= 1 and not state.get("revision_occurred", False):
        return _post_critic_target(state)

    # (1) cap and (3) trigger
    if rounds_so_far >= MAX_CRITIC_ROUNDS or not _critic_trigger(state):
        return _post_critic_target(state)

    return "critique"


def _post_critic_target(state: GraphState) -> str:
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
        "tool_result_cache": {},
    }
    return graph.invoke(initial, config=config)
