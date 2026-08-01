"""Human gate — genuinely suspends the graph via LangGraph's `interrupt()`.

The node always calls `interrupt(packet)` when the model's decision is
ESCALATE, and short-circuits when it's not. It does NOT read the mode.
Mode dispatch (demo / interactive / queue) lives in the runner that
wraps `graph.invoke` — see `src.graph.run_with_human_resume` and the
dashboard's `/queue/{invoice_number}` route.

On suspension, the payload passed to `interrupt(...)` is the escalation
context a clerk needs to decide: invoice identifiers, findings, the
Adjudicator's rationale, and the Scribe's note. On resumption via
`Command(resume={"outcome": ..., "note": ..., "source": ...})`, the
node applies the human's decision:

  - HOLD          → `human_queued=True`, `human_outcome=None`. `settle`
                    skips; the audit trail still records the run.
  - APPROVE/REJECT → `human_outcome=<outcome>`. `settle` drives payment
                    or rejection.

The `source` field on the resume value labels the resolution path
("demo" / "interactive" / "queued" / "dashboard") — it is folded into
the `nodes_fired` tag so a reader can tell who resolved the escalation
without cross-referencing the audit store's `human_note`.
"""
from __future__ import annotations

from langgraph.types import interrupt

from src.graph_state import GraphState
from src.schema import Outcome


VALID_HUMAN_OUTCOMES = {"APPROVE", "REJECT", "HOLD"}


def human_gate(state: GraphState) -> dict:
    decision = state.get("decision")

    # Only ESCALATE routes through the gate. Anything else short-circuits.
    if decision is None or decision.outcome != Outcome.ESCALATE:
        return {"nodes_fired": ["human_gate:skipped_not_escalate"]}

    invoice = state["invoice"]
    findings = state.get("findings", [])
    scribe_note = state.get("scribe_note")

    packet = {
        "invoice_number": invoice.invoice_number,
        "vendor_name": invoice.vendor_name,
        "stated_total_usd": (
            float(invoice.stated_total.amount_usd) if invoice.stated_total else None
        ),
        "currency": (
            invoice.stated_total.currency if invoice.stated_total else None
        ),
        "findings": [
            {"code": f.code, "severity": f.severity.value, "message": f.message}
            for f in findings
        ],
        "rationale": decision.rationale,
        "scribe_note": scribe_note,
    }

    # Genuine suspension. The runner outside the graph resolves the packet
    # (fixture / stdin / dashboard) and resumes with a dict answer.
    answer = interrupt(packet)

    outcome, note, source = _unpack_answer(answer)

    if outcome == "HOLD":
        return {
            "human_outcome": None,
            "human_note": note,
            "human_queued": True,
            "nodes_fired": [f"human_gate:{source}_hold"],
        }
    return {
        "human_outcome": outcome,
        "human_note": note,
        "nodes_fired": [f"human_gate:{source}_{outcome.lower()}"],
    }


def _unpack_answer(answer) -> tuple[str, str, str]:
    """Normalize the resume value the runner sends back.

    Accepts: {"outcome": ..., "note": ..., "source": ...} — the canonical
    shape. Also tolerates a bare string ("APPROVE" / "REJECT" / "HOLD")
    for callers that don't want to construct a dict. Anything else
    defaults to HOLD so the run stays resumable rather than settling on
    ambiguous input."""
    if isinstance(answer, dict):
        outcome = str(answer.get("outcome", "")).upper()
        note = str(answer.get("note") or "")
        source = str(answer.get("source") or "unknown")
    elif isinstance(answer, str):
        outcome = answer.upper()
        note = ""
        source = "unknown"
    else:
        outcome, note, source = "", "", "unknown"

    if outcome not in VALID_HUMAN_OUTCOMES:
        outcome = "HOLD"
    return outcome, note, source
