"""Run the full pipeline over the 16 unique invoices and print the outcome table.

Uses the batch pre-pass so duplicate findings are seeded correctly.
Prints one row per UNIQUE invoice (deduplicating the txt/pdf pairs).

    python scripts/adjudicate_corpus.py
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

CORPUS = REPO_ROOT / "data" / "invoices"


def main() -> int:
    import time
    from collections import defaultdict

    from src.adapters.router import extract as router_extract
    from src.graph import build_graph
    from src.graph_state import GraphState
    from src.llm.agent_loop import CircuitBreakerTripped
    from src.validators import find_duplicates

    paths = sorted(
        p for p in CORPUS.iterdir()
        if p.suffix.lower() in {".txt", ".pdf", ".json", ".csv", ".xml"}
    )
    extractions = [(p, router_extract(p)) for p in paths]
    invoices = [r.invoice for _p, r in extractions]

    dup_findings = defaultdict(list)
    for inv, f in find_duplicates(invoices):
        dup_findings[inv.source_file].append(f)

    g = build_graph()

    # Dedupe by invoice_number for reporting; keep the first file per group
    seen: set[str] = set()
    rows = []
    total_cost = Decimal("0")
    aborted_on: str | None = None
    abort_reason: str | None = None
    for (path, extraction) in extractions:
        num = extraction.invoice.invoice_number
        if num in seen:
            continue
        seen.add(num)

        seed = dup_findings.get(extraction.invoice.source_file, [])
        initial: GraphState = {
            "source_path": str(path),
            "findings": list(seed),
            "nodes_fired": [],
            "model_calls": [], "tool_calls": [],
            "critic_challenges": [], "critic_rounds": 0,
            "tool_result_cache": {},
        }
        config = {"configurable": {"thread_id": str(path)}}
        started = time.perf_counter()
        try:
            state = g.invoke(initial, config=config)
        except CircuitBreakerTripped as exc:
            elapsed = time.perf_counter() - started
            aborted_on = f"{path.name} ({num})"
            abort_reason = str(exc)
            print()
            print(f"!!! CIRCUIT BREAKER TRIPPED on {aborted_on} after {elapsed:.1f}s")
            print(f"!!! {abort_reason}")
            print(f"!!! Aborting batch — {len(rows)} invoice(s) completed before this one.")
            print(f"!!! Cumulative COMPLETED cost so far: ${float(total_cost):.5f}")
            print(f"!!! (partial spend on {aborted_on} not included — see cassettes)")
            break
        elapsed = time.perf_counter() - started

        dec = state.get("decision")
        outcome = dec.outcome.value if dec else "?"
        critic = state.get("critic_rounds", 0)
        revised = state.get("revision_occurred", False)
        n_tools = len(state.get("tool_calls", []))
        n_models = len(state.get("model_calls", []))
        cost = sum((mc.cost_usd for mc in state.get("model_calls", [])), Decimal("0"))
        total_cost += cost

        rows.append((
            path.name, num, outcome, critic, revised, n_models, n_tools,
            float(cost), elapsed, (state.get("scribe_note") or "")[:60],
        ))
        # Running per-invoice line: outcome, calls, this-invoice cost,
        # cumulative-so-far cost, wall clock. Flushed immediately so if the
        # batch is interrupted we know exactly where we stopped and what we
        # have spent.
        print(
            f"  [{len(rows):2d}/16] {num:10s} {outcome:9s} "
            f"models={n_models:2d} tools={n_tools:2d} "
            f"${float(cost):.5f}  cum=${float(total_cost):.5f}  ({elapsed:.1f}s)",
            flush=True,
        )

    # Print
    print()
    print(f"{'file':30s} {'invoice':10s} {'outcome':9s} {'crit':>4s} "
          f"{'rev':>4s} {'mdls':>4s} {'tls':>4s} {'$cost':>8s} {'sec':>5s}  scribe note")
    print("-" * 140)
    for r in rows:
        rev = "yes" if r[4] else ""
        print(f"{r[0]:30s} {r[1]:10s} {r[2]:9s} {r[3]:>4d} "
              f"{rev:>4s} {r[5]:>4d} {r[6]:>4d} {r[7]:>8.5f} {r[8]:>5.1f}  {r[9]}")

    # Distribution
    distribution = defaultdict(int)
    for r in rows:
        distribution[r[2]] += 1
    print()
    print(f"Completed:    {len(rows)} / 16 invoices")
    if aborted_on:
        print(f"Aborted on:   {aborted_on}")
        print(f"Reason:       {abort_reason}")
    print(f"Distribution: {dict(distribution)}")
    print(f"vs target:    5 APPROVE / 7 REJECT / 5 ESCALATE")
    print(f"Total cost:   ${total_cost:.5f}")
    if rows:
        print(f"Per invoice:  ${total_cost / len(rows):.5f}")
    return 1 if aborted_on else 0


if __name__ == "__main__":
    sys.exit(main())
