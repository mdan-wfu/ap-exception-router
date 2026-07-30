"""CLI entry point. Honors --invoice_path, --batch, --live, --replay.

    python main.py --invoice_path=data/invoices/invoice_1012.txt
    python main.py --batch
    python main.py --batch --replay
"""
import argparse
import os
from pathlib import Path


CORPUS = Path("data/invoices")


def main() -> None:
    parser = argparse.ArgumentParser(description="AP Exception Router")
    parser.add_argument("--invoice_path", help="Path to a single invoice file")
    parser.add_argument("--batch", action="store_true", help="Process the full corpus")

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--live", action="store_true", help="Hit the real LLM API")
    mode_group.add_argument("--replay", action="store_true", help="Replay from cassettes only")

    args = parser.parse_args()

    if args.live:
        os.environ["LLM_MODE"] = "live"
    elif args.replay:
        os.environ["LLM_MODE"] = "replay"
    else:
        os.environ.setdefault("LLM_MODE", "auto")

    if bool(args.invoice_path) == bool(args.batch):
        parser.error("Provide exactly one of --invoice_path or --batch")

    # Import after env is set so LLMProvider picks up the mode
    from src.batch import run_batch          # noqa: E402
    from src.graph import run_one            # noqa: E402

    if args.invoice_path:
        state = run_one(args.invoice_path)
        _print_result(args.invoice_path, state, note_no_dup_pass=True)
        return

    # batch
    paths = sorted(
        p for p in CORPUS.iterdir()
        if p.suffix.lower() in {".txt", ".pdf", ".json", ".csv", ".xml"}
    )
    results = run_batch(paths)
    for path, state in zip(paths, results):
        _print_result(str(path), state, note_no_dup_pass=False)


def _print_result(path: str, state, note_no_dup_pass: bool) -> None:
    inv = state.get("invoice")
    findings = state.get("findings", [])
    dec = state.get("decision")
    outcome = state.get("terminal_status", "?")
    outcome_val = outcome.value if hasattr(outcome, "value") else outcome
    inv_no = inv.invoice_number if inv else "-"
    print(
        f"{Path(path).name:30s} "
        f"{inv_no:10s} "
        f"{outcome_val:9s} "
        f"findings={len(findings):2d} "
        f"nodes={','.join(state.get('nodes_fired', []))}"
    )
    if note_no_dup_pass:
        print("  (single-invoice mode: duplicate detection skipped — batch required)")
    if dec and dec.rationale:
        print(f"  rationale: {dec.rationale}")


if __name__ == "__main__":
    main()
