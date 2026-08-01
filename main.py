"""CLI entry point. Case brief:

    python main.py --invoice_path=data/invoices/invoice_1012.txt
    python main.py --batch
    python main.py --batch --replay
"""
import argparse
import os
from decimal import Decimal
from pathlib import Path


CORPUS = Path("data/invoices")


def main() -> None:
    parser = argparse.ArgumentParser(description="AP Exception Router")
    parser.add_argument("--invoice_path", help="Path to a single invoice file")
    parser.add_argument("--batch", action="store_true", help="Process the full corpus")
    parser.add_argument("--corpus", default=str(CORPUS),
                        help="Directory of invoices to process in batch mode "
                             "(default: data/invoices). Adversarial set at "
                             "data/adversarial for the authored-set eval.")

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--live", action="store_true",
        help="Explicit opt-in: hit the real xAI API for cache misses "
             "and incur cost. Default is --replay.",
    )
    mode_group.add_argument(
        "--replay", action="store_true",
        help="(Default) Replay from cassettes only; a miss is a hard error "
             "with a reviewer-friendly message pointing at --live.",
    )
    mode_group.add_argument(
        "--auto", action="store_true",
        help="Development mode: replay on hit, live on miss. "
             "NEVER the default — quiet spend is exactly the surprise the "
             "default-replay rule exists to prevent.",
    )

    args = parser.parse_args()

    # Default is REPLAY. --live is the explicit opt-in that authorises spend.
    # Ambient LLM_MODE from the shell is IGNORED to keep the reviewer's mental
    # model tight: what she typed on the command line is what runs.
    if args.live:
        os.environ["LLM_MODE"] = "live"
    elif args.auto:
        os.environ["LLM_MODE"] = "auto"
    else:
        os.environ["LLM_MODE"] = "replay"

    if bool(args.invoice_path) == bool(args.batch):
        parser.error("Provide exactly one of --invoice_path or --batch")

    # Import after env is set so LLMProvider picks up the mode
    from src.cli import (
        console, print_batch_row, print_batch_start,
        print_batch_summary, print_single_result,
    )
    from src.graph import run_one
    from src.llm.agent_loop import CircuitBreakerTripped
    from src.llm.provider import CacheMissError
    from src.observability import (
        build_manifest, build_run_record, format_manifest_lines, write_jsonl,
    )

    manifest = build_manifest()
    for line in format_manifest_lines(manifest):
        console.print(f"[dim]{line}[/dim]")

    if args.invoice_path:
        import time
        started = time.perf_counter()
        try:
            state = run_one(args.invoice_path)
        except CircuitBreakerTripped as exc:
            console.print(f"[bold red]circuit breaker tripped:[/bold red] {exc}")
            raise SystemExit(1)
        except CacheMissError as exc:
            # A new invoice legitimately has no cassette in replay mode. Surface
            # the friendly guidance from provider.CacheMissError without the
            # Python traceback that LangGraph otherwise emits.
            console.print(f"[bold yellow]replay-mode cache miss[/bold yellow]")
            console.print(str(exc))
            raise SystemExit(2)
        elapsed = time.perf_counter() - started
        print_single_result(args.invoice_path, state)
        _emit_jsonl(manifest, [_record_from_state(args.invoice_path, state, elapsed)])
        # Exit non-zero on FAILED so shell scripts and CI catch the failure.
        outcome_val = getattr(state.get("terminal_status"), "value", state.get("terminal_status"))
        if outcome_val == "FAILED":
            raise SystemExit(3)
        return

    # -- batch --
    import time
    from collections import defaultdict

    from src.adapters.router import extract as router_extract
    from src.graph import build_graph
    from src.graph_state import GraphState
    from src.validators import find_duplicates, select_batch_retentions

    corpus_dir = Path(args.corpus)
    paths = sorted(
        p for p in corpus_dir.iterdir()
        if p.suffix.lower() in {".txt", ".pdf", ".json", ".csv", ".xml"}
    )
    extractions = [(p, router_extract(p)) for p in paths]
    invoices = [r.invoice for _, r in extractions]
    dup_findings = defaultdict(list)
    for inv, f in find_duplicates(invoices):
        dup_findings[inv.source_file].append(f)

    # Collapse duplicate groups: DP-001 (matching hash) → most-complete file;
    # DP-002 (differing hash) → alphabetical-first, do NOT auto-pick. Fixes
    # the INV-1011 payment_terms miss without silently swapping INV-1004's
    # original for its revised submission. See DECISIONS 2026-07-31.
    retained_source_files = select_batch_retentions(invoices)
    print_batch_start(len(paths), len(retained_source_files))

    g = build_graph()

    rows = []
    jsonl_records: list = []
    total_cost = Decimal("0")
    for path, extraction in extractions:
        if extraction.invoice.source_file not in retained_source_files:
            continue
        num = extraction.invoice.invoice_number

        seed = dup_findings.get(extraction.invoice.source_file, [])
        initial: GraphState = {
            "source_path": str(path),
            "findings": list(seed),
            "nodes_fired": [],
            "model_calls": [], "tool_calls": [],
            "critic_challenges": [], "critic_rounds": 0,
            "tool_result_cache": {},
            "human_queued": False,
        }
        config = {"configurable": {"thread_id": str(path)}}
        started = time.perf_counter()
        try:
            from src.graph import run_with_human_resume
            state = run_with_human_resume(g, initial, config)
        except CircuitBreakerTripped as exc:
            elapsed = time.perf_counter() - started
            console.print(
                f"[bold red]!!! CIRCUIT BREAKER on {path.name} ({num}) "
                f"after {elapsed:.1f}s[/bold red]"
            )
            console.print(f"    {exc}")
            console.print(
                f"    cumulative COMPLETED cost so far: [bold]${float(total_cost):.5f}[/bold]"
            )
            raise SystemExit(1)
        elapsed = time.perf_counter() - started

        dec = state.get("decision")
        outcome = dec.outcome.value if dec else "FAILED"
        n_findings = len(state.get("findings", []))
        n_models = len(state.get("model_calls", []))
        n_tools = len(state.get("tool_calls", []))
        cost = sum((mc.cost_usd for mc in state.get("model_calls", [])), Decimal("0"))
        total_cost += cost

        rows.append({
            "file": path.name, "invoice": num, "outcome": outcome,
            "findings": n_findings, "model_calls": n_models, "tool_calls": n_tools,
            "cost": float(cost),
        })
        jsonl_records.append(_record_from_state(str(path), state, elapsed))
        print_batch_row(len(rows), len(retained_source_files), num, outcome,
                        n_models, n_tools, float(cost), float(total_cost), elapsed)

    print_batch_summary(rows)
    _emit_jsonl(manifest, jsonl_records)


