"""Terminal node: freeze `terminal_status` and persist the full run record.

The Adjudicator is authoritative for outcome; this node publishes it and
writes the run + findings + model_calls + tool_calls into the audit store.
Phase 11's dashboard reads from that store.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.graph_state import GraphState
from src.schema import Outcome
from src.store.audit import AuditStore


def route_outcome(state: GraphState) -> dict:
    decision = state.get("decision")
    invoice = state.get("invoice")

    # An empty invoice_number means extraction produced a structurally valid
    # Invoice but no usable ID. Those records are unroutable via /invoice/
    # and appear as orphaned ESCALATE items in the queue. Classify FAILED
    # so they land in the failed-runs section instead.
    if invoice is not None and not invoice.invoice_number:
        _persist(state, Outcome.FAILED, "no invoice number extracted")
        return {
            "terminal_status": Outcome.FAILED,
            "failure_reason": "no invoice number extracted",
            "nodes_fired": ["route_outcome"],
        }

    if decision is None:
        _persist(state, Outcome.FAILED, "no Adjudicator decision available")
        return {
            "terminal_status": Outcome.FAILED,
            "failure_reason": "no Adjudicator decision available",
            "nodes_fired": ["route_outcome"],
        }

    _persist(state, decision.outcome, None)
    return {
        "terminal_status": decision.outcome,
        "nodes_fired": ["route_outcome"],
    }


def _persist(state: GraphState, terminal_status: Outcome,
             failure_reason: str | None) -> None:
    invoice = state.get("invoice")
    if invoice is None:
        # Nothing meaningful to persist — extraction must have failed.
        return
    audit = AuditStore()
    try:
        audit.record_run(
            invoice=invoice,
            decision=state.get("decision"),
            findings=state.get("findings", []),
            scribe_note=state.get("scribe_note"),
            nodes_fired=state.get("nodes_fired", []),
            model_calls=state.get("model_calls", []),
            tool_calls=state.get("tool_calls", []),
            started_at=None,
            finished_at=datetime.now(timezone.utc).isoformat(),
            terminal_status=terminal_status.value,
            failure_reason=failure_reason,
            human_outcome=state.get("human_outcome"),
            human_note=state.get("human_note"),
            revision_occurred=bool(state.get("revision_occurred", False)),
            guardrail_override_fired=bool(state.get("guardrail_override_fired", False)),
            guardrail_override_reason=state.get("guardrail_override_reason"),
            critic_challenges=state.get("critic_challenges", []),
        )
    except Exception as exc:
        # Persistence must never crash the graph — the decision is already
        # made and is in state. Log and continue.
        print(f"[audit] failed to persist run for {invoice.invoice_number}: {exc}")
