"""Graph structure + reducer semantics.

Zero API calls: extraction hits cassettes; agent nodes are backed by the
fake provider from `conftest.py::graph_llm_fake`. Tests check structural
invariants (node sequence, critic firing, reducer semantics, batch dedup)
which do not depend on the LLM's decision quality.
"""
from pathlib import Path

import pytest

from src.graph import build_graph, run_with_human_resume
from src.graph_state import GraphState
from src.schema import Finding, Outcome, Severity
from src.validators import find_duplicates


# Autouse: every test in this module uses the in-process fake provider.
@pytest.fixture(autouse=True)
def _install_fake_llm(graph_llm_fake):
    yield


@pytest.fixture
def graph():
    """Fresh graph per test, no persistent checkpointer."""
    return build_graph(checkpointer_path=None)


def _invoke(graph, source_path, seeded=None):
    initial: GraphState = {
        "source_path": source_path,
        "findings": list(seeded) if seeded else [],
        "nodes_fired": [],
        "model_calls": [],
        "tool_calls": [],
        "critic_challenges": [],
        "critic_rounds": 0,
        "tool_result_cache": {},
    }
    # Drive through any human-gate suspension via the same runner run_one
    # and the batch loop use. Fake-provider tests pass a unique thread_id
    # per invocation so the checkpointer never collides across tests.
    import uuid
    config = {"configurable": {"thread_id": f"{source_path}::{uuid.uuid4().hex}"}}
    return run_with_human_resume(graph, initial, config)


# ---------------------------------------------------------------------------
# Compile + single-invoice traversal
# ---------------------------------------------------------------------------

def test_graph_compiles(graph) -> None:
    assert graph is not None


def test_single_invoice_traverses_deterministic_prefix(graph) -> None:
    """Every run passes through the deterministic prefix in this order, in
    both APPROVE and non-APPROVE paths. Fake provider returns ESCALATE so
    the tail below routes to scribe -> route_outcome."""
    state = _invoke(graph, "data/invoices/invoice_1001.txt")
    fired = state.get("nodes_fired", [])
    assert fired[:4] == ["triage", "validate", "policy_gate", "adjudicate"]
    assert fired[-1] == "route_outcome"


def test_single_invoice_produces_terminal_outcome(graph) -> None:
    state = _invoke(graph, "data/invoices/invoice_1001.txt")
    assert state.get("terminal_status") is not None
    assert state.get("decision") is not None


# ---------------------------------------------------------------------------
# Critic conditional
# ---------------------------------------------------------------------------

def test_critic_runs_at_most_one_round(graph) -> None:
    """Single-critic-round policy: MAX_CRITIC_ROUNDS=1. Trigger-qualified
    invoices get exactly one critic round regardless of whether revision
    occurred. INV-1013 has both HIGH findings and total > threshold, so
    the trigger fires; the cap stops after round 1."""
    from src.config import MAX_CRITIC_ROUNDS
    assert MAX_CRITIC_ROUNDS == 1, (
        "Policy: single critic round. If MAX_CRITIC_ROUNDS changes, this "
        "test needs updating alongside DECISIONS.md."
    )
    state = _invoke(graph, "data/invoices/invoice_1013.json")
    critic_count = sum(1 for n in state.get("nodes_fired", []) if n.startswith("critique"))
    assert critic_count == 1, (
        f"expected exactly 1 critic round; got {critic_count}. "
        f"Nodes: {state.get('nodes_fired')}"
    )
    assert state.get("critic_rounds") == 1
    assert state.get("revision_occurred", False) is False


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
