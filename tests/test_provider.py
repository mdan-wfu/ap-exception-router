"""LLMProvider: record/replay round-trip, mode dispatch, retry classification."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from openai import (
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from src.llm.cassette import CassetteStore
from src.llm.provider import (
    CacheMissError,
    LLMCallFailed,
    LLMProvider,
    _call_with_retry,
)
from src.schema import ModelCall


# ---------------------------------------------------------------------------
# Helpers for mocking OpenAI responses and exceptions
# ---------------------------------------------------------------------------

def _fake_response(
    content: str = "hello",
    tool_calls: list | None = None,
    resolved_model: str = "grok-4.5-2026-07-01",
    prompt_tokens: int = 12,
    completion_tokens: int = 34,
    cached_prompt_tokens: int = 0,
    reasoning_tokens: int = 0,
    system_fingerprint: str | None = "fp_abcdef01",
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.message = msg
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    prompt_details = MagicMock()
    prompt_details.cached_tokens = cached_prompt_tokens
    usage.prompt_tokens_details = prompt_details
    completion_details = MagicMock()
    completion_details.reasoning_tokens = reasoning_tokens
    usage.completion_tokens_details = completion_details
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    response.model = resolved_model
    response.system_fingerprint = system_fingerprint
    return response


def _fake_client(*, side_effects=None, return_value=None) -> MagicMock:
    client = MagicMock()
    if side_effects is not None:
        client.chat.completions.create.side_effect = side_effects
    else:
        client.chat.completions.create.return_value = return_value or _fake_response()
    return client


# Subclass openai exceptions to bypass their __init__ (which needs httpx types)
class _FakeRateLimit(RateLimitError):
    def __init__(self, msg="rate limited"):
        Exception.__init__(self, msg)


class _FakeTimeout(APITimeoutError):
    def __init__(self, msg="timeout"):
        Exception.__init__(self, msg)


class _Fake500(InternalServerError):
    def __init__(self, msg="upstream error"):
        Exception.__init__(self, msg)


class _FakeAuth(AuthenticationError):
    def __init__(self, msg="bad key"):
        Exception.__init__(self, msg)


class _FakeBadRequest(BadRequestError):
    def __init__(self, msg="bad body"):
        Exception.__init__(self, msg)


# ---------------------------------------------------------------------------
# Record + replay round trip
# ---------------------------------------------------------------------------

def test_record_then_replay_round_trip(tmp_path: Path, monkeypatch) -> None:
    """A live call records; a subsequent request with the same fingerprint
    returns identical content and identical ModelCall metadata."""
    monkeypatch.setattr("time.sleep", lambda *_: None)

    store = CassetteStore(root=tmp_path)
    client = _fake_client(return_value=_fake_response(
        content="RECORDED CONTENT",
        resolved_model="grok-4.5-dated",
        prompt_tokens=42,
        cached_prompt_tokens=10,
        completion_tokens=99,
        reasoning_tokens=200,
    ))
    provider = LLMProvider(
        api_key="dummy", model="grok-4.5", mode="live",
        cassette_store=store, client=client,
    )

    messages = [{"role": "user", "content": "hi"}]
    first = provider.chat(messages)

    assert first.cache_hit is False
    assert first.content == "RECORDED CONTENT"
    assert first.model_call.resolved_model == "grok-4.5-dated"
    assert first.model_call.prompt_tokens == 42
    assert first.model_call.cached_prompt_tokens == 10
    assert first.model_call.completion_tokens == 99
    assert first.model_call.reasoning_tokens == 200
    assert first.model_call.latency_ms > 0

    # Switch to replay mode. The next call must not hit the API and must
    # return byte-identical content + metadata from the cassette.
    provider2 = LLMProvider(
        api_key="dummy", model="grok-4.5", mode="replay",
        cassette_store=store, client=MagicMock(),  # would explode if used
    )
    second = provider2.chat(messages)

    assert second.cache_hit is True
    assert second.content == first.content
    assert second.model_call.resolved_model == first.model_call.resolved_model
    assert second.model_call.prompt_tokens == first.model_call.prompt_tokens
    assert second.model_call.cached_prompt_tokens == first.model_call.cached_prompt_tokens
    assert second.model_call.completion_tokens == first.model_call.completion_tokens
    assert second.model_call.reasoning_tokens == first.model_call.reasoning_tokens
    assert second.model_call.latency_ms == first.model_call.latency_ms
    assert second.model_call.system_fingerprint == first.model_call.system_fingerprint


def test_system_fingerprint_captured_from_response(tmp_path: Path) -> None:
    """system_fingerprint is the only field that would detect a silent alias remap."""
    store = CassetteStore(root=tmp_path)
    client = _fake_client(return_value=_fake_response(system_fingerprint="fp_xyz123"))
    provider = LLMProvider(
        api_key="dummy", model="grok-4.5", mode="live",
        cassette_store=store, client=client,
    )
    result = provider.chat([{"role": "user", "content": "hi"}])
    assert result.model_call.system_fingerprint == "fp_xyz123"


def test_system_fingerprint_none_when_absent(tmp_path: Path) -> None:
    """Providers that omit system_fingerprint must not break the pipeline."""
    store = CassetteStore(root=tmp_path)
    client = _fake_client(return_value=_fake_response(system_fingerprint=None))
    provider = LLMProvider(
        api_key="dummy", model="grok-4.5", mode="live",
        cassette_store=store, client=client,
    )
    result = provider.chat([{"role": "user", "content": "hi"}])
    assert result.model_call.system_fingerprint is None


def test_prompt_edit_forces_new_live_call(tmp_path: Path) -> None:
    """A single-char change to the prompt must produce a cache miss even in
    replay+existing-cassette scenarios."""
    store = CassetteStore(root=tmp_path)
    client = _fake_client(return_value=_fake_response("first response"))
    provider = LLMProvider(
        api_key="dummy", model="grok-4.5", mode="auto",
        cassette_store=store, client=client,
    )
    provider.chat([{"role": "user", "content": "hello"}])

    # Now edit the prompt by one character. Auto mode should call live again.
    client.chat.completions.create.return_value = _fake_response("second response")
    result = provider.chat([{"role": "user", "content": "hello."}])
    assert result.content == "second response"
    assert client.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
# Replay mode with missing key must raise
# ---------------------------------------------------------------------------

def test_replay_missing_key_raises(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path)
    client = MagicMock()  # if provider calls this we've silently ignored replay mode
    provider = LLMProvider(
        api_key="dummy", model="grok-4.5", mode="replay",
        cassette_store=store, client=client,
    )
    with pytest.raises(CacheMissError):
        provider.chat([{"role": "user", "content": "no cassette exists for this"}])
    client.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# Retry classification
# ---------------------------------------------------------------------------

def test_retry_on_rate_limit_then_succeed(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda *_: None)  # skip backoff
    client = _fake_client(side_effects=[_FakeRateLimit(), _fake_response("ok")])
    response, _latency = _call_with_retry(client, {"model": "grok-4.5", "messages": []})
    assert response.choices[0].message.content == "ok"
    assert client.chat.completions.create.call_count == 2


def test_retry_on_timeout_then_succeed(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda *_: None)
    client = _fake_client(side_effects=[_FakeTimeout(), _fake_response("ok")])
    response, _ = _call_with_retry(client, {"model": "grok-4.5", "messages": []})
    assert response.choices[0].message.content == "ok"


def test_retry_on_5xx_then_succeed(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda *_: None)
    client = _fake_client(side_effects=[_Fake500(), _fake_response("ok")])
    response, _ = _call_with_retry(client, {"model": "grok-4.5", "messages": []})
    assert response.choices[0].message.content == "ok"


def test_retry_exhaustion_raises_llmcallfailed(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda *_: None)
    client = _fake_client(side_effects=[_FakeRateLimit(), _FakeRateLimit(), _FakeRateLimit()])
    with pytest.raises(LLMCallFailed):
        _call_with_retry(client, {"model": "grok-4.5", "messages": []})
    assert client.chat.completions.create.call_count == 3


def test_no_retry_on_auth_error(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda *_: None)
    client = _fake_client(side_effects=[_FakeAuth()])
    with pytest.raises(LLMCallFailed):
        _call_with_retry(client, {"model": "grok-4.5", "messages": []})
    # Must NOT have retried a second time
    assert client.chat.completions.create.call_count == 1


def test_no_retry_on_bad_request(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda *_: None)
    client = _fake_client(side_effects=[_FakeBadRequest()])
    with pytest.raises(LLMCallFailed):
        _call_with_retry(client, {"model": "grok-4.5", "messages": []})
    assert client.chat.completions.create.call_count == 1