def _record_from_state(path: str, state, elapsed: float):
    """Compose an observability record from a terminal graph state."""
    from src.observability import build_run_record
    inv = state.get("invoice")
    dec = state.get("decision")
    return build_run_record(
        invoice_path=path,
        invoice_number=inv.invoice_number if inv else "(unknown)",
        outcome=dec.outcome.value if dec else "FAILED",
        findings=state.get("findings", []),
        nodes_fired=state.get("nodes_fired", []),
        model_calls=state.get("model_calls", []),
        tool_calls=state.get("tool_calls", []),
        scribe_note=state.get("scribe_note"),
        elapsed_seconds=elapsed,
        terminal_status=(
            state.get("terminal_status").value
            if hasattr(state.get("terminal_status"), "value")
            else (state.get("terminal_status") or "UNKNOWN")
        ),
        failure_reason=state.get("failure_reason"),
        human_outcome=state.get("human_outcome"),
        human_note=state.get("human_note"),
        settlement_result=state.get("settlement_result"),
        mock_payment_reference=state.get("mock_payment_reference"),
    )


def _emit_jsonl(manifest, records):
    """Write the batch JSONL. The path is intentionally NOT printed to stdout
    (the filename contains a timestamp, and demo output must be deterministic
    for the Phase 7 byte-identical check). Reviewers can `ls runs/*.jsonl`."""
    from src.observability import write_jsonl
    write_jsonl(manifest, records)


if __name__ == "__main__":
    main()
