"""Rich-formatted CLI presentation for the AP pipeline.

Engineer-facing surface. This is what a reviewer sees when they run the
code. Feel of watching someone work, not reading a log.

Structured JSON logs live elsewhere (audit store). This module is display only.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.schema import Outcome, Severity


_SEVERITY_COLOR = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH:     "red",
    Severity.MEDIUM:   "yellow",
    Severity.LOW:      "dim yellow",
    Severity.INFO:     "dim",
}

_OUTCOME_COLOR = {
    Outcome.APPROVE:  "bold green",
    Outcome.REJECT:   "bold red",
    Outcome.ESCALATE: "bold yellow",
    Outcome.FAILED:   "bold magenta",
}


console = Console()


# ---------------------------------------------------------------------------
# Single-invoice presentation
# ---------------------------------------------------------------------------

def print_single_result(source_path: str, state: dict[str, Any]) -> None:
    """Render one invoice's outcome and its evidence trail."""
    inv = state.get("invoice")
    findings = state.get("findings", [])
    decision = state.get("decision")
    outcome = state.get("terminal_status")
    scribe_note = state.get("scribe_note")
    settlement = state.get("settlement_result")

    header = f"[bold]{Path(source_path).name}[/bold]"
    if inv:
        header += f"  {inv.invoice_number}  {inv.vendor_name!r}"
    console.print()
    console.print(Panel.fit(header, border_style="cyan"))

    if findings:
        _print_findings_table(findings)

    if decision:
        _print_decision_panel(decision, outcome)

    tool_calls = state.get("tool_calls", [])
    if tool_calls:
        _print_tool_calls(tool_calls)

    if scribe_note:
        console.print(Panel(scribe_note, title="Scribe note", border_style="blue"))

    if settlement:
        console.print(f"[dim]settlement:[/dim] {settlement}")

    _print_cost_footer(state)


def _print_findings_table(findings) -> None:
    table = Table(title="Findings", title_style="bold", show_header=True, header_style="dim")
    table.add_column("code", style="bold")
    table.add_column("severity")
    table.add_column("message")
    for f in findings:
        colour = _SEVERITY_COLOR.get(f.severity, "")
        table.add_row(
            f.code,
            Text(f.severity.value, style=colour),
            (f.message or "")[:100],
        )
    console.print(table)


def _print_decision_panel(decision, outcome) -> None:
    outcome_label = outcome.value if hasattr(outcome, "value") else str(outcome)
    style = _OUTCOME_COLOR.get(outcome, "bold white") if hasattr(outcome, "value") else "bold"
    body = Text()
    body.append(f"{outcome_label}\n\n", style=style)
    body.append(decision.rationale)
    console.print(Panel(body, title="Decision", border_style=style.split()[-1]))


def _print_tool_calls(tool_calls) -> None:
    table = Table(title="Tool calls", title_style="dim", show_header=True, header_style="dim")
    table.add_column("#", style="dim")
    table.add_column("tool")
    table.add_column("arguments")
    table.add_column("latency ms", justify="right")
    for i, tc in enumerate(tool_calls, 1):
        marker = " [cyan]cache[/cyan]" if tc.latency_ms == 0.0 else ""
        args_short = str(tc.arguments)
        if len(args_short) > 60:
            args_short = args_short[:60] + "…"
        table.add_row(str(i), tc.name + marker, args_short, f"{tc.latency_ms:.0f}")
    console.print(table)


def _print_cost_footer(state) -> None:
    mcs = state.get("model_calls", [])
    cost = sum((mc.cost_usd for mc in mcs), Decimal("0"))
    tools_n = len(state.get("tool_calls", []))
    console.print(
        f"[dim]cost ${float(cost):.5f}  |  "
        f"{len(mcs)} model call(s)  |  "
        f"{tools_n} tool call(s)  |  "
        f"{len(state.get('nodes_fired', []))} nodes[/dim]"
    )


# ---------------------------------------------------------------------------
# Batch summary
# ---------------------------------------------------------------------------

def print_batch_summary(rows: list[dict]) -> None:
    """Print a table + straight-through-rate summary for the batch."""
    table = Table(title="Batch summary", title_style="bold", show_header=True)
    table.add_column("file", style="dim")
    table.add_column("invoice")
    table.add_column("outcome")
    table.add_column("findings", justify="right")
    table.add_column("model calls", justify="right")
    table.add_column("tool calls", justify="right")
    table.add_column("cost", justify="right")

    total_cost = Decimal("0")
    dist = {}
    for r in rows:
        outcome = r["outcome"]
        colour = "bold green" if outcome == "APPROVE" else "bold red" if outcome == "REJECT" else "bold yellow"
        dist[outcome] = dist.get(outcome, 0) + 1
        total_cost += Decimal(str(r["cost"]))
        table.add_row(
            r["file"],
            r["invoice"],
            Text(outcome, style=colour),
            str(r["findings"]),
            str(r["model_calls"]),
            str(r["tool_calls"]),
            f"${r['cost']:.5f}",
        )
    console.print(table)

    n = len(rows) or 1
    approve_n = dist.get("APPROVE", 0)
    console.print()
    console.print(f"[bold]Distribution:[/bold] {dist}")
    console.print(
        f"[bold]Straight-through rate:[/bold] "
        f"{approve_n}/{n} = {approve_n / n * 100:.1f}% APPROVE (no human touch)"
    )
    console.print(f"[bold]Total cost:[/bold]  ${total_cost:.5f}")
    console.print(f"[bold]Per invoice:[/bold] ${total_cost / n:.5f}")


# ---------------------------------------------------------------------------
# Progress indicator (batch)
# ---------------------------------------------------------------------------

def print_batch_start(n: int) -> None:
    console.print()
    console.print(Panel.fit(
        f"[bold]Processing {n} corpus files[/bold]",
        border_style="cyan",
    ))


def print_batch_row(idx: int, n: int, invoice_number: str, outcome: str,
                    model_calls: int, tool_calls: int, cost: float,
                    cum_cost: float, elapsed: float) -> None:
    outcome_colour = ({
        "APPROVE": "green", "REJECT": "red",
        "ESCALATE": "yellow", "FAILED": "magenta",
    }).get(outcome, "white")
    console.print(
        f"  [{idx:2d}/{n}] {invoice_number:10s} "
        f"[{outcome_colour}]{outcome:9s}[/{outcome_colour}] "
        f"models={model_calls:2d} tools={tool_calls:2d} "
        f"[dim]${cost:.5f}  cum=${cum_cost:.5f}  ({elapsed:.1f}s)[/dim]"
    )
