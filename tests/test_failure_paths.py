"""Failure paths produce structured records, not stack traces.

Three paths, all pre-Phase-8 in origin — this file locks in the observability
contract: a reviewer or downstream system can tell exactly which cap tripped,
which cassette was missing, or why extraction failed, without reading a
Python traceback.
"""
from __future__ import annotations

import pytest

from src.llm.provider import CacheMissError
from src.llm.cassette import CassetteStore
from src.llm.agent_loop import CircuitBreakerTripped


# --------------------------------------------------------------------------
# Cache-miss error identifies the key AND provides a fix hint
# --------------------------------------------------------------------------

def test_cache_miss_error_message_contains_key_and_fix_hint(tmp_path):
    """Replay-mode cache miss must name the missing key and tell the reader
    how to record it. This is what makes the Phase 6 cold-clone debug loop
    tractable — you can grep the key against data/cassettes/."""
    from src.llm.provider import LLMProvider
    store = CassetteStore(root=tmp_path)
    provider = LLMProvider(
        api_key="test-key", model="grok-4.5", mode="replay",
        cassette_store=store,
    )
    with pytest.raises(CacheMissError) as exc:
        provider.chat(messages=[{"role": "user", "content": "unrecorded prompt"}])
    msg = str(exc.value)
    # Full 64-char SHA-256 present so a reviewer can grep for it
    import re
    keys = re.findall(r"\b[0-9a-f]{64}\b", msg)
    assert len(keys) == 1, f"expected one hex key in error, got {keys}"
    # Fix hint present
    assert "--live" in msg or "LLM_MODE=live" in msg


# --------------------------------------------------------------------------
# Circuit-breaker error identifies which cap and which prompt_name
# --------------------------------------------------------------------------

def test_circuit_breaker_model_cap_names_cap_and_prompt():
    """Message must name (a) the cap value and (b) the prompt_name that
    was about to fire when the cap tripped. Batch runs use this to attribute
    an aborted invoice to the right layer."""
    from src.llm.agent_loop import run_agent_loop
    import src.llm.agent_loop as al
    from pydantic import BaseModel

    class Dummy(BaseModel):
        outcome: str = "x"

    old_cap = al.MAX_MODEL_CALLS_PER_INVOICE
    al.MAX_MODEL_CALLS_PER_INVOICE = 0
    try:
        with pytest.raises(CircuitBreakerTripped) as exc:
            run_agent_loop(
                initial_prompt="test", response_schema=Dummy,
                prompt_name="critic_round_1",
                tool_cache={}, invoice_tool_calls_used=0,
                invoice_model_calls_used=0,
            )
    finally:
        al.MAX_MODEL_CALLS_PER_INVOICE = old_cap
    msg = str(exc.value)
    assert "model-call cap" in msg
    assert "critic_round_1" in msg
    assert "(0)" in msg


def test_circuit_breaker_tool_cap_names_cap_and_prompt():
    """Tool-cap trip fires from inside the investigation loop AFTER the
    model returned tool_calls. Simulate by installing a provider that
    returns one tool call, then verify the trip message names the cap
    and prompt_name."""
    from src.llm.agent_loop import run_agent_loop, set_provider, get_provider
    import src.llm.agent_loop as al
    from unittest.mock import MagicMock
    from pydantic import BaseModel

    class Dummy(BaseModel):
        outcome: str = "x"

    fake = MagicMock()
    result = MagicMock()
    result.content = ""
    result.parsed = None
    result.tool_calls = [{"id": "1", "name": "get_policy",
                          "arguments": {"finding_code": "TM-001"}}]
    result.cache_hit = False
    from datetime import datetime, timezone
    from src.schema import ModelCall
    result.model_call = ModelCall(
        requested_model="grok-4.5", resolved_model="grok-4.5",
        prompt_tokens=1, cached_prompt_tokens=0, completion_tokens=1,
        reasoning_tokens=0, latency_ms=1.0,
        timestamp=datetime.now(timezone.utc),
    )
    fake.chat.return_value = result

    orig = None
    try:
        orig = get_provider()
    except Exception:
        pass
    set_provider(fake)

    old_cap = al.MAX_TOOL_CALLS_PER_INVOICE
    al.MAX_TOOL_CALLS_PER_INVOICE = 0
    try:
        with pytest.raises(CircuitBreakerTripped) as exc:
            run_agent_loop(
                initial_prompt="test", response_schema=Dummy,
                prompt_name="adjudicator",
                tool_cache={}, invoice_tool_calls_used=0,
                invoice_model_calls_used=0,
            )
    finally:
        al.MAX_TOOL_CALLS_PER_INVOICE = old_cap
        if orig is not None:
            set_provider(orig)
    msg = str(exc.value)
    assert "tool-call cap" in msg
    assert "adjudicator" in msg
    assert "(0)" in msg


# --------------------------------------------------------------------------
# Extraction failure produces a FAILED run row, not a stack trace
# --------------------------------------------------------------------------

def test_route_outcome_returns_failed_when_no_decision():
    """No Adjudicator decision (e.g. graph short-circuited before adjudicate)
    lands as terminal_status=FAILED with a reason — never crashes the graph.
    The reason string must be non-empty so a reviewer can read it in the
    JSONL log without opening the audit DB."""
    from src.nodes.route_outcome import route_outcome
    from src.schema import Outcome
    result = route_outcome({"invoice": None, "decision": None})
    assert result["terminal_status"] == Outcome.FAILED
    assert result["failure_reason"]
    assert "no Adjudicator decision" in result["failure_reason"]


def test_route_outcome_failed_row_persists_when_invoice_present(tmp_path, monkeypatch):
    """When an Invoice exists in state but no decision, the FAILED status
    reaches the audit store — so a Phase 11 dashboard can surface the run
    rather than silently dropping it."""
    from src.store import audit as audit_mod
    from src import config as cfg_mod
    isolated = tmp_path / "audit.sqlite"
    monkeypatch.setattr(cfg_mod, "AUDIT_DB_PATH", isolated)
    monkeypatch.setattr(audit_mod, "AUDIT_DB_PATH", isolated)

    # Use a fixture invoice from the corpus rather than hand-constructing one.
    from src.adapters.router import extract as router_extract
    from pathlib import Path
    inv = router_extract(Path("data/invoices/invoice_1001.txt")).invoice

    from src.nodes.route_outcome import route_outcome
    from src.schema import Outcome
    state = {
        "invoice": inv, "decision": None, "findings": [], "nodes_fired": [],
        "model_calls": [], "tool_calls": [], "critic_challenges": [],
    }
    result = route_outcome(state)
    assert result["terminal_status"] == Outcome.FAILED

    import sqlite3
    conn = sqlite3.connect(str(isolated))
    row = conn.execute(
        "SELECT terminal_status, failure_reason FROM runs WHERE invoice_number=?",
        (inv.invoice_number,),
    ).fetchone()
    assert row is not None, "FAILED status did not reach the audit store"
    assert row[0] == "FAILED"
    assert "no Adjudicator decision" in row[1]
