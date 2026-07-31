"""Corpus-stats rollup — reads the audit store, prints a rich terminal report.

Zero API calls. Zero writes. Read-only view of what the pipeline actually
did across whatever runs currently sit in `runs/audit.sqlite`. Meant to
be run after `make demo` (or a live batch) to answer:

  - what was the outcome distribution?
  - what's the straight-through rate (approved with no human touch)?
  - what did each finding category cost the human queue?
  - what did the corpus cost in tokens and dollars?
  - what's the queue depth a clerk would actually face?
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.cli import fmt_cost
from src.config import AUDIT_DB_PATH


def _connect() -> sqlite3.Connection:
    if not Path(AUDIT_DB_PATH).exists():
        raise SystemExit(
            f"no audit store at {AUDIT_DB_PATH}. Run `make demo` first."
        )
    conn = sqlite3.connect(str(AUDIT_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _bucket(prompt_name: str | None) -> str:
    """Collapse per-turn prompt_names into agent buckets for cost breakdown."""
    if not prompt_name:
        return "(unknown)"
    if prompt_name.startswith("adjudicator_revised"):
        return "adjudicator_revised"
    if prompt_name.startswith("adjudicator"):
        return "adjudicator"
    if prompt_name.startswith("critic"):
        return "critic"
    if prompt_name.startswith("scribe"):
        return "scribe"
    if prompt_name.startswith("extractor"):
        return "extractor"
    return prompt_name


def main() -> None:
    conn = _connect()
    # Fixed width so the cost-by-node table doesn't truncate agent names when
    # the terminal is narrow (a reviewer pasting output into a doc doesn't
    # want "adjudi…" for the 30%-of-spend row).
    console = Console(width=130)

    runs = conn.execute("""
        SELECT id, invoice_number, outcome, human_outcome,
               started_at, finished_at
        FROM runs ORDER BY invoice_number
    """).fetchall()

    if not runs:
        console.print("[red]audit store empty — nothing to report.[/red]")
        return

    n = len(runs)

    # ---- 1. outcome distribution --------------------------------------------
    outcome_counter = Counter(r["outcome"] for r in runs)
    t = Table(title="Outcome distribution")
    t.add_column("outcome"); t.add_column("count", justify="right"); t.add_column("%", justify="right")
    for o in ("APPROVE", "REJECT", "ESCALATE", "FAILED"):
        c = outcome_counter.get(o, 0)
        t.add_row(o, str(c), f"{100*c/n:.1f}")
    console.print(t)

    # ---- 2. straight-through --------------------------------------------------
    # Approved with no human touch: outcome=APPROVE and human_outcome IS NULL.
    stp = sum(1 for r in runs if r["outcome"] == "APPROVE" and not r["human_outcome"])
    console.print(
        f"[bold]Straight-through rate:[/bold] {stp}/{n} = {100*stp/n:.1f}% "
        f"(APPROVE with no clerk decision recorded)"
    )

    # ---- 3. queue depth -------------------------------------------------------
    # What a clerk actually faces: ESCALATE runs with no PAID or REJECTED
    # settlement — either explicitly QUEUED, or HOLD from the human gate (which
    # records human_outcome=None + human_queued=True and writes no settlement).
    resolved = conn.execute(
        "SELECT DISTINCT invoice_number FROM settlements "
        "WHERE settlement_type IN ('PAID', 'REJECTED')"
    ).fetchall()
    resolved_nums = {r["invoice_number"] for r in resolved}
    queue = [r for r in runs if r["outcome"] == "ESCALATE" and r["invoice_number"] not in resolved_nums]
    queued_settlements = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE settlement_type='QUEUED'"
    ).fetchone()[0]
    console.print(
        f"[bold]Queue depth:[/bold] [bold yellow]{len(queue)}[/bold yellow] "
        f"awaiting clerk action "
        f"({queued_settlements} QUEUED + {len(queue) - queued_settlements} HOLD)"
    )
    if queue:
        console.print(f"  [dim]held: {', '.join(r['invoice_number'] for r in queue)}[/dim]")
    console.print()

    # ---- 4. exceptions by category -------------------------------------------
    finding_rows = conn.execute("SELECT code FROM findings").fetchall()
    by_prefix: Counter[str] = Counter()
    for r in finding_rows:
        by_prefix[r["code"].split("-")[0]] += 1

    t = Table(title=f"Exceptions by category ({len(finding_rows)} findings across {n} runs)")
    t.add_column("prefix"); t.add_column("domain"); t.add_column("count", justify="right")
    domains = {
        "EX": "extraction", "AR": "arithmetic", "IN": "inventory",
        "PR": "pricing", "VN": "vendor", "TM": "terms",
        "DP": "duplicates", "PO": "policy", "FR": "fraud signals",
    }
    for prefix in sorted(by_prefix):
        t.add_row(prefix, domains.get(prefix, ""), str(by_prefix[prefix]))
    console.print(t)

    # ---- 5. cost by node type -----------------------------------------------
    call_rows = conn.execute("""
        SELECT prompt_name, cost_usd, prompt_tokens, cached_prompt_tokens,
               completion_tokens, reasoning_tokens
        FROM model_calls
    """).fetchall()

    by_bucket: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0, "cost": 0.0, "prompt": 0, "cached": 0, "completion": 0, "reasoning": 0}
    )
    for r in call_rows:
        b = by_bucket[_bucket(r["prompt_name"])]
        b["n"] += 1
        b["cost"] += float(r["cost_usd"] or 0)
        b["prompt"] += r["prompt_tokens"] or 0
        b["cached"] += r["cached_prompt_tokens"] or 0
        b["completion"] += r["completion_tokens"] or 0
        b["reasoning"] += r["reasoning_tokens"] or 0

    total_cost = sum(b["cost"] for b in by_bucket.values())
    t = Table(title="Cost by node type")
    t.add_column("node"); t.add_column("calls", justify="right")
    t.add_column("cost", justify="right"); t.add_column("% total", justify="right")
    t.add_column("prompt tk", justify="right"); t.add_column("cached tk", justify="right")
    t.add_column("compl tk", justify="right"); t.add_column("reason tk", justify="right")
    for bucket in sorted(by_bucket, key=lambda k: -by_bucket[k]["cost"]):
        b = by_bucket[bucket]
        pct = 100 * b["cost"] / total_cost if total_cost else 0
        t.add_row(
            bucket, str(int(b["n"])),
            fmt_cost(b['cost']), f"{pct:.1f}",
            f"{int(b['prompt']):,}", f"{int(b['cached']):,}",
            f"{int(b['completion']):,}", f"{int(b['reasoning']):,}",
        )
    console.print(t)

    console.print(
        f"[bold]Total cost:[/bold] {fmt_cost(total_cost)}    "
        f"[bold]Per invoice:[/bold] {fmt_cost(total_cost/n)}    "
        f"[bold]Calls:[/bold] {len(call_rows)}"
    )

    # ---- 6. wall clock -------------------------------------------------------
    from datetime import datetime
    durations = []
    for r in runs:
        if not (r["started_at"] and r["finished_at"]):
            continue
        try:
            s = datetime.fromisoformat(r["started_at"])
            f = datetime.fromisoformat(r["finished_at"])
            durations.append(((f - s).total_seconds(), r["invoice_number"]))
        except Exception:
            pass
    if durations:
        secs = [d for d, _ in durations]
        _, slowest_inv = max(durations)
        console.print(
            f"[bold]Wall clock (replay):[/bold] "
            f"mean {sum(secs)/len(secs):.3f}s, "
            f"max {max(secs):.3f}s ({slowest_inv}), "
            f"min {min(secs):.3f}s"
        )


if __name__ == "__main__":
    main()
