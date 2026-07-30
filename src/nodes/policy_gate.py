"""Policy gate — makes the CLAUDE.md §2.2 hard-guardrail predicate visible.

This node does not decide. It evaluates whether any CRITICAL finding
exists and stores the result on state so downstream routing (and the
Phase 5c Adjudicator) can respect the constraint. Any decision belongs
to `route_outcome` (placeholder now) or the Adjudicator (Phase 5c).
"""
from __future__ import annotations

from src.graph_state import GraphState
from src.validators import has_critical


def policy_gate(state: GraphState) -> dict:
    return {
        "has_critical": has_critical(state.get("findings", [])),
        "nodes_fired": ["policy_gate"],
    }
