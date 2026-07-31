"""Settlement node.

Terminal action:
  APPROVE  → call mock_payment(vendor, amount); record PAID
  REJECT   → log the rejection with rationale + findings; record REJECTED
  ESCALATE (unresolved) → do nothing here; the human_gate handles it
  ESCALATE (human-resolved APPROVE) → mock_payment then PAID
  ESCALATE (human-resolved REJECT)  → log then REJECTED

**Idempotency (per CLAUDE.md and the corpus's duplicate-pair trap):** before
settling, check the audit store for a prior PAID entry on the same
`(invoice_number, vendor_name)` key. If present, refuse and record why.
A system that can double-pay under replay is a real defect — INV-1011 exists
in the corpus specifically to exercise this failure mode.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from src.graph_state import GraphState
from src.schema import Outcome
from src.store.audit import AuditStore


def mock_payment(vendor: str, amount_usd: Decimal) -> dict:
    """Case-brief compliance: fake payment sender. Returns a reference and status.
    Never actually moves money — the whole system is a decision layer."""
    return {
        "reference": f"MOCK-{uuid.uuid4().hex[:12].upper()}",
        "status": "sent",
        "vendor": vendor,
        "amount_usd": str(amount_usd),
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


def settle(state: GraphState) -> dict:
    invoice = state.get("invoice")
    decision = state.get("decision")
    if invoice is None or decision is None:
        return {
            "settlement_result": "SKIPPED: missing invoice or decision",
            "nodes_fired": ["settle:skipped"],
        }

    # If human_gate queued this run (no resolution provided yet), skip settle.
    if state.get("human_queued"):
        return {
            "settlement_result": "QUEUED: awaiting human review, no settlement",
            "nodes_fired": ["settle:queued"],
        }

    # Determine effective outcome. The human's decision, when present, drives
    # settlement — but the model's `decision.outcome` is never overwritten;
    # it stays in state as evidence of what the model concluded.
    human_outcome = state.get("human_outcome")
    if decision.outcome == Outcome.ESCALATE and human_outcome:
        try:
            effective = Outcome(human_outcome)
        except ValueError:
            effective = Outcome.ESCALATE
    else:
        effective = decision.outcome

    audit = AuditStore()

    # Idempotency check: PAID settlements are unique per (invoice_number, vendor)
    prior = audit.prior_paid_settlement(invoice.invoice_number, invoice.vendor_name)
    if prior is not None and effective == Outcome.APPROVE:
        return {
            "settlement_result": (
                f"REFUSED: {invoice.invoice_number} / {invoice.vendor_name!r} "
                f"already PAID at {prior.settled_at} "
                f"(ref {prior.mock_payment_ref}) — refusing to double-pay"
            ),
            "nodes_fired": ["settle:idempotent_refuse"],
        }

    if effective == Outcome.APPROVE:
        amount = invoice.stated_total.amount_usd if invoice.stated_total else Decimal("0")
        payment = mock_payment(invoice.vendor_name, amount)
        audit.record_settlement(
            run_id=None,   # linked in route_outcome when full run row is persisted
            invoice_number=invoice.invoice_number,
            vendor_name=invoice.vendor_name,
            settlement_type="PAID",
            amount_usd=amount,
            mock_payment_ref=payment["reference"],
            reason=None,
        )
        return {
            "settlement_result": (
                f"PAID {invoice.vendor_name}: ${amount} (ref {payment['reference']})"
            ),
            "mock_payment_reference": payment["reference"],
            "nodes_fired": ["settle:paid"],
        }

    if effective == Outcome.REJECT:
        reason = decision.rationale
        audit.record_settlement(
            run_id=None,
            invoice_number=invoice.invoice_number,
            vendor_name=invoice.vendor_name or "(empty)",
            settlement_type="REJECTED",
            amount_usd=(invoice.stated_total.amount_usd if invoice.stated_total else None),
            mock_payment_ref=None,
            reason=reason,
        )
        return {
            "settlement_result": f"REJECTED {invoice.invoice_number}: {(reason or '')[:120]}",
            "nodes_fired": ["settle:rejected"],
        }

    # ESCALATE with no human outcome — leave for the queue
    return {
        "settlement_result": (
            "ESCALATE unresolved: no human outcome; run_outcome writes to queue"
        ),
        "nodes_fired": ["settle:escalate_unresolved"],
    }
