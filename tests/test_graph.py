"""Graph structure + reducer semantics.

Zero API calls; deterministic adapters and validators throughout, so no
cassette dependency. The Phase 5c LLM agents will land as separate tests.
"""
from pathlib import Path

import pytest

from src.graph import build_graph
from src.graph_state import GraphState
from src.schema import Finding, Outcome, Severity
from src.validators import find_duplicates


@pytest.fixture
def graph():
    """Fresh graph per test, no persistent checkpointer."""
    return build_graph(checkpointer_path=None)


def _invoke(graph, source_path, seeded=None):
    initial: GraphState = {
        "source_path": source_path,
        "findings": list(seeded) if seeded else [],
        "nodes_fired": [],
        "critic_rounds": 0,
    }
    return graph.invoke(initial)


# ---------------------------------------------------------------------------
# Compile + single-invoice traversal
# ---------------------------------------------------------------------------

def test_graph_compiles(graph) -> None:
    assert graph is not None


def test_single_invoice_traverses_expected_nodes(graph) -> None:
    """A clean invoice: triage -> validate -> policy_gate -> adjudicate ->
    route_outcome (no critic)."""
    state = _invoke(graph, "data/invoices/invoice_1001.txt")
    assert state.get("nodes_fired") == [
        "triage", "validate", "policy_gate", "adjudicate:STUB", "route_outcome",
    ]


def test_single_invoice_produces_terminal_outcome(graph) -> None:
    state = _invoke(graph, "data/invoices/invoice_1001.txt")
    assert state.get("terminal_status") == Outcome.APPROVE
    assert state.get("decision") is not None


# ---------------------------------------------------------------------------
# Critic conditional
# ---------------------------------------------------------------------------

def test_critic_fires_for_inv_1013_over_threshold(graph) -> None:
    """$22,562.80 is over $10,000; critic must fire, capped at
    MAX_CRITIC_ROUNDS (2)."""
    state = _invoke(graph, "data/invoices/invoice_1013.json")
    critic_count = sum(1 for n in state.get("nodes_fired", []) if n.startswith("critique"))
    assert critic_count == 2, f"expected 2 critic rounds, got {critic_count}"
    assert state.get("critic_rounds") == 2


def test_critic_does_not_fire_for_inv_1001_clean(graph) -> None:
    """$5,000, no HIGH+ findings — critic must NOT fire."""
    state = _invoke(graph, "data/invoices/invoice_1001.txt")
    assert not any(n.startswith("critique") for n in state.get("nodes_fired", []))


def test_critic_fires_on_high_finding_even_below_threshold(graph) -> None:
    """INV-1008 ($9,900, below threshold) has VN-001 HIGH — critic must fire."""
    state = _invoke(graph, "data/invoices/invoice_1008.txt")
    assert any(n.startswith("critique") for n in state.get("nodes_fired", [])), (
        f"critic did not fire despite HIGH finding. Nodes: "
        f"{state.get('nodes_fired')}"
    )


# ---------------------------------------------------------------------------
# Findings reducer — accumulate, never overwrite
# ---------------------------------------------------------------------------

def test_findings_accumulate_across_nodes(graph) -> None:
    """Seeded findings + validator findings must both appear in final state."""
    seed = Finding(
        code="DP-999",
        severity=Severity.INFO,
        message="seeded-by-test",
        evidence="test-seed",
    )
    state = _invoke(graph, "data/invoices/invoice_1009.json", seeded=[seed])
    assert seed in state["findings"], "seeded finding was overwritten"
    codes = {f.code for f in state["findings"]}
    assert "AR-005" in codes and "VN-005" in codes, "validator findings missing"


# ---------------------------------------------------------------------------
# Batch: 20 files, 16 unique invoices, duplicate findings present
# ---------------------------------------------------------------------------

def _run_batch_no_checkpointer(paths):
    """Run the batch pre-pass with an in-memory graph (no persistent checkpoints
    that would leak between test invocations)."""
    from collections import defaultdict

    from src.adapters.router import extract as router_extract

    extractions = [(p, router_extract(p)) for p in paths]
    invoices = [r.invoice for _p, r in extractions]

    dup: dict[str, list] = defaultdict(list)
    for inv, finding in find_duplicates(invoices):
        dup[inv.source_file].append(finding)

    g = build_graph(checkpointer_path=None)
    return [
        _invoke(g, str(p), seeded=dup.get(r.invoice.source_file, []))
        for (p, r) in extractions
    ]


def test_batch_processes_every_file_and_surfaces_duplicates() -> None:
    corpus = Path("data/invoices")
    paths = sorted(
        p for p in corpus.iterdir()
        if p.suffix.lower() in {".txt", ".pdf", ".json", ".csv", ".xml"}
    )
    assert len(paths) == 20

    results = _run_batch_no_checkpointer(paths)
    assert len(results) == 20

    dp_001_files = {
        r.get("invoice").source_file if r.get("invoice") else None
        for r in results
        if any(f.code == "DP-001" for f in r.get("findings", []))
    }
    for expected in ("invoice_1011.txt", "invoice_1011.pdf",
                     "invoice_1012.txt", "invoice_1012.pdf",
                     "invoice_1013.json", "invoice_1013.pdf"):
        assert any(expected in f for f in dp_001_files if f), (
            f"DP-001 missing on {expected}. Got: {dp_001_files}"
        )

    dp_002_files = {
        r.get("invoice").source_file if r.get("invoice") else None
        for r in results
        if any(f.code == "DP-002" for f in r.get("findings", []))
    }
    assert any("invoice_1004.json" in f for f in dp_002_files if f)
    assert any("invoice_1004_revised" in f for f in dp_002_files if f)


def test_batch_results_are_distinct_16_unique_invoices() -> None:
    corpus = Path("data/invoices")
    paths = sorted(
        p for p in corpus.iterdir()
        if p.suffix.lower() in {".txt", ".pdf", ".json", ".csv", ".xml"}
    )
    results = _run_batch_no_checkpointer(paths)
    invoice_numbers = [
        r.get("invoice").invoice_number if r.get("invoice") else None for r in results
    ]
    unique = set(invoice_numbers)
    # 20 files -> 16 unique invoices (INV-1011/1012/1013 duplicate pairs, INV-1004 twice)
    assert len(unique) == 16, f"expected 16 unique invoice numbers, got {len(unique)}"
