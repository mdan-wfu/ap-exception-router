"""Load every file in data/invoices/ through the router and print a summary table.

Uses whatever cassettes are already on disk. Missing cassettes trigger live
calls in `auto` mode — pass `--replay` to require cassettes only.

    python scripts/corpus_smoke.py
    python scripts/corpus_smoke.py --replay
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "data" / "invoices"

sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", action="store_true", help="require cassettes; fail on miss")
    args = parser.parse_args()

    if args.replay:
        os.environ["LLM_MODE"] = "replay"

    # Import after env is set so LLMProvider picks up the mode
    from src.adapters.router import extract

    console = Console()
    table = Table(title=f"Corpus smoke ({CORPUS})", show_lines=False)
    table.add_column("file", style="dim")
    table.add_column("fmt")
    table.add_column("adapter")
    table.add_column("fallback")
    table.add_column("invoice_number")
    table.add_column("vendor")
    table.add_column("items", justify="right")
    table.add_column("total", justify="right")
    table.add_column("corr", justify="right")
    table.add_column("conf", justify="right")

    failures: list[tuple[Path, Exception]] = []
    for path in sorted(CORPUS.iterdir()):
        if path.suffix.lower() not in {".txt", ".pdf", ".json", ".csv", ".xml"}:
            continue
        try:
            r = extract(path)
            inv = r.invoice
            total = f"${inv.stated_total.amount_usd}" if inv.stated_total else "-"
            table.add_row(
                path.name,
                path.suffix.lstrip("."),
                r.adapter_used,
                "yes" if r.llm_fallback else "",
                inv.invoice_number or "-",
                (inv.vendor_name or "")[:35],
                str(len(inv.line_items)),
                total,
                str(len(inv.corrections)),
                f"{inv.extraction_confidence:.2f}",
            )
        except Exception as exc:
            failures.append((path, exc))
            table.add_row(
                path.name, path.suffix.lstrip("."), "-", "-", "FAILED",
                str(exc)[:35], "-", "-", "-", "-",
            )

    console.print(table)
    if failures:
        console.print(f"[red]{len(failures)} file(s) failed[/red]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
