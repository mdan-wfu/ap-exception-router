"""Scribe node — human-facing note for ESCALATE / REJECT invoices.

APPROVE outcomes get no note. The Scribe reads invoice + findings +
decision and writes one short paragraph modeled on:

    "Line 3 requests 20 GadgetX; 5 in stock. Vendor has no prior orders.
     Recommend hold pending vendor confirmation."

No tools. No structured retries. If the model returns garbage, the node
records that fact rather than crashing.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from src.graph_state import GraphState
from src.llm import agent_loop as _agent_loop_mod
from src.llm.agent_loop import CircuitBreakerTripped, get_provider
from src.nodes.adjudicate import _invoice_summary
from src.schema import Outcome

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "scribe.md"
_PROMPT: str | None = None


class ScribeOutput(BaseModel):
    note: str


def _load_prompt() -> str:
    global _PROMPT
    if _PROMPT is None:
        _PROMPT = PROMPT_PATH.read_text()
    return _PROMPT


def scribe(state: GraphState) -> dict:
    decision = state.get("decision")
    if decision is None or decision.outcome == Outcome.APPROVE:
        return {"nodes_fired": ["scribe:skipped"]}

    used = len(state.get("model_calls", []))
    cap = _agent_loop_mod.MAX_MODEL_CALLS_PER_INVOICE   # read at call time
    if used >= cap:
        raise CircuitBreakerTripped(
            f"per-invoice model-call cap ({cap}) tripped in scribe; "
            f"{used} model calls already made."
        )

    invoice = state["invoice"]
    findings = state.get("findings", [])

    context = _build_context(invoice, findings, decision)
    prompt = _load_prompt().replace("<<CONTEXT>>", context)

    provider = get_provider()
    result = provider.chat(
        [{"role": "user", "content": prompt}],
        response_schema=ScribeOutput,
        prompt_name="scribe",
    )

    if isinstance(result.parsed, ScribeOutput):
        note = result.parsed.note.strip()
    else:
        note = "(scribe unable to produce a note)"

    return {
        "scribe_note": note,
        "model_calls": [result.model_call],
        "nodes_fired": ["scribe"],
    }


def _build_context(invoice, findings, decision) -> str:
    return "\n".join([
        "### Invoice",
        json.dumps(_invoice_summary(invoice), indent=2, default=str),
        "",
        "### Findings",
        *(
            [f"- {f.code} [{f.severity.value}] {f.message}" for f in findings]
            if findings else ["(none)"]
        ),
        "",
        "### Adjudicator decision",
        f"outcome: {decision.outcome.value}",
        f"rationale: {decision.rationale}",
    ])
