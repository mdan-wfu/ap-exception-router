"""LangGraph assembly.

Reads like the flow diagram. If you are adding logic here beyond wiring,
it belongs in a node or a routing predicate — not in this file.

Node sequence:

    triage → validate → policy_gate → adjudicate → (critique?)+ → route_outcome

The `adjudicate` and `critique` nodes are Phase 5c stubs; graph structure
is final. The conditional critic edge fires when the invoice exceeds the
approval threshold OR any finding is HIGH+ (CLAUDE.md §4 CRITIC_TRIGGER),
capped at MAX_CRITIC_ROUNDS.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from src.config import APPROVAL_THRESHOLD_USD, MAX_CRITIC_ROUNDS
from src.graph_state import GraphState
from src.nodes.policy_gate import policy_gate
from src.nodes.route_outcome import route_outcome
from src.nodes.stubs import adjudicate, critique
from src.nodes.triage import triage
from src.nodes.validate import validate
from src.schema import Severity


_THRESHOLD = Decimal(str(APPROVAL_THRESHOLD_USD))


def should_critique(state: GraphState) -> str:
    """CLAUDE.md §4 CRITIC_TRIGGER: total > threshold OR any HIGH+ finding,
    capped at MAX_CRITIC_ROUNDS."""
    if state.get("critic_rounds", 0) >= MAX_CRITIC_ROUNDS:
        return "route_outcome"

    invoice = state.get("invoice")
    total_over = (
        invoice is not None
        and invoice.stated_total is not None
        and invoice.stated_total.amount_usd > _THRESHOLD
    )
    has_high = any(
        f.severity in (Severity.HIGH, Severity.CRITICAL)
        for f in state.get("findings", [])
    )
    return "critique" if (total_over or has_high) else "route_outcome"


def build_graph(checkpointer_path: Path | str | None = "runs/checkpoints.sqlite"):
    """Build and compile the pipeline. Checkpointer is Phase 6's dependency
    for the human-gate `interrupt()`; installed now so Phase 6 does not
    retrofit it."""
    g = StateGraph(GraphState)

    g.add_node("triage", triage)
    g.add_node("validate", validate)
    g.add_node("policy_gate", policy_gate)
    g.add_node("adjudicate", adjudicate)
    g.add_node("critique", critique)
    g.add_node("route_outcome", route_outcome)

    g.add_edge(START, "triage")
    g.add_edge("triage", "validate")
    g.add_edge("validate", "policy_gate")
    g.add_edge("policy_gate", "adjudicate")

    g.add_conditional_edges(
        "adjudicate",
        should_critique,
        {"critique": "critique", "route_outcome": "route_outcome"},
    )
    # After a critique round, re-enter adjudicate (Phase 5c will use the
    # critic's challenge to revise). The `should_critique` cap on
    # MAX_CRITIC_ROUNDS prevents infinite loops.
    g.add_edge("critique", "adjudicate")

    g.add_edge("route_outcome", END)

    if checkpointer_path is None:
        return g.compile()

    checkpointer_path = Path(checkpointer_path)
    checkpointer_path.parent.mkdir(parents=True, exist_ok=True)
    # SqliteSaver.from_conn_string returns a context manager. For a compiled
    # graph we want a persistent checkpointer, so open the connection directly.
    import sqlite3
    conn = sqlite3.connect(str(checkpointer_path), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return g.compile(checkpointer=checkpointer)


def run_one(source_path: str, graph=None, thread_id: str | None = None) -> GraphState:
    """Run a single invoice through the graph. Convenience wrapper."""
    graph = graph if graph is not None else build_graph()
    config = {"configurable": {"thread_id": thread_id or source_path}}
    initial: GraphState = {
        "source_path": source_path,
        "findings": [],
        "nodes_fired": [],
        "critic_rounds": 0,
    }
    return graph.invoke(initial, config=config)
