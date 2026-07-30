"""Run the Phase 4 validator registry and accumulate findings."""
from __future__ import annotations

from src.graph_state import GraphState
from src.validators import Reference, run_validators


# Reference is loaded once per process. It is read-only, so sharing is safe.
_reference: Reference | None = None


def _get_reference() -> Reference:
    global _reference
    if _reference is None:
        _reference = Reference()
    return _reference


def validate(state: GraphState) -> dict:
    invoice = state.get("invoice")
    if invoice is None:
        return {"nodes_fired": ["validate"]}
    findings = run_validators(invoice, _get_reference())
    return {
        "findings": findings,
        "nodes_fired": ["validate"],
    }
