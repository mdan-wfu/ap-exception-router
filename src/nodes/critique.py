"""Critic node — argues the opposite side of the Adjudicator's decision.

Produces a challenge, not a verdict. The Adjudicator on the next turn
weighs the challenge and either revises or holds; both are valid.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from src.graph_state import GraphState
from src.llm.agent_loop import (
    MAX_INVESTIGATION_TURNS,
    format_tool_history,
    run_agent_loop,
)
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
    prior_investigation = format_tool_history(state.get("tool_calls", []))
    tool_cache_in = state.get("tool_result_cache", {})

    context = _build_context(invoice, findings, decision, prior_investigation)
    prompt = _load_prompt().replace("<<CONTEXT>>", context)

    agent_result = run_agent_loop(
        prompt, CriticOutput, prompt_name=f"critic_round_{round_num}",
        tool_cache=tool_cache_in,
    )

    if agent_result.parsed is None:
        challenge = (
            f"Critic investigation cap ({MAX_INVESTIGATION_TURNS}) reached "
            f"without a structured challenge; the Adjudicator's decision stands."
        )
    else:
        challenge = agent_result.parsed.challenge

    return {
        "critic_challenges": [challenge],
        "critic_rounds": round_num,
        "model_calls": agent_result.model_calls,
        "tool_calls": agent_result.tool_calls,
        "tool_result_cache": agent_result.tool_cache,
        "nodes_fired": [f"critique:round_{round_num}"],
    }


def _build_context(invoice, findings, decision, prior_investigation: str) -> str:
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
    if prior_investigation:
        parts.append("")
        parts.append("### Prior investigation (do NOT re-run these tool calls)")
        parts.append(
            "The Adjudicator already ran these tool calls. Their results are "
            "in context below. Do NOT re-call the same tool with the same "
            "arguments — the answer will be identical. Reuse. You may call "
            "tools for genuinely NEW questions not yet answered."
        )
        parts.append("")
        parts.append(prior_investigation)
    return "\n".join(parts)
