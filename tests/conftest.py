"""Shared pytest fixtures.

Notably: the graph-integration tests in `test_graph.py` traverse the full
pipeline including the LLM agent nodes (adjudicate / critique / scribe).
Live LLM calls in the test suite are undesirable; the tests are checking
graph STRUCTURE (node sequence, critic firing, finding accumulation, batch
dedup) rather than the LLM's decision quality.

`graph_llm_fake` (autouse in test_graph.py) installs a fake provider that
returns valid parsed responses for each of the three agent schemas so the
graph runs to completion without touching the network. Cassette hits are
still preferred (extraction cassettes stay live via replay), but any
agent-node call falls back to this in-process stub.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def _fake_model_call():
    from src.schema import ModelCall
    return ModelCall(
        requested_model="grok-4.5", resolved_model="grok-4.5",
        prompt_tokens=100, cached_prompt_tokens=0,
        completion_tokens=20, reasoning_tokens=50,
        latency_ms=1.0,
        timestamp=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )


class _GraphFakeProvider:
    """Returns a valid parsed response for whichever schema the caller asks for.

    - AdjudicatorOutput → ESCALATE with a fixed rationale
    - CriticOutput      → empty challenge
    - ScribeOutput      → short static note
    - Any other         → parsed=None (agent loop escalates as designed)

    Ignores tools entirely — no tool_calls returned, so the investigation
    phase terminates on turn 1 for every agent, keeping graph traversal
    predictable for structural assertions.
    """

    def chat(self, messages, response_schema=None, tools=None, prompt_name=None):
        result = MagicMock()
        result.content = ""
        result.tool_calls = []
        result.cache_hit = False
        result.model_call = _fake_model_call()

        if response_schema is None:
            # Investigation-phase call (tools active, no schema). Return a bare
            # response so the loop moves to synthesis on the next call.
            result.parsed = None
            return result

        parsed = _synth_for(response_schema)
        result.parsed = parsed
        return result


def _synth_for(schema_cls):
    """Construct a minimal valid instance for the three known agent schemas."""
    from src.nodes.adjudicate import AdjudicatorOutput
    from src.nodes.critique import CriticOutput
    from src.nodes.scribe import ScribeOutput

    if schema_cls is AdjudicatorOutput:
        return AdjudicatorOutput(
            outcome="ESCALATE",
            rationale="[fake provider] structural test — content not evaluated",
            confidence=0.5,
            finding_codes_referenced=[],
        )
    if schema_cls is CriticOutput:
        return CriticOutput(
            challenge="[fake provider] structural test — no real challenge",
            proposed_outcome=None,
        )
    if schema_cls is ScribeOutput:
        return ScribeOutput(note="Structural test — fake scribe note.")
    return None


@pytest.fixture
def graph_llm_fake(monkeypatch, tmp_path):
    """Install the fake provider, raise circuit-breaker caps, force the
    human gate into demo mode, and isolate the audit store for the duration
    of one test.

    Isolation matters: the graph now writes to the audit store in route_outcome
    and settle. Without a per-test store, tests would accumulate state across
    runs and leak into `get_prior_invoice` results.
    """
    from src.llm import agent_loop
    monkeypatch.setattr(agent_loop, "MAX_MODEL_CALLS_PER_INVOICE", 999)
    monkeypatch.setattr(agent_loop, "MAX_TOOL_CALLS_PER_INVOICE", 999)

    # Force demo mode so human_gate never blocks on stdin
    monkeypatch.setenv("HUMAN_GATE_MODE", "demo")

    # Isolate the audit store to a temp file per test
    from src.config import AUDIT_DB_PATH
    from src import config as cfg_mod
    from src.store import audit as audit_mod
    isolated_db = tmp_path / "audit.sqlite"
    monkeypatch.setattr(cfg_mod, "AUDIT_DB_PATH", isolated_db)
    monkeypatch.setattr(audit_mod, "AUDIT_DB_PATH", isolated_db)

    original = None
    try:
        original = agent_loop.get_provider()
    except Exception:
        original = None
    agent_loop.set_provider(_GraphFakeProvider())
    yield
    agent_loop.set_provider(original) if original is not None else None
