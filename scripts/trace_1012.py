"""Manual node-by-node walk of INV-1012 in replay mode.

Purpose: reveal exactly where the fixed code diverges from the recorded
cassettes, and inspect state (specifically tool_result_cache and the
prior-investigation summary) at the point of divergence.

DOES NOT go live. Uses replay mode — a cache miss raises CacheMissError
and this script catches it, reports the miss, and dumps state.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Force replay mode BEFORE anything else imports the provider
os.environ["LLM_MODE"] = "replay"


def merge(state, partial):
    """Mimic LangGraph reducers for the state fields we care about."""
    accumulate = {
        "findings", "nodes_fired", "model_calls", "tool_calls",
        "critic_challenges",
    }
    for k, v in partial.items():
        if k in accumulate:
            state[k] = state.get(k, []) + v
        elif k == "tool_result_cache":
            state[k] = {**state.get(k, {}), **v}
        else:
            state[k] = v
    return state


def main() -> int:
    from src.adapters.router import extract as router_extract
    from src.llm.agent_loop import format_tool_history
    from src.llm.provider import CacheMissError
    from src.nodes.adjudicate import adjudicate
    from src.nodes.critique import critique
    from src.nodes.policy_gate import policy_gate
    from src.nodes.route_outcome import route_outcome
    from src.nodes.scribe import scribe
    from src.nodes.triage import triage
    from src.nodes.validate import validate

    path = Path("data/invoices/invoice_1012.txt")

    state = {
        "source_path": str(path),
        "findings": [], "nodes_fired": [], "model_calls": [], "tool_calls": [],
        "critic_challenges": [], "critic_rounds": 0, "tool_result_cache": {},
    }

    steps = [
        ("triage", triage),
        ("validate", validate),
        ("policy_gate", policy_gate),
    ]

    # Deterministic prefix (no LLM)
    for name, fn in steps:
        state = merge(state, fn(state))
        print(f"[OK] {name}")

    print(f"     findings: {len(state['findings'])}")
    print(f"     invoice: {state['invoice'].invoice_number} vendor={state['invoice'].vendor_name!r}")

    # Initial adjudicate — the first LLM call
    print()
    print(">>> attempting initial adjudicate (first LLM call under new agent_loop)")
    try:
        partial = adjudicate(state)
        state = merge(state, partial)
        _report_success("initial adjudicate", state, partial)
    except CacheMissError as e:
        _report_miss("initial adjudicate", state, e)
        return 0

    # If we got here, the initial adjudicate cassette actually hit — continue.
    from src.graph import route_after_adjudicate
    branch = route_after_adjudicate(state)
    print(f"     route_after_adjudicate -> {branch}")

    if branch == "critique":
        # Build the prior_investigation summary the same way critique does
        prior = format_tool_history(state.get("tool_calls", []))
        print()
        print(f"### PRIOR INVESTIGATION passed into critic:")
        print(prior if prior else "(empty)")
        print()
        print(">>> attempting critique round 1")
        try:
            partial = critique(state)
            state = merge(state, partial)
            _report_success("critique round 1", state, partial)
        except CacheMissError as e:
            _report_miss("critique round 1", state, e)
            return 0

    return 0


def _report_success(node_name: str, state: dict, partial: dict) -> None:
    print(f"[OK] {node_name}")
    print(f"     tool_calls this node: {len(partial.get('tool_calls', []))}")
    print(f"     cache size after node: {len(state.get('tool_result_cache', {}))}")
    # List tool calls made in this node with cache-hit markers
    for tc in partial.get("tool_calls", []):
        marker = "  [CACHE HIT]" if tc.latency_ms == 0.0 else ""
        args = json.dumps(tc.arguments, sort_keys=True)
        print(f"       {tc.name}({args}){marker}")


def _report_miss(node_name: str, state: dict, exc) -> None:
    print()
    print(f"[MISS] CacheMissError inside {node_name}")
    print(f"       missing key: {exc}")
    print()
    print("STATE AT POINT OF MISS")
    print(f"  nodes_fired:           {state.get('nodes_fired')}")
    print(f"  findings count:        {len(state.get('findings', []))}")
    print(f"  model_calls count:     {len(state.get('model_calls', []))}")
    print(f"  tool_calls count:      {len(state.get('tool_calls', []))}")
    print(f"  tool_result_cache size: {len(state.get('tool_result_cache', {}))}")
    print(f"  critic_rounds:         {state.get('critic_rounds', 0)}")
    print()
    print("TOOL RESULT CACHE CONTENTS (keys only):")
    for k in sorted(state.get("tool_result_cache", {}).keys()):
        print(f"  {k}")
    print()
    # Answer the FastShip specific question
    fastship_key = 'get_vendor_record::{"name": "FastShip Ltd."}'
    if fastship_key in state.get("tool_result_cache", {}):
        print(f"CONFIRMED: get_vendor_record(FastShip Ltd.) IS in cache")
    else:
        # Try a variant with different spacing (sort_keys=True does not affect
        # single-key dicts, but let me be explicit)
        for k in state.get("tool_result_cache", {}):
            if "FastShip" in k:
                print(f"FASTSHIP-RELATED KEY FOUND: {k}")
                break
        else:
            print("NOT YET IN CACHE: get_vendor_record(FastShip Ltd.)")
    print()
    if state.get("tool_calls"):
        print("Repeat-attempt check — same (name, args) appearing more than once:")
        from collections import Counter
        sigs = [(tc.name, json.dumps(tc.arguments, sort_keys=True), tc.latency_ms)
                for tc in state.get("tool_calls", [])]
        cache_hits = sum(1 for s in sigs if s[2] == 0.0)
        print(f"  total tool_calls recorded: {len(sigs)}")
        print(f"  cache hits (latency=0):    {cache_hits}")


if __name__ == "__main__":
    sys.exit(main())
