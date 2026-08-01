"""Score the pipeline against eval/ground_truth.yaml.

Three layers:
  extraction — field-level accuracy vs ground truth on the extracted Invoice
  findings   — MUST-fire codes must all be raised; unexpected codes reported
               but not scored (they may be legitimate; a reviewer decides)
  decisions  — Adjudicator outcome against expected-outcome set

Exit nonzero if ANY must-fire code is missing OR any single-valued expected
outcome diverges. This makes `make eval` a regression gate.

Runs in LLM_MODE=replay. Never issues a live API call.

CLI:
    make eval
    .venv/bin/python -m eval.run_eval
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table


REPO_ROOT = Path(__file__).resolve().parents[1]
# Relative to the repo root so `str(path)` matches what `make demo` produces.
# The Phase 6 basename normalization strips the path from the LLM view, but
# the raw source_file still flows through the audit store and duplicate
# detection — using an absolute path here produces different audit-store
# keys than the recorded cassettes were made against.
DEFAULT_CORPUS = Path("data/invoices")
DEFAULT_GROUND_TRUTH = REPO_ROOT / "eval" / "ground_truth.yaml"
RESULTS_DIR = REPO_ROOT / "runs"


# ---------------------------------------------------------------------------
# Structures for the results
# ---------------------------------------------------------------------------

@dataclass
class Miss:
    invoice: str
    field: str
    expected: Any
    actual: Any
    layer: str          # extraction | finding | decision
    kind: str           # miss_must_fire | unexpected_finding | field_mismatch | outcome_divergence
    format: str = ""    # source_format for per-format breakdown


@dataclass
class InvoiceScore:
    invoice_number: str
    format: str
    outcome: str
    expected_outcome: list[str]
    outcome_ok: bool
    must_fired: list[str]
    must_missing: list[str]
    may_fired: list[str]
    unexpected: list[str]
    extraction_fields_checked: int
    extraction_fields_matched: int
    field_misses: list[Miss] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Ground truth loader
# ---------------------------------------------------------------------------

def load_ground_truth(path: Path | None = None) -> dict[str, dict[str, Any]]:
    with (path or DEFAULT_GROUND_TRUTH).open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Run one invoice through the pipeline and score it
# ---------------------------------------------------------------------------

def score_invoice(gt: dict[str, Any], invoice_key: str, state: dict) -> InvoiceScore:
    """Compare a terminal graph state against ground truth for one invoice."""
    inv = state.get("invoice")
    dec = state.get("decision")
    findings = state.get("findings", [])
    fmt = inv.source_format if inv else "?"

    # -- extraction accuracy ------------------------------------------------
    misses: list[Miss] = []
    checked = 0
    matched = 0

    def check(field_name: str, expected, actual):
        nonlocal checked, matched
        checked += 1
        if _values_match(expected, actual):
            matched += 1
        else:
            misses.append(Miss(
                invoice=invoice_key, field=field_name,
                expected=expected, actual=actual,
                layer="extraction", kind="field_mismatch", format=fmt,
            ))

    if inv is not None:
        check("invoice_number", invoice_key, inv.invoice_number)
        check("vendor_name", gt.get("vendor_name", ""), inv.vendor_name or "")
        expected_currency = gt.get("currency", "USD")
        actual_currency = inv.stated_total.currency if inv.stated_total else None
        check("currency", expected_currency, actual_currency)

        expected_total_usd = gt.get("stated_total_usd")
        if expected_total_usd is not None:
            actual_usd = (
                float(inv.stated_total.amount_usd) if inv.stated_total else None
            )
            check("stated_total_usd", float(expected_total_usd), actual_usd)

        expected_line_count = gt.get("line_item_count")
        if expected_line_count is not None:
            check("line_item_count", int(expected_line_count), len(inv.line_items))

        # Aggregate quantities per canonical item (the important check —
        # this is what INV-1013 hinges on).
        expected_agg = gt.get("aggregate_by_canonical") or {}
        if expected_agg:
            actual_agg: dict[str, int] = defaultdict(int)
            for li in inv.line_items:
                key = li.canonical_item or li.raw_item_name.replace(" ", "")
                actual_agg[key] += li.quantity
            for canonical, qty in expected_agg.items():
                check(f"aggregate.{canonical}", int(qty), actual_agg.get(canonical, 0))

        expected_terms = gt.get("payment_terms")
        if expected_terms is not None:
            actual_terms = inv.payment_terms
            # None ground truth means "empty or missing acceptable"
            if expected_terms == "" or expected_terms is None:
                # accept None or empty
                if not (actual_terms is None or actual_terms == ""):
                    checked += 1
                    misses.append(Miss(
                        invoice=invoice_key, field="payment_terms",
                        expected=expected_terms, actual=actual_terms,
                        layer="extraction", kind="field_mismatch", format=fmt,
                    ))
                else:
                    checked += 1
                    matched += 1
            else:
                check("payment_terms", expected_terms, actual_terms)

        expected_corrections = gt.get("corrections_expected")
        if expected_corrections is not None:
            check("corrections_count", int(expected_corrections), len(inv.corrections))

    # -- findings accuracy --------------------------------------------------
    fired_codes = [f.code for f in findings]
    must = list(gt.get("must_fire") or [])
    may = list(gt.get("may_fire") or [])
    must_fired = [c for c in must if c in fired_codes]
    must_missing = [c for c in must if c not in fired_codes]
    may_fired = [c for c in may if c in fired_codes]
    unexpected = [c for c in fired_codes if c not in must and c not in may]
    for code in must_missing:
        misses.append(Miss(
            invoice=invoice_key, field=code, expected="fired", actual="missing",
            layer="finding", kind="miss_must_fire", format=fmt,
        ))
    for code in unexpected:
        misses.append(Miss(
            invoice=invoice_key, field=code, expected="not expected", actual="fired",
            layer="finding", kind="unexpected_finding", format=fmt,
        ))

    # -- decision accuracy --------------------------------------------------
    expected_outcomes = list(gt.get("expected_outcome") or [])
    actual_outcome = dec.outcome.value if dec else "FAILED"
    outcome_ok = actual_outcome in expected_outcomes
    if not outcome_ok:
        misses.append(Miss(
            invoice=invoice_key, field="outcome",
            expected=expected_outcomes, actual=actual_outcome,
            layer="decision", kind="outcome_divergence", format=fmt,
        ))

    return InvoiceScore(
        invoice_number=invoice_key,
        format=fmt,
        outcome=actual_outcome,
        expected_outcome=expected_outcomes,
        outcome_ok=outcome_ok,
        must_fired=must_fired,
        must_missing=must_missing,
        may_fired=may_fired,
        unexpected=unexpected,
        extraction_fields_checked=checked,
        extraction_fields_matched=matched,
        field_misses=misses,
    )


def _values_match(expected: Any, actual: Any) -> bool:
    if expected is None and (actual is None or actual == ""):
        return True
    if isinstance(expected, float) or isinstance(actual, float):
        try:
            return abs(float(expected) - float(actual)) < 0.01
        except (TypeError, ValueError):
            return False
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip() == actual.strip()
    if isinstance(actual, Decimal):
        try:
            return abs(float(expected) - float(actual)) < 0.01
        except (TypeError, ValueError):
            return False
    return expected == actual


# ---------------------------------------------------------------------------
# The batch runner — mirrors main.py --batch but returns terminal states
# ---------------------------------------------------------------------------

def run_corpus(corpus_dir: Path | None = None) -> dict[str, dict]:
    """Run the full corpus through the graph, return {invoice_number: state}."""
    from src.adapters.router import extract as router_extract
    from src.graph import build_graph
    from src.graph_state import GraphState
    from src.validators import find_duplicates, select_batch_retentions

    # Route this eval to its OWN audit + checkpoint files. Sharing
    # runs/audit.sqlite with `make demo` meant a reviewer following the
    # README verbatim (demo → eval → report) saw the report contradict
    # the demo output — eval had silently replaced the store's contents.
    # Isolation here means: report + dashboard + demo-digest all keep
    # showing the demo state after `make eval` runs. See DECISIONS
    # 2026-08-01 eval-audit-isolation.
    from src import config as _cfg
    eval_audit = REPO_ROOT / "runs" / "audit-eval.sqlite"
    eval_ckpt = REPO_ROOT / "runs" / "checkpoints-eval.sqlite"
    _cfg.AUDIT_DB_PATH = eval_audit
    for p in (eval_audit, eval_ckpt):
        if p.exists():
            p.unlink()

    corpus = corpus_dir or DEFAULT_CORPUS
    paths = sorted(
        p for p in corpus.iterdir()
        if p.suffix.lower() in {".txt", ".pdf", ".json", ".csv", ".xml"}
    )
    extractions = [(p, router_extract(p)) for p in paths]
    invoices = [r.invoice for _, r in extractions]

    dup_findings: dict[str, list] = defaultdict(list)
    for inv, f in find_duplicates(invoices):
        dup_findings[inv.source_file].append(f)

    # Same retained-file selection as main.py — see DECISIONS 2026-07-31.
    retained_source_files = select_batch_retentions(invoices)

    graph = build_graph(checkpointer_path=eval_ckpt)
    states: dict[str, dict] = {}
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
        states[num] = graph.invoke(initial, config=config)
    return states


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Corpus eval harness")
    parser.add_argument("--json-out", type=str, default=None,
                        help="Write a JSON summary to this path (default: auto in runs/)")
    parser.add_argument("--corpus", type=str, default=None,
                        help="Corpus directory (default: data/invoices)")
    parser.add_argument("--ground-truth", type=str, default=None,
                        help="Ground truth YAML (default: eval/ground_truth.yaml)")
    parser.add_argument("--label", type=str, default="provided corpus",
                        help="Label for the report header (e.g. "
                             "'authored adversarial set')")
    args = parser.parse_args()

    os.environ.setdefault("LLM_MODE", "replay")
    os.environ.setdefault("HUMAN_GATE_MODE", "demo")

    console = Console(width=140)
    console.print(f"[bold]Eval — {args.label} — replay mode, zero live calls[/bold]")

    from src.observability import build_manifest, format_manifest_lines
    manifest = build_manifest()
    for line in format_manifest_lines(manifest):
        console.print(f"[dim]{line}[/dim]")

    gt_path = Path(args.ground_truth) if args.ground_truth else None
    corpus_dir = Path(args.corpus) if args.corpus else None
    gt = load_ground_truth(gt_path)
    states = run_corpus(corpus_dir)

    # Score each invoice
    scores: list[InvoiceScore] = []
    for inv_num in sorted(gt.keys()):
        state = states.get(inv_num)
        if state is None:
            console.print(f"[red]missing state for {inv_num}[/red]")
            continue
        scores.append(score_invoice(gt[inv_num], inv_num, state))

    # -- overall numbers ----------------------------------------------------
    total_fields = sum(s.extraction_fields_checked for s in scores)
    matched_fields = sum(s.extraction_fields_matched for s in scores)
    all_musts = sum(len(gt[s.invoice_number].get("must_fire") or []) for s in scores)
    hit_musts = sum(len(s.must_fired) for s in scores)
    outcome_hits = sum(1 for s in scores if s.outcome_ok)

    # -- table: per-invoice summary ----------------------------------------
    t = Table(title="Per-invoice results")
    for col in ("invoice", "fmt", "outcome", "ok?", "expected",
                "extract", "must", "may", "unexpected"):
        t.add_column(col)
    for s in scores:
        expected = "/".join(s.expected_outcome)
        extract_str = f"{s.extraction_fields_matched}/{s.extraction_fields_checked}"
        must_str = f"{len(s.must_fired)}/{len(s.must_fired) + len(s.must_missing)}"
        ok_mark = "[green]✓[/green]" if s.outcome_ok else "[red]✗[/red]"
        t.add_row(
            s.invoice_number, s.format, s.outcome, ok_mark, expected,
            extract_str, must_str,
            ", ".join(s.may_fired) or "-",
            ", ".join(s.unexpected) or "-",
        )
    console.print(t)

    # -- per-format extraction ---------------------------------------------
    by_fmt: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for s in scores:
        by_fmt[s.format][0] += s.extraction_fields_matched
        by_fmt[s.format][1] += s.extraction_fields_checked
    fmt_t = Table(title="Extraction accuracy by source format")
    fmt_t.add_column("format"); fmt_t.add_column("matched", justify="right")
    fmt_t.add_column("checked", justify="right"); fmt_t.add_column("%", justify="right")
    for f in sorted(by_fmt):
        m, c = by_fmt[f]
        pct = 100.0 * m / c if c else 0.0
        fmt_t.add_row(f, str(m), str(c), f"{pct:.1f}")
    console.print(fmt_t)

    # -- headline -----------------------------------------------------------
    console.print(
        f"[bold]Extraction:[/bold] {matched_fields}/{total_fields} = "
        f"{100 * matched_fields / total_fields:.1f}%"
    )
    console.print(
        f"[bold]Must-fire findings:[/bold] {hit_musts}/{all_musts} = "
        f"{100 * hit_musts / all_musts:.1f}%"
    )
    console.print(
        f"[bold]Decision agreement:[/bold] {outcome_hits}/{len(scores)} = "
        f"{100 * outcome_hits / len(scores):.1f}%"
    )

    # -- named misses -------------------------------------------------------
    misses = [m for s in scores for m in s.field_misses]
    if misses:
        console.print("\n[bold red]Misses (named):[/bold red]")
        for m in misses:
            layer_tag = {
                "extraction": "[yellow]E[/yellow]",
                "finding":    "[red]F[/red]",
                "decision":   "[red]D[/red]",
            }.get(m.layer, "?")
            console.print(
                f"  {layer_tag} {m.invoice} [{m.format}] {m.field}: "
                f"expected={m.expected!r} actual={m.actual!r} ({m.kind})"
            )

    # -- write JSON summary -------------------------------------------------
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.json_out:
        out_path = Path(args.json_out)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        out_path = RESULTS_DIR / f"eval-{ts}.json"
    summary = {
        "manifest": manifest,
        "headline": {
            "extraction": {"matched": matched_fields, "checked": total_fields,
                           "pct": 100 * matched_fields / total_fields if total_fields else 0},
            "must_fire": {"hit": hit_musts, "expected": all_musts,
                          "pct": 100 * hit_musts / all_musts if all_musts else 0},
            "decisions": {"hit": outcome_hits, "total": len(scores),
                          "pct": 100 * outcome_hits / len(scores) if scores else 0},
        },
        "per_format_extraction": {
            f: {"matched": v[0], "checked": v[1]} for f, v in by_fmt.items()
        },
        "per_invoice": [
            {
                "invoice": s.invoice_number, "format": s.format,
                "outcome": s.outcome, "expected_outcome": s.expected_outcome,
                "outcome_ok": s.outcome_ok,
                "extraction_matched": s.extraction_fields_matched,
                "extraction_checked": s.extraction_fields_checked,
                "must_fired": s.must_fired, "must_missing": s.must_missing,
                "may_fired": s.may_fired, "unexpected": s.unexpected,
            } for s in scores
        ],
        "misses": [
            {"invoice": m.invoice, "layer": m.layer, "kind": m.kind,
             "field": m.field, "expected": m.expected, "actual": str(m.actual),
             "format": m.format}
            for m in misses
        ],
    }
    with out_path.open("w") as f:
        json.dump(summary, f, indent=2, default=str)
    console.print(f"[dim]summary: runs/{out_path.name}[/dim]")

    # -- gate ---------------------------------------------------------------
    must_miss_count = sum(len(s.must_missing) for s in scores)
    single_valued_divergences = sum(
        1 for s in scores
        if not s.outcome_ok and len(s.expected_outcome) == 1
    )
    if must_miss_count or single_valued_divergences:
        console.print(
            f"\n[bold red]FAIL[/bold red]: "
            f"{must_miss_count} must-fire miss(es), "
            f"{single_valued_divergences} single-valued outcome divergence(s)"
        )
        return 1
    console.print("\n[bold green]PASS[/bold green]: all must-fire codes hit, "
                  "all single-valued outcomes agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
