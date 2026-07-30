"""Critic node — argues the opposite side of the Adjudicator's decision.

Produces a challenge, not a verdict. The Adjudicator on the next turn
weighs the challenge and either revises or holds; both are valid.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from src.graph_state import GraphState
from src.nodes._llm_turn import MAX_TOOL_TURNS, run_llm_with_tools
from src.nodes.adjudicate import _invoice_summary

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "critic.md"
_PROMPT: str | None = None


class CriticOutput(BaseModel):
    challenge: str
    proposed_outcome: str | None = None
    cites_finding_codes: list[str] = Field(default_factory=list)


def _load_prompt() -> str:
    global _PROMPT
    if _PROMPT is None:
        _PROMPT = PROMPT_PATH.read_text()
    return _PROMPT


def critique(state: GraphState) -> dict:
    invoice = state["invoice"]
    findings = state.get("findings", [])
    decision = state.get("decision")
    round_num = state.get("critic_rounds", 0) + 1

    context = _build_context(invoice, findings, decision)
    prompt = _load_prompt().replace("<<CONTEXT>>", context)

    turn_result = run_llm_with_tools(
        prompt, CriticOutput, prompt_name=f"critic_round_{round_num}",
    )

    if turn_result.parsed is None:
        challenge = (
            f"Critic could not produce a structured challenge within "
            f"{MAX_TOOL_TURNS} turns; the Adjudicator's decision stands unchallenged."
        )
    else:
        challenge = turn_result.parsed.challenge

    return {
        "critic_challenges": [challenge],
        "critic_rounds": round_num,
        "model_calls": turn_result.model_calls,
        "tool_calls": turn_result.tool_calls,
        "nodes_fired": [f"critique:round_{round_num}"],
    }


def _build_context(invoice, findings, decision) -> str:
    parts = [
        "### Adjudicator decision",
        json.dumps({
            "outcome": decision.outcome.value if decision else None,
            "rationale": decision.rationale if decision else None,
            "confidence": decision.confidence if decision else None,
        }, indent=2),
        "",
        "### Invoice",
        json.dumps(_invoice_summary(invoice), indent=2, default=str),
        "",
        "### Findings",
    ]
    if not findings:
        parts.append("(none)")
    else:
        for f in findings:
            parts.append(f"- {f.code} [{f.severity.value}] {f.message}")
    return "\n".join(parts)
