"""Cassette store: key stability, prompt-edit-forces-miss, redaction."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.llm.cassette import CassetteStore, RedactionError, _KEY_PATTERN
from src.llm.provider import LLMResult
from src.schema import ModelCall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_request() -> dict:
    return {
        "model": "grok-4.5",
        "messages": [
            {"role": "system", "content": "You are an invoice extractor."},
            {"role": "user", "content": "Extract fields as JSON.\n\nINV-1001\n..."},
        ],
        "tools": None,
        "response_format": None,
        "max_completion_tokens": 500,
    }


def _fake_result(content: str = "hello") -> LLMResult:
    mc = ModelCall(
        requested_model="grok-4.5",
        resolved_model="grok-4.5-resolved",
        prompt_name=None,
        tokens_in=10,
        tokens_out=20,
        latency_ms=123.4,
        cost_usd=Decimal("0.0001"),
        timestamp=datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc),
    )
    return LLMResult(content=content, tool_calls=[], model_call=mc)


# ---------------------------------------------------------------------------
# Key stability + prompt-edit sensitivity
# ---------------------------------------------------------------------------

def test_key_is_stable_for_identical_request(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path)
    req_a = _base_request()
    req_b = _base_request()
    assert store.compute_key(req_a) == store.compute_key(req_b)


def test_prompt_edit_forces_cache_miss(tmp_path: Path) -> None:
    """Single-character edit to the user prompt must change the key."""
    store = CassetteStore(root=tmp_path)

    req_original = _base_request()
    req_edited = _base_request()
    # One character difference in the user content
    req_edited["messages"][1]["content"] = req_edited["messages"][1]["content"] + "."

    key_original = store.compute_key(req_original)
    key_edited = store.compute_key(req_edited)

    assert key_original != key_edited, (
        "prompt text must be in the cassette key, otherwise editing "
        "prompts/extractor.md returns a stale cached response"
    )


def test_system_prompt_edit_forces_cache_miss(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path)
    req_a = _base_request()
    req_b = _base_request()
    req_b["messages"][0]["content"] = "You are an invoice extractor and pedant."
    assert store.compute_key(req_a) != store.compute_key(req_b)


def test_tool_change_forces_cache_miss(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path)
    req_a = _base_request()
    req_b = _base_request()
    req_b["tools"] = [
        {"type": "function", "function": {"name": "get_vendor_record", "parameters": {}}}
    ]
    assert store.compute_key(req_a) != store.compute_key(req_b)


def test_max_tokens_change_does_not_force_cache_miss(tmp_path: Path) -> None:
    """max_completion_tokens is intentionally excluded from the key."""
    store = CassetteStore(root=tmp_path)
    req_a = _base_request()
    req_b = _base_request()
    req_b["max_completion_tokens"] = 999
    assert store.compute_key(req_a) == store.compute_key(req_b)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_get_returns_none_on_miss(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path)
    assert store.get(_base_request()) is None


def test_put_then_get_round_trips(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path)
    req = _base_request()
    result = _fake_result("extracted content")
    store.put(req, result)

    payload = store.get(req)
    assert payload is not None
    assert payload["content"] == "extracted content"
    assert payload["model_call"]["resolved_model"] == "grok-4.5-resolved"
    assert payload["model_call"]["tokens_in"] == 10
    assert payload["model_call"]["tokens_out"] == 20
    assert payload["model_call"]["latency_ms"] == 123.4


def test_cassette_filename_is_the_key(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path)
    req = _base_request()
    store.put(req, _fake_result())
    key = store.compute_key(req)
    assert (tmp_path / f"{key}.json").exists()


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def test_put_rejects_credential_in_response(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path)
    leaky_result = _fake_result("here is my key: xai-J7jSzIKorBop9ZxJNwH0Pv")
    with pytest.raises(RedactionError):
        store.put(_base_request(), leaky_result)


def test_put_rejects_credential_in_request_messages(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path)
    req = _base_request()
    req["messages"][1]["content"] = "leaked: xai-J7jSzIKorBop9ZxJNwH0Pv"
    with pytest.raises(RedactionError):
        store.put(req, _fake_result())


def test_scan_for_credentials_finds_nothing_in_clean_store(tmp_path: Path) -> None:
    store = CassetteStore(root=tmp_path)
    store.put(_base_request(), _fake_result("nothing to hide here"))
    assert store.scan_for_credentials() == []


def test_committed_cassettes_contain_no_credentials() -> None:
    """The real committed cassette dir must never contain an xAI key."""
    store = CassetteStore()  # default = data/cassettes
    offenders = store.scan_for_credentials()
    assert offenders == [], f"committed cassettes with credentials: {offenders}"


def test_key_pattern_matches_real_shape() -> None:
    """Sanity: the regex actually matches the .env key shape."""
    assert _KEY_PATTERN.search("prefix xai-J7jSzIKorBop9ZxJNwH0Pv suffix")
    assert not _KEY_PATTERN.search("this is just prose without credentials")
    assert not _KEY_PATTERN.search("xai-shrt")  # too short
