"""xAI Grok provider via the OpenAI-compatible SDK.

Thin. Not an abstraction framework. The single entry point is
`LLMProvider.chat(messages, response_schema=None, tools=None, prompt_name=None)`.

Constraints (confirmed in Phase 0 probe, see DECISIONS.md):
  - `max_completion_tokens` (never `max_tokens`)
  - no `stop` / `presence_penalty` / `frequency_penalty`
  - no streaming
  - response-format schemas must contain no `minLength`/`maxLength`/
    `minItems`/`maxItems`/`pattern`

The Claude path is deliberately not implemented. Per CLAUDE.md §3, Grok is
the only provider used during development — prompts tuned against one model
and executed against another fail silently, returning valid JSON with subtly
wrong values.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, Field

from src.config import (
    GROK_MODEL,
    LLM_MODE,
    XAI_API_KEY,
)
from src.schema import ModelCall

if TYPE_CHECKING:
    from src.llm.cassette import CassetteStore


BASE_URL = "https://api.x.ai/v1"
DEFAULT_TIMEOUT = 180.0        # seconds; reasoning models can be slow
DEFAULT_MAX_TOKENS = 4096
MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Exceptions — failure is explicit. Nothing returns None.
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base class for provider errors."""


class LLMCallFailed(LLMError):
    """A call exhausted retries or hit a non-retryable error."""


class CacheMissError(LLMError):
    """Replay mode encountered a request with no recorded cassette."""


# ---------------------------------------------------------------------------
# Result — what every chat() call returns.
# ---------------------------------------------------------------------------

class LLMResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: str
    parsed: Any = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model_call: ModelCall
    cache_hit: bool = False


# ---------------------------------------------------------------------------
# Retry classification
# ---------------------------------------------------------------------------

_RETRYABLE: tuple[type[Exception], ...] = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)


