"""LIVE run of INV-1012 through the full graph.

Records the metrics the user requested and confirms the circuit breaker
either did not trip or, if it did, names which cap tripped and where.
"""
from __future__ import annotations

import os
import sys
import time
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Ensure live mode is honored even if the .env is stale
os.environ["LLM_MODE"] = "live"


def main() -> int:
    from collections import defaultdict

    from src.adapters.router import extract as router_extract
    from src.graph import build_graph
    from src.llm.agent_loop import CircuitBreakerTripped
    from src.validators import find_duplicates

    invoice_path = Path("data/invoices/invoice_1012.txt")

    # Only INV-1012's own duplicate finding — do NOT extract the whole corpus.
    # INV-1012 has a companion .pdf so DP-001 needs both files' invoices to compute.
    inv_txt = router_extract(invoice_path).invoice
    inv_pdf = router_extract(Path("data/invoices/invoice_1012.pdf")).invoice
    dup_findings = defaultdict(list)
    for inv, f in find_duplicates([inv_txt, inv_pdf]):
        dup_findings[inv.source_file].append(f)

    g = build_graph(checkpointer_path=None)
    seed = dup_findings.get(inv_txt.source_file, [])
    initial = {
        "source_path": str(invoice_path),
        "findings": list(seed),
        "nodes_fired": [],
        "model_calls": [], "tool_calls": [],
        "critic_challenges": [], "critic_rounds": 0,
        "tool_result_cache": {},
    }

    tripped = None
    started = time.perf_counter()
    try:
        state = g.invoke(initial)
    except CircuitBreakerTripped as exc:
        tripped = str(exc)
        state = None
    elapsed = time.perf_counter() - started

    print(f"\n{'=' * 70}")
    print("INV-1012 LIVE RUN — REPORT")
    print(f"{'=' * 70}\n")
    print(f"wall clock:              {elapsed:.1f}s")
    print(f"circuit breaker tripped: {'YES — ' + tripped if tripped else 'no'}")

    if state is None:
        # Aborted — cannot report the rest
        print("\nRun aborted before completion. No decision available.")
        return 1

    tcs = state.get("tool_calls", [])
    mcs = state.get("model_calls", [])
    cost = sum((mc.cost_usd for mc in mcs), Decimal("0"))
    unique_sigs = set()
    import json
    for tc in tcs:
        unique_sigs.add((tc.name, json.dumps(tc.arguments, sort_keys=True)))
    cache_hits = sum(1 for tc in tcs if tc.latency_ms == 0.0)

    print(f"total tool calls:        {len(tcs)}  (previous run: 41)")
    print(f"unique tool calls:       {len(unique_sigs)}  (previous run: 12)")
    print(f"cache hits (latency=0):  {cache_hits}")
    print(f"total model calls:       {len(mcs)}  (previous run: 17)")
    print(f"cost:                    ${cost:.5f}  (previous run: $0.184)")
    print(f"outcome:                 {state['terminal_status'].value}")
    print(f"critic rounds:           {state.get('critic_rounds', 0)}")
    print(f"revision occurred:       {state.get('revision_occurred', False)}")
    print()
    print("TOOL CALL SEQUENCE:")
    for i, tc in enumerate(tcs, 1):
        marker = "  [CACHE]" if tc.latency_ms == 0.0 else ""
        args = json.dumps(tc.arguments, sort_keys=True)
        print(f"  {i:2d}. {tc.name}({args}){marker}")
    print()
    print("FINAL RATIONALE (verbatim):")
    print(state["decision"].rationale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
