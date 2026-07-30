"""Multi-turn agent loop for tool-using nodes.

Two phases:

  1. INVESTIGATION — up to `max_investigation_turns` calls. Tools passed,
     response_format NOT passed. The model may call tools; results are
     executed against the Phase 5b registry, appended as tool-role messages,
     and the model gets another turn. When the model returns text without
     tool_calls, investigation is done.

  2. SYNTHESIS — one final call. response_format passed, tools NOT passed.
     "Given your investigation, produce the final structured answer."

Why two phases: when both `tools` and `response_format=json_schema` are
active on the same turn, Grok often satisfies the schema directly rather
than calling tools it deems optional. Separating the concerns forces the
model to investigate first and structure the answer second.

If investigation exhausts the cap while still requesting tools, `parsed`
is None and `cap_reached=True`. The caller (`adjudicate`, `critique`)
turns that into ESCALATE with the fact stated in the rationale — never
a crash, never a silent default approval.

Every ToolCall is recorded (name, arguments, result, latency, timestamp)
regardless of which turn it fired on.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeVar

from pydantic import BaseModel

from src.llm.cassette import CassetteStore
from src.llm.provider import LLMProvider
from src.schema import ModelCall, ToolCall
from src.tools import TOOLS, TOOLS_BY_NAME

MAX_INVESTIGATION_TURNS = 3

_T = TypeVar("_T", bound=BaseModel)

_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = LLMProvider(cassette_store=CassetteStore())
    return _provider


def set_provider(provider: LLMProvider) -> None:
    global _provider
    _provider = provider


@dataclass
class AgentResult:
    parsed: BaseModel | None
    raw_content: str
    model_calls: list[ModelCall]
    tool_calls: list[ToolCall]
    tool_cache: dict[str, Any]     # updated cache to merge back into state
    cap_reached: bool
    investigation_turns: int       # how many investigation calls fired


def cache_key(name: str, args: dict[str, Any]) -> str:
    """Stable cache key: tool name + JSON-sorted args."""
    return f"{name}::{json.dumps(args, sort_keys=True)}"


def format_tool_history(tool_calls: list[ToolCall]) -> str:
    """Compact bullet list of prior tool calls and results. Deduplicated —
    each unique (name, args) appears once, with the result condensed to keep
    the prompt manageable."""
    if not tool_calls:
        return ""
    seen: set[str] = set()
    lines: list[str] = []
    for tc in tool_calls:
        key = cache_key(tc.name, tc.arguments)
        if key in seen:
            continue
        seen.add(key)
        result_preview = json.dumps(tc.result, sort_keys=True, default=str)
        if len(result_preview) > 400:
            result_preview = result_preview[:400] + "…"
        args_json = json.dumps(tc.arguments, sort_keys=True)
        lines.append(f"- {tc.name}({args_json}) → {result_preview}")
    return "\n".join(lines)


def run_agent_loop(
    initial_prompt: str,
    response_schema: type[_T],
    prompt_name: str,
    *,
    max_investigation_turns: int = MAX_INVESTIGATION_TURNS,
    tool_cache: dict[str, Any] | None = None,
) -> AgentResult:
    provider = get_provider()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": initial_prompt},
    ]
    tools_schema = [t.openai_schema() for t in TOOLS]

    tool_calls_recorded: list[ToolCall] = []
    model_calls_recorded: list[ModelCall] = []
    investigation_content: str = ""
    cache: dict[str, Any] = dict(tool_cache) if tool_cache else {}

    # ---- Phase 1: INVESTIGATION ---------------------------------------
    cap_reached = False
    turns_fired = 0
    for turn in range(max_investigation_turns):
        turns_fired = turn + 1
        result = provider.chat(
            messages,
            tools=tools_schema,
            # NO response_schema — decouple tool use from schema pressure
            prompt_name=f"{prompt_name}_investigate_turn_{turns_fired}",
        )
        model_calls_recorded.append(result.model_call)

        if not result.tool_calls:
            # Model chose to answer / stop investigating. Preserve any content
            # so the synthesis call has the model's own reasoning to work from.
            investigation_content = result.content
            break

        # Execute tool calls, extend conversation, next turn
        messages.append(_assistant_tool_call_message(
            content=result.content, tool_calls=result.tool_calls,
        ))
        for tc in result.tool_calls:
            key = cache_key(tc["name"], tc["arguments"])
            if key in cache:
                # Cache hit: same tool + args already executed this run
                payload = cache[key]
                latency_ms = 0.0
            else:
                exec_result, latency_ms, err = _execute_tool(tc)
                payload = exec_result if err is None else {"error": err}
                # Only cache successful executions (errors may be transient)
                if err is None:
                    cache[key] = payload
            tool_calls_recorded.append(ToolCall(
                name=tc["name"],
                arguments=tc["arguments"],
                result=payload,
                latency_ms=latency_ms,      # 0.0 marks cache hits in the audit
                timestamp=datetime.now(timezone.utc),
            ))
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(payload),
            })
    else:
        # for-else: only executes if the loop completed without break.
        # Investigation cap reached with the model still calling tools.
        cap_reached = True

    if cap_reached:
        return AgentResult(
            parsed=None, raw_content="",
            model_calls=model_calls_recorded,
            tool_calls=tool_calls_recorded,
            tool_cache=cache,
            cap_reached=True,
            investigation_turns=turns_fired,
        )

    # ---- Phase 2: SYNTHESIS ------------------------------------------
    # Ask the model to produce the final structured answer, given every tool
    # result already in the conversation. Tools NOT passed here — the model
    # cannot call more; it must decide.
    messages.append({
        "role": "user",
        "content": (
            "Based on your investigation above, produce your final answer as "
            "JSON matching the response schema. Do not call any tools; you "
            "have all the information you need."
        ),
    })
    synthesis = provider.chat(
        messages,
        response_schema=response_schema,
        prompt_name=f"{prompt_name}_synthesize",
    )
    model_calls_recorded.append(synthesis.model_call)

    parsed = (
        synthesis.parsed
        if isinstance(synthesis.parsed, response_schema)
        else None
    )
    return AgentResult(
        parsed=parsed,
        raw_content=synthesis.content,
        model_calls=model_calls_recorded,
        tool_calls=tool_calls_recorded,
        tool_cache=cache,
        cap_reached=parsed is None,   # unparseable synthesis → treat as cap
        investigation_turns=turns_fired,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assistant_tool_call_message(
    *, content: str, tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"]),
                },
            }
            for tc in tool_calls
        ],
    }


def _execute_tool(tc: dict[str, Any]) -> tuple[dict[str, Any], float, str | None]:
    """Execute one tool call. Returns (result_dict, latency_ms, error_or_None)."""
    tool = TOOLS_BY_NAME.get(tc["name"])
    if tool is None:
        return ({}, 0.0, f"unknown tool: {tc['name']}")
    started = time.perf_counter()
    try:
        input_obj = tool.input_model.model_validate(tc["arguments"])
        output_obj = tool.fn(input_obj)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return (json.loads(output_obj.model_dump_json()), latency_ms, None)
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000.0
        return ({}, latency_ms, f"{type(exc).__name__}: {exc}")
