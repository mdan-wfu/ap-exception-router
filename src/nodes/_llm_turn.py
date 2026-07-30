"""Shared helper for Adjudicator + Critic tool-calling loops.

Runs the LLM up to `max_turns` times, executing tool calls in between. Every
tool call is recorded with arguments, result, and latency. Returns the parsed
final response (or None if the cap was reached without a structured answer)
plus the accumulated tool call trace.

This exists once so Adjudicator and Critic behave identically at the plumbing
level — the only difference between them is the prompt and the output schema.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeVar

from pydantic import BaseModel

from src.llm import CassetteStore, LLMProvider
from src.schema import ModelCall, ToolCall
from src.tools import TOOLS, TOOLS_BY_NAME

MAX_TOOL_TURNS = 3

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
class TurnResult:
    parsed: BaseModel | None       # None if the cap was reached
    raw_content: str
    model_calls: list[ModelCall]
    tool_calls: list[ToolCall]
    cap_reached: bool


def run_llm_with_tools(
    system_and_user_prompt: str,
    response_schema: type[_T],
    prompt_name: str,
    *,
    max_turns: int = MAX_TOOL_TURNS,
) -> TurnResult:
    """Iterative tool-calling loop. Returns the first parsed response of type
    `response_schema`, or (parsed=None, cap_reached=True) if we run out of turns.
    """
    provider = get_provider()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": system_and_user_prompt},
    ]
    tools_schema = [t.openai_schema() for t in TOOLS]

    tool_calls_recorded: list[ToolCall] = []
    model_calls_recorded: list[ModelCall] = []

    for _turn in range(max_turns):
        result = provider.chat(
            messages,
            response_schema=response_schema,
            tools=tools_schema,
            prompt_name=prompt_name,
        )
        model_calls_recorded.append(result.model_call)

        # If the model called tools, execute them and continue the loop
        if result.tool_calls:
            messages.append(_assistant_tool_call_message(
                content=result.content, tool_calls=result.tool_calls,
            ))
            for tc in result.tool_calls:
                exec_result, latency_ms, err = _execute_tool(tc)
                tool_calls_recorded.append(ToolCall(
                    name=tc["name"],
                    arguments=tc["arguments"],
                    result=exec_result if err is None else {"error": err},
                    latency_ms=latency_ms,
                    timestamp=datetime.now(timezone.utc),
                ))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(exec_result if err is None else {"error": err}),
                })
            continue

        # No tool calls: the model produced (or attempted to produce) a final answer
        if isinstance(result.parsed, response_schema):
            return TurnResult(
                parsed=result.parsed,
                raw_content=result.content,
                model_calls=model_calls_recorded,
                tool_calls=tool_calls_recorded,
                cap_reached=False,
            )
        # Model returned content that didn't parse; fall through to cap logic
        # since the repair loop in provider already tried MAX_REPAIR_ATTEMPTS.
        break

    return TurnResult(
        parsed=None,
        raw_content=result.content if "result" in locals() else "",
        model_calls=model_calls_recorded,
        tool_calls=tool_calls_recorded,
        cap_reached=True,
    )


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
    """Run one tool call. Returns (result_dict, latency_ms, error_or_None)."""
    tool = TOOLS_BY_NAME.get(tc["name"])
    if tool is None:
        return ({}, 0.0, f"unknown tool: {tc['name']}")
    started = time.perf_counter()
    try:
        input_obj = tool.input_model.model_validate(tc["arguments"])
        output_obj = tool.fn(input_obj)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return (json.loads(output_obj.model_dump_json()), latency_ms, None)
    except Exception as exc:  # tool contract: not-found never raises; genuine
        # infrastructure failures may. Record the error but don't crash the graph.
        latency_ms = (time.perf_counter() - started) * 1000.0
        return ({}, latency_ms, f"{type(exc).__name__}: {exc}")
