"""LangGraph state shape.

Two design requirements documented in Phase 5a:

  1. `findings` and `nodes_fired` accumulate via LangGraph's reducer
     annotation. Multiple nodes contribute; contributions merge, they
     don't clobber. Concurrent branches (a Phase 5b tool call in parallel
     with something else) would otherwise overwrite each other's findings.

  2. `Invoice` is frozen. Nothing in the graph mutates it. A node needing
     a modified invoice produces a new one via `Invoice.model_copy(update=...)`
     and replaces the state field.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from src.schema import Decision, Finding, Invoice, ModelCall, Outcome, ToolCall


def _merge_tool_cache(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Union of two tool-result caches. Later values win on conflict — they
    should be identical anyway since tools are pure functions of their args
    and the invariant reference DB within a single run."""
    if not a:
        return dict(b) if b else {}
    if not b:
        return dict(a)
    return {**a, **b}


class GraphState(TypedDict, total=False):
    # Inputs
    source_path: str

    # Extraction stage
    adapter_used: str            # "json"|"csv"|"xml"|"text"|"pdf"
    llm_fallback: bool
    fallback_reason: str | None
    invoice: Invoice | None

    # Findings — additive across nodes via operator.add reducer
    findings: Annotated[list[Finding], operator.add]

    # §2.2 hard guardrail predicate — set by policy_gate, read by routing
    has_critical: bool

    # Adjudicator / critic tracking
    decision: Decision | None
    critic_rounds: int
    critic_challenges: Annotated[list[str], operator.add]
    revision_occurred: bool
    guardrail_override_fired: bool
    guardrail_override_reason: str | None

    # Scribe output — the human-facing note (None on APPROVE)
    scribe_note: str | None

    # Trace — one entry per node in the order they fired
    nodes_fired: Annotated[list[str], operator.add]
    # LLM + tool call traces accumulate across every node that uses them
    model_calls: Annotated[list[ModelCall], operator.add]
    tool_calls: Annotated[list[ToolCall], operator.add]

    # Tool-result cache, shared across every agent node within one run.
    # Key: `${tool_name}::${sorted-args-json}`. Value: the tool result dict.
    # Prevents re-executing an identical lookup across the initial adjudicator,
    # critic rounds, and revised adjudicator passes. Beyond cost, an uncached
    # tool could theoretically return different answers to the same question
    # inside one decision — the cache eliminates that class of incoherence.
    tool_result_cache: Annotated[dict[str, Any], _merge_tool_cache]

    # Terminal
    terminal_status: Outcome | None
    failure_reason: str | None
