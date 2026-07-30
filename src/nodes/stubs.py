"""Stub nodes for the LLM agents landing in Phase 5c.

Each stub records that it was called (and the fact that it was a stub)
into `nodes_fired` and passes state through unchanged. Wiring the graph
now — with stubs — lets Phase 5b test tool plumbing and Phase 5c drop
in the real agents without any structural graph changes.
"""
from __future__ import annotations

from src.graph_state import GraphState


def adjudicate(state: GraphState) -> dict:  # noqa: ARG001
    return {"nodes_fired": ["adjudicate:STUB"]}


def critique(state: GraphState) -> dict:
    """Increments critic_rounds so the routing conditional terminates."""
    return {
        "nodes_fired": ["critique:STUB"],
        "critic_rounds": state.get("critic_rounds", 0) + 1,
    }
