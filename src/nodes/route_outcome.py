"""Terminal node: freeze the Adjudicator's decision as terminal_status.

The Adjudicator (adjudicate.py) is authoritative for outcome. This node
just publishes it to terminal_status so callers can read the final state
without dereferencing the Decision object.
"""
from __future__ import annotations

from src.graph_state import GraphState
from src.schema import Outcome


def route_outcome(state: GraphState) -> dict:
    decision = state.get("decision")
    if decision is None:
        return {
            "terminal_status": Outcome.FAILED,
            "failure_reason": "no Adjudicator decision available",
            "nodes_fired": ["route_outcome"],
        }
    return {
        "terminal_status": decision.outcome,
        "nodes_fired": ["route_outcome"],
    }
