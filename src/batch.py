"""Batch entry point.

Duplicates are batch-scoped by design: `DP-*` findings need the SET of
invoices to group by normalized invoice_number. That check cannot run
inside a per-invoice graph pass.

Approach: pre-pass over the batch — extract every invoice via the router,
run `find_duplicates()` over the set, then seed each single-invoice graph
run's initial state with that invoice's duplicate findings. The Phase 4
finding-reducer merges them with per-invoice findings inside the graph.

Single-invoice mode (`--invoice_path`) cannot detect duplicates without
corpus context. The `run_one_from_extraction` path surfaces this by
seeding `duplicate_findings=[]` and noting the limitation in the run
output; the audit view in Phase 6 will label it "single-file mode,
duplicate detection skipped".
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from src.adapters.router import ExtractionResult, extract as router_extract
from src.graph import build_graph
from src.graph_state import GraphState
from src.schema import Finding, Invoice
from src.validators import find_duplicates, select_batch_retentions


def run_batch(paths: list[Path]) -> list[GraphState]:
    """Extract every file, compute duplicates once, then graph-run each.

    Duplicate groups collapse via `select_batch_retentions`: DP-001
    (matching semantic_hash) → most-complete file; DP-002 (differing
    semantic_hash) → alphabetical-first, no auto-selection. Prevents
    double-processing of duplicate pairs (INV-1011/1012/1013) without
    silently swapping between genuinely different submissions."""
    extractions: list[tuple[Path, ExtractionResult]] = [
        (p, router_extract(p)) for p in paths
    ]
    invoices: list[Invoice] = [r.invoice for _p, r in extractions]

    dup_findings: dict[str, list[Finding]] = defaultdict(list)
    for inv, finding in find_duplicates(invoices):
        dup_findings[inv.source_file].append(finding)

    retained_source_files = select_batch_retentions(invoices)

    graph = build_graph()
    results: list[GraphState] = []
    for (path, extraction), inv in zip(extractions, invoices):
        if inv.source_file not in retained_source_files:
            continue
        results.append(_run_with_seed(
            graph, str(path),
            seeded_findings=dup_findings.get(inv.source_file, []),
            thread_id=str(path),
        ))
    return results


def _run_with_seed(
    graph, source_path: str, seeded_findings: list[Finding], thread_id: str
) -> GraphState:
    """Seed initial findings (e.g. duplicate findings from batch pre-pass)."""
    config = {"configurable": {"thread_id": thread_id}}
    initial: GraphState = {
        "source_path": source_path,
        "findings": list(seeded_findings),
        "nodes_fired": [],
        "model_calls": [],
        "tool_calls": [],
        "critic_challenges": [],
        "critic_rounds": 0,
        "tool_result_cache": {},
        "human_queued": False,
    }
    return graph.invoke(initial, config=config)
