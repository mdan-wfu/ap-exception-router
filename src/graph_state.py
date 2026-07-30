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
from typing import Annotated, TypedDict

from src.schema import Decision, Finding, Invoice, ModelCall, Outcome, ToolCall


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

    # Terminal
    terminal_status: Outcome | None
    failure_reason: str | None
