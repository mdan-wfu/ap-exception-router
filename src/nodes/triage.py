"""Triage + extract, collapsed.

The original phase-diagram had triage and extract as separate nodes. In
practice `router.extract()` returns an `ExtractionResult` that carries
the finished `Invoice`; there is no post-normalization work left for a
separate `extract` node to do, and EX- findings are emitted by the
extraction validator inside `validate`. Keeping an empty `extract` node
purely for symmetry would violate the "read like the flow diagram" rule
in `src/graph.py`. Recorded in DECISIONS.md.
"""
from __future__ import annotations

from pathlib import Path

from src.adapters.router import extract as router_extract
from src.graph_state import GraphState


def triage(state: GraphState) -> dict:
    path = Path(state["source_path"])
    result = router_extract(path)
    return {
        "adapter_used": result.adapter_used,
        "llm_fallback": result.llm_fallback,
        "fallback_reason": result.fallback_reason,
        "invoice": result.invoice,
        "nodes_fired": ["triage"],
    }
