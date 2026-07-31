"""LangGraph assembly.

Reads like the flow diagram. If you are adding logic here beyond wiring,
it belongs in a node or a routing predicate — not in this file.

Node sequence:

    triage → validate → policy_gate → adjudicate →
        route_after_adjudicate:
            → critique → adjudicate           (up to MAX_CRITIC_ROUNDS)
            → scribe (ESCALATE / REJECT)      → route_after_scribe:
                                                    → human_gate (ESCALATE)
                                                        → settle → route_outcome → END
                                                    → settle (REJECT)   → route_outcome → END
            → settle (APPROVE)                → route_outcome → END

The critic conditional fires when CLAUDE.md §4 CRITIC_TRIGGER holds
(stated_total > threshold OR any HIGH+ finding). Scribe runs only for
ESCALATE / REJECT. Human gate fires only for ESCALATE — three modes:
interactive / demo (fixture) / queue (audit-store write, no resume).
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
from src.nodes.human_gate import human_gate
from src.nodes.policy_gate import policy_gate
from src.nodes.route_outcome import route_outcome
from src.nodes.scribe import scribe
from src.nodes.settle import settle
from src.nodes.triage import triage
from src.nodes.validate import validate
from src.schema import Outcome, Severity


_THRESHOLD = Decimal(str(APPROVAL_THRESHOLD_USD))


def route_after_adjudicate(state: GraphState) -> str:
    """Route the graph after each `adjudicate` call.

    Two gates on continuing to `critique`:
      1. Rounds cap: never above MAX_CRITIC_ROUNDS (currently 1).
         A single critic round runs on every invoice meeting the trigger;
         there is no second round regardless of revision.
      2. Trigger: CRITIC_TRIGGER (total > threshold OR any HIGH+ finding).

    Otherwise route to `scribe` (ESCALATE / REJECT) or directly to
    `route_outcome` (APPROVE).

    A previous "convergence check" (only run round 2 if round 1 revised)
    was removed with the single-round policy — see DECISIONS.md. It became
    dead code once MAX_CRITIC_ROUNDS dropped to 1.
    """
    rounds_so_far = state.get("critic_rounds", 0)
    if rounds_so_far >= MAX_CRITIC_ROUNDS or not _critic_trigger(state):
        return _post_critic_target(state)
    return "critique"


def _post_critic_target(state: GraphState) -> str:
    """After the critic loop terminates, route based on the model's outcome.
    ESCALATE / REJECT → scribe; APPROVE → straight to settle."""
    decision = state.get("decision")
    if decision is not None and decision.outcome in (Outcome.ESCALATE, Outcome.REJECT):
        return "scribe"
    return "settle"


def route_after_scribe(state: GraphState) -> str:
    """After scribe writes its note: ESCALATE → human_gate, REJECT → settle."""
    decision = state.get("decision")
    if decision is not None and decision.outcome == Outcome.ESCALATE:
        return "human_gate"
    return "settle"


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
    g.add_node("human_gate", human_gate)
    g.add_node("settle", settle)
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
            "settle": "settle",
        },
    )
    g.add_edge("critique", "adjudicate")

    g.add_conditional_edges(
        "scribe",
        route_after_scribe,
        {"human_gate": "human_gate", "settle": "settle"},
    )
    g.add_edge("human_gate", "settle")
    g.add_edge("settle", "route_outcome")
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
        "human_queued": False,
    }
    return graph.invoke(initial, config=config)
