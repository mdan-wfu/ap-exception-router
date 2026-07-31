"""Adjudicator node.

Loads prompts/adjudicator.md, calls the provider with the tool schemas
enabled, records every ModelCall and ToolCall into state. Applies the
CLAUDE.md §2.2 hard guardrail in code AFTER the model returns — an
Adjudicator that returns APPROVE on an invoice carrying a CRITICAL
finding is overridden to ESCALATE, and the override itself is recorded
so history is preserved.
"""
from __future__ import annotations

import json
from pathlib import Path

from pathlib import Path as _Path

from pydantic import BaseModel, Field

from src.graph_state import GraphState
from src.llm.agent_loop import (
    MAX_INVESTIGATION_TURNS,
    format_tool_history,
    run_agent_loop,
)
from src.schema import Decision, Invoice, Outcome
from src.validators import has_critical

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "adjudicator.md"
_PROMPT: str | None = None


class AdjudicatorOutput(BaseModel):
    outcome: str
    rationale: str
    confidence: float
    finding_codes_referenced: list[str] = Field(default_factory=list)


def _load_prompt() -> str:
    global _PROMPT
    if _PROMPT is None:
        _PROMPT = PROMPT_PATH.read_text()
    return _PROMPT


def adjudicate(state: GraphState) -> dict:
    invoice: Invoice = state["invoice"]
    findings = state.get("findings", [])
    critic_challenges = state.get("critic_challenges", [])
    prior_investigation = format_tool_history(state.get("tool_calls", []))
    tool_cache_in = state.get("tool_result_cache", {})

    context = _build_context(
        invoice, findings, critic_challenges, prior_investigation,
    )
    prompt = _load_prompt().replace("<<CONTEXT>>", context)

    is_revision = len(critic_challenges) > 0
    prompt_name = "adjudicator_revised" if is_revision else "adjudicator"

    agent_result = run_agent_loop(
        prompt, AdjudicatorOutput, prompt_name=prompt_name,
        tool_cache=tool_cache_in,
        invoice_tool_calls_used=len(state.get("tool_calls", [])),
        invoice_model_calls_used=len(state.get("model_calls", [])),
    )

    model_output = agent_result.parsed
    if model_output is None:
        decision = Decision(
            outcome=Outcome.ESCALATE,
            rationale=(
                f"Investigation cap ({MAX_INVESTIGATION_TURNS} tool turns) "
                f"reached without a structured decision after "
                f"{agent_result.investigation_turns} turn(s) and "
                f"{len(agent_result.tool_calls)} tool call(s). Escalating."
            ),
            confidence=0.3,
        )
    else:
        try:
            outcome = Outcome(model_output.outcome)
        except ValueError:
            outcome = Outcome.ESCALATE
        decision = Decision(
            outcome=outcome,
            rationale=model_output.rationale,
            confidence=model_output.confidence,
        )

    # Detect revision: on the second+ adjudicate call, compare against prior decision.
    revised = False
    if is_revision:
        prior = state.get("decision")
        if prior is not None and prior.outcome != decision.outcome:
            revised = True
        decision = decision.model_copy(update={
            "critic_challenge": critic_challenges[-1],
            "revised": revised,
        })

    # §2.2 hard guardrail — applied AFTER the model returns
    override_fired = False
    override_reason: str | None = None
    if decision.outcome == Outcome.APPROVE and has_critical(findings):
        override_fired = True
        override_reason = (
            "§2.2 guardrail: CRITICAL finding present; Adjudicator returned "
            "APPROVE and has been overridden to ESCALATE. Original rationale: "
            + decision.rationale
        )
        decision = decision.model_copy(update={
            "outcome": Outcome.ESCALATE,
            "rationale": override_reason,
        })

    return {
        "decision": decision,
        "model_calls": agent_result.model_calls,
        "tool_calls": agent_result.tool_calls,
        "tool_result_cache": agent_result.tool_cache,
        "revision_occurred": state.get("revision_occurred", False) or revised,
        "guardrail_override_fired": override_fired,
        "guardrail_override_reason": override_reason,
        "nodes_fired": ["adjudicate"],
    }


