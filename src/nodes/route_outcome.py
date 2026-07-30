"""Terminal node: derive a placeholder outcome from findings so the graph
runs end to end. Phase 5c's Adjudicator replaces this decision entirely —
this stub exists only to give the graph a terminal state during 5a/5b.
"""
from __future__ import annotations

from src.graph_state import GraphState
from src.schema import Decision, Outcome, Severity


def route_outcome(state: GraphState) -> dict:
    findings = state.get("findings", [])
    if state.get("has_critical"):
        outcome = Outcome.REJECT
        rationale = "placeholder: CRITICAL finding present"
    elif any(f.severity in (Severity.HIGH, Severity.MEDIUM) for f in findings):
        outcome = Outcome.ESCALATE
        rationale = "placeholder: HIGH/MEDIUM findings present"
    else:
        outcome = Outcome.APPROVE
        rationale = "placeholder: no substantive findings"

    decision = Decision(
        outcome=outcome,
        rationale=rationale,
        confidence=0.0,   # placeholder — Adjudicator will report real confidence
    )
    return {
        "decision": decision,
        "terminal_status": outcome,
        "nodes_fired": ["route_outcome"],
    }
