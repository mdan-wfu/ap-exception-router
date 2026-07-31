"""Human gate — fires on ESCALATE, three modes selected by config.

Modes (src.config.HUMAN_GATE_MODE):

  interactive — pause on stdin, print the escalation packet (invoice, findings,
                Adjudicator rationale, Scribe note), accept APPROVE/REJECT/HOLD,
                resume the graph with the human's decision recorded as such.
                The human's outcome NEVER overwrites the model's decision —
                both are stored, distinct, in the audit trail. The disagreement
                between them is one of the most useful data points the system
                produces.

  demo        — auto-resolve from data/fixtures/human_gate.json. `make demo`
                relies on this; the reviewer's run never hangs.

  queue       — record the escalation to the audit store's ESCALATE queue and
                exit the run without a human answer. Phase 11's dashboard reads
                the queue.

`interrupt()` is only used in interactive mode. Demo and queue modes are
synchronous — no checkpoint dependency for the reviewer's happy path.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from src.config import HUMAN_GATE_FIXTURE_PATH, HUMAN_GATE_MODE
from src.graph_state import GraphState
from src.schema import Outcome


VALID_HUMAN_OUTCOMES = {"APPROVE", "REJECT", "HOLD"}


def human_gate(state: GraphState) -> dict:
    """Route based on HUMAN_GATE_MODE. Read at call time so tests / demo can
    override via env vars without restarting the interpreter."""
    import os
    mode = os.environ.get("HUMAN_GATE_MODE", HUMAN_GATE_MODE)

    invoice = state["invoice"]
    decision = state.get("decision")

    # Only fire for ESCALATE. Anything else short-circuits.
    if decision is None or decision.outcome != Outcome.ESCALATE:
        return {"nodes_fired": ["human_gate:skipped_not_escalate"]}

    if mode == "queue":
        return _queue(state)
    if mode == "demo":
        return _demo(state)
    return _interactive(state)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def _demo(state: GraphState) -> dict:
    """Auto-resolve from the fixture. Never blocks. Suitable for `make demo`."""
    invoice = state["invoice"]
    fixture = _load_fixture()
    entry = fixture.get(invoice.invoice_number, fixture.get("default", {}))
    outcome = entry.get("outcome", "HOLD")
    note = entry.get("note", "demo fixture default")

    if outcome not in VALID_HUMAN_OUTCOMES:
        outcome = "HOLD"

    # If HOLD, treat like queue — no settlement resolution
    if outcome == "HOLD":
        return {
            "human_outcome": None,
            "human_note": note,
            "human_queued": True,
            "nodes_fired": ["human_gate:demo_hold"],
        }
    return {
        "human_outcome": outcome,
        "human_note": note,
        "nodes_fired": [f"human_gate:demo_{outcome.lower()}"],
    }


def _queue(state: GraphState) -> dict:
    """Record the escalation and set `human_queued=True` so settle skips.
    The Phase 11 dashboard reads escalated runs via the audit store."""
    return {
        "human_outcome": None,
        "human_note": "queued for review — awaiting clerk decision",
        "human_queued": True,
        "nodes_fired": ["human_gate:queued"],
    }


def _interactive(state: GraphState) -> dict:
    """Print the escalation packet to stdout, read the clerk's decision from
    stdin. Blocks until the clerk answers. This is the primary human path
    when the system is actually deployed."""
    invoice = state["invoice"]
    decision = state["decision"]
    findings = state.get("findings", [])
    scribe_note = state.get("scribe_note")

    _print_packet(invoice, findings, decision, scribe_note)
    outcome, note = _read_clerk_answer()

    if outcome == "HOLD":
        return {
            "human_outcome": None,
            "human_note": note,
            "human_queued": True,
            "nodes_fired": ["human_gate:interactive_hold"],
        }
    return {
        "human_outcome": outcome,
        "human_note": note,
        "nodes_fired": [f"human_gate:interactive_{outcome.lower()}"],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_fixture() -> dict:
    path = Path(HUMAN_GATE_FIXTURE_PATH)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _print_packet(invoice, findings, decision, scribe_note) -> None:
    print("\n" + "=" * 72)
    print(f"HUMAN REVIEW REQUIRED — {invoice.invoice_number}  vendor={invoice.vendor_name!r}")
    print("=" * 72)
    if invoice.stated_total:
        print(f"Total: ${invoice.stated_total.amount_usd} {invoice.stated_total.currency}")
    print(f"Source: {invoice.source_file}")
    print()
    print("Findings:")
    for f in findings:
        print(f"  [{f.severity.value:8s}] {f.code}: {f.message}")
    print()
    print("Adjudicator rationale:")
    print(f"  {decision.rationale}")
    if scribe_note:
        print()
        print("Scribe note:")
        print(f"  {scribe_note}")
    print("-" * 72)


def _read_clerk_answer() -> tuple[str, str]:
    """Read APPROVE / REJECT / HOLD from stdin. Retries on invalid input.
    Also accepts a short free-text note appended after the outcome."""
    while True:
        try:
            raw = input("Decision [approve/reject/hold]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return ("HOLD", "no answer provided (stdin closed) — held for later review")
        if not raw:
            continue
        parts = raw.split(maxsplit=1)
        outcome = parts[0].upper()
        note = parts[1] if len(parts) > 1 else ""
        if outcome in VALID_HUMAN_OUTCOMES:
            return (outcome, note or f"clerk decision at {datetime.now(timezone.utc).isoformat()}")
        print(f"  invalid: {outcome!r}. Try approve / reject / hold.")