def _build_context(
    invoice: Invoice, findings,
    critic_challenges: list[str],
    prior_investigation: str,
) -> str:
    lines = []
    lines.append("### Invoice")
    inv_dict = _invoice_summary(invoice)
    lines.append(json.dumps(inv_dict, indent=2, default=str))
    lines.append("")
    lines.append("### Findings")
    if not findings:
        lines.append("(none)")
    else:
        for f in findings:
            lines.append(
                f"- {f.code} [{f.severity.value}] {f.message}"
                + (f"  (evidence: {f.evidence})" if f.evidence else "")
                + (f"  (at {f.field_path})" if f.field_path else "")
            )
    if prior_investigation:
        lines.append("")
        lines.append("### Prior investigation (do NOT re-run these tool calls)")
        lines.append(
            "The results below are ALREADY IN CONTEXT. Re-calling the same "
            "tool with the same arguments will return the identical result — "
            "reuse the facts here. You may still call tools for NEW questions "
            "not answered below."
        )
        lines.append("")
        lines.append(prior_investigation)
    if critic_challenges:
        lines.append("")
        lines.append("### Prior critic challenges")
        for i, c in enumerate(critic_challenges, start=1):
            lines.append(f"Round {i}: {c}")
    return "\n".join(lines)


def _invoice_summary(invoice: Invoice) -> dict:
    """Compact JSON view — only fields the Adjudicator needs."""
    return {
        "invoice_number": invoice.invoice_number,
        "invoice_number_raw": invoice.invoice_number_raw,
        "vendor_name": invoice.vendor_name,
        "vendor_raw": invoice.vendor_raw,
        "vendor_claims": invoice.vendor_claims,
        "vendor_address": invoice.vendor_address,
        "vendor_email": invoice.vendor_email,
        "invoice_date": invoice.invoice_date,
        "date_raw": invoice.date_raw,
        "due_date": invoice.due_date,
        "due_date_raw": invoice.due_date_raw,
        "payment_terms": invoice.payment_terms,
        "references": invoice.references,
        "notes": invoice.notes,
        "line_items": [
            {
                "raw_item_name": li.raw_item_name,
                "canonical_item": li.canonical_item,
                "quantity": li.quantity,
                "unit_price_usd": str(li.unit_price.amount_usd),
                "unit_price_native": (
                    f"{li.unit_price.amount_native} {li.unit_price.currency}"
                ),
                "line_amount_usd": (
                    str(li.line_amount.amount_usd) if li.line_amount else None
                ),
                "note": li.note,
            }
            for li in invoice.line_items
        ],
        "additional_charges": [
            {"label": ac.label, "amount_usd": str(ac.amount.amount_usd)}
            for ac in invoice.additional_charges
        ],
        "stated_subtotal_usd": (
            str(invoice.stated_subtotal.amount_usd) if invoice.stated_subtotal else None
        ),
        "stated_tax_usd": (
            str(invoice.stated_tax.amount_usd) if invoice.stated_tax else None
        ),
        "stated_total_usd": (
            str(invoice.stated_total.amount_usd) if invoice.stated_total else None
        ),
        "currency": (
            invoice.stated_total.currency if invoice.stated_total else "USD"
        ),
        # Basename only — cassette fingerprints must be machine-independent so
        # a cold clone in any location hits recorded plays. The absolute path
        # remains on the Invoice object for the audit store and dashboard.
        "source_file": _Path(invoice.source_file).name,
        "source_format": invoice.source_format,
        "corrections": [
            {
                "field_path": c.field_path,
                "original": c.original,
                "corrected": c.corrected,
                "reason": c.reason,
            }
            for c in invoice.corrections
        ],
    }
