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
    from collections import defaultdict

    from src.adapters.router import extract as router_extract
    from src.graph import build_graph
    from src.graph_state import GraphState
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
        }
        config = {"configurable": {"thread_id": str(path)}}
        state = g.invoke(initial, config=config)

        dec = state.get("decision")
        outcome = dec.outcome.value if dec else "?"
        critic = state.get("critic_rounds", 0)
        revised = state.get("revision_occurred", False)
        n_tools = len(state.get("tool_calls", []))
        cost = sum((mc.cost_usd for mc in state.get("model_calls", [])), Decimal("0"))
        total_cost += cost

        rows.append((
            path.name, num, outcome, critic, revised, n_tools,
            float(cost), (state.get("scribe_note") or "")[:60],
        ))

    # Print
    print(f"{'file':30s} {'invoice':10s} {'outcome':9s} {'crit':>4s} "
          f"{'rev':>4s} {'tools':>5s} {'$cost':>7s}  scribe note")
    print("-" * 130)
    for r in rows:
        rev = "yes" if r[4] else ""
        print(f"{r[0]:30s} {r[1]:10s} {r[2]:9s} {r[3]:>4d} "
              f"{rev:>4s} {r[5]:>5d} {r[6]:>7.5f}  {r[7]}")

    # Distribution
    distribution = defaultdict(int)
    for r in rows:
        distribution[r[2]] += 1
    print()
    print(f"Distribution: {dict(distribution)}")
    print(f"Total cost:   ${total_cost:.5f}")
    print(f"Per invoice:  ${total_cost / len(rows):.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