def _call_with_retry(
    client: Any,
    request: dict[str, Any],
    max_retries: int = MAX_RETRIES,
) -> tuple[Any, float]:
    """Execute the API call with exponential backoff.

    Retries on: APIConnectionError, APITimeoutError, RateLimitError, InternalServerError.
    Non-retryable (4xx auth/validation) raise on first occurrence as LLMCallFailed.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        started = time.perf_counter()
        try:
            response = client.chat.completions.create(**request)
            latency_ms = (time.perf_counter() - started) * 1000.0
            return response, latency_ms
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt == max_retries - 1:
                raise LLMCallFailed(
                    f"Retryable error persisted after {max_retries} attempts: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            time.sleep(min(2 ** attempt, 30))
        except Exception as exc:
            raise LLMCallFailed(
                f"Non-retryable error: {type(exc).__name__}: {exc}"
            ) from exc
    raise LLMCallFailed(f"Retry loop exhausted: {last_exc}") from last_exc  # unreachable


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class LLMProvider:
    """Single entry point for LLM calls. Wraps the OpenAI client, adds retry,
    cost/latency capture, and cassette dispatch."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        model: str | None = None,
        mode: str | None = None,
        cassette_store: "CassetteStore | None" = None,
        client: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.api_key = api_key or XAI_API_KEY
        self.base_url = base_url
        self.model = model or GROK_MODEL
        self.mode = mode or LLM_MODE
        self.cassette_store = cassette_store
        self.timeout = timeout
        self.max_tokens = max_tokens

        # A missing API key is only fatal for a live call. In replay mode we
        # never touch the network — the OpenAI client is constructed with a
        # placeholder key so cold-clone `make demo` (no .env) works. If a
        # live call is later attempted with the placeholder, xAI returns an
        # auth error which the retry classifier treats as non-retryable and
        # surfaces plainly.
        effective_key = self.api_key or "replay-mode-placeholder"
        self._client = client or OpenAI(
            api_key=effective_key, base_url=base_url, timeout=timeout,
        )

    # -- Public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        response_schema: type[BaseModel] | dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        prompt_name: str | None = None,
    ) -> LLMResult:
        request = self._build_request(messages, response_schema, tools)

        # Cassette dispatch
        if self.cassette_store is not None and self.mode in ("replay", "auto"):
            cached = self.cassette_store.get(request)
            if cached is not None:
                return self._result_from_cassette(cached, response_schema)
            if self.mode == "replay":
                key = self.cassette_store.compute_key(request)
                raise CacheMissError(
                    "This invoice has no recorded response — it has never "
                    "been run through the LLM against the current code. "
                    "That is the intended behavior in LLM_MODE=replay "
                    "(the committed 16 corpus invoices + 4 authored "
                    "adversarial invoices all replay from cassettes on "
                    "disk with no API calls).\n"
                    "\n"
                    "To process a new invoice you must:\n"
                    "  1. Put a valid xAI key in .env: XAI_API_KEY=xai-...\n"
                    "  2. Re-run with `--live`  (e.g. "
                    "`python main.py --invoice_path <PATH> --live`)\n"
                    "\n"
                    "This makes real API calls against your xAI account "
                    "and incurs cost (typically $0.01–0.10 per invoice, "
                    "capped by the per-invoice circuit breaker in "
                    "src/llm/agent_loop.py). Fresh cassettes get written "
                    "under data/cassettes/ so subsequent replay runs are "
                    "free.\n"
                    "\n"
                    f"Missing cassette key (SHA-256 of the request "
                    f"fingerprint, for debugging only): {key}"
                )

        response, latency_ms = _call_with_retry(self._client, request)
        result = self._result_from_response(
            response, latency_ms, response_schema, prompt_name
        )

        if self.cassette_store is not None:
            self.cassette_store.put(request, result)

        return result

    # -- Internal -----------------------------------------------------------

    def _build_request(
        self,
        messages: list[dict[str, Any]],
        response_schema: type[BaseModel] | dict[str, Any] | None,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        req: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": self.max_tokens,
        }
        if response_schema is not None:
            if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
                schema = response_schema.model_json_schema()
                name = response_schema.__name__
            else:
                schema = response_schema
                name = schema.get("title", "Response")
            req["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema},
            }
        if tools:
            req["tools"] = tools
            req["tool_choice"] = "auto"
        return req

    def _result_from_response(
        self,
        response: Any,
        latency_ms: float,
        response_schema: type[BaseModel] | dict[str, Any] | None,
        prompt_name: str | None,
    ) -> LLMResult:
        msg = response.choices[0].message
        content = msg.content or ""

        tool_calls: list[dict[str, Any]] = []
        raw_tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in raw_tool_calls:
            tool_calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments) if tc.function.arguments else {},
            })

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

        # Detail sub-objects: xAI populates these for cached prompt tokens and
        # reasoning tokens. Both default to 0 if the provider omits them.
        prompt_details = getattr(usage, "prompt_tokens_details", None) if usage else None
        completion_details = getattr(usage, "completion_tokens_details", None) if usage else None
        cached_prompt_tokens = getattr(prompt_details, "cached_tokens", 0) or 0 if prompt_details else 0
        reasoning_tokens = getattr(completion_details, "reasoning_tokens", 0) or 0 if completion_details else 0

        model_call = ModelCall(
            requested_model=self.model,
            resolved_model=getattr(response, "model", self.model),
            system_fingerprint=getattr(response, "system_fingerprint", None),
            prompt_name=prompt_name,
            prompt_tokens=prompt_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            latency_ms=latency_ms,
            timestamp=datetime.now(timezone.utc),
        )

        parsed = _try_parse(content, response_schema)

        return LLMResult(
            content=content,
            parsed=parsed,
            tool_calls=tool_calls,
            model_call=model_call,
            cache_hit=False,
        )

    def _result_from_cassette(
        self,
        cached: dict[str, Any],
        response_schema: type[BaseModel] | dict[str, Any] | None,
    ) -> LLMResult:
        model_call = ModelCall.model_validate(cached["model_call"])
        content = cached.get("content", "")
        tool_calls = cached.get("tool_calls", [])
        parsed = _try_parse(content, response_schema)
        return LLMResult(
            content=content,
            parsed=parsed,
            tool_calls=tool_calls,
            model_call=model_call,
            cache_hit=True,
        )


def _try_parse(
    content: str,
    response_schema: type[BaseModel] | dict[str, Any] | None,
) -> Any:
    """Best-effort parse. Returns None on failure — the Phase 3 repair loop
    is where invalid structured output gets fixed."""
    if not content or response_schema is None:
        return None
    try:
        if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
            return response_schema.model_validate_json(content)
        return json.loads(content)
    except Exception:
        return None
