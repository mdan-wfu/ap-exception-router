"""Human-gate resolution — the outside-the-graph half of `interrupt()`.

The `human_gate` node calls `interrupt(packet)` unconditionally to suspend
the graph when the model's decision is ESCALATE. This module supplies the
resume value based on `HUMAN_GATE_MODE`:

  - "demo"        — auto-resolve from data/fixtures/human_gate.json.
  - "interactive" — prompt on stdin; block until the clerk answers.
  - "queue"       — default to HOLD so the run completes cleanly with a
                    proper audit record. Post-completion clerk decisions
                    go through the dashboard's override path.

The `source` field on the returned dict lands in `nodes_fired` so a
reader can tell which path resolved the escalation without opening the
audit store. The demo fixture's `default` entry is used for any
invoice number not explicitly listed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from src.config import HUMAN_GATE_FIXTURE_PATH, HUMAN_GATE_MODE


VALID = {"APPROVE", "REJECT", "HOLD"}


def resolve_interrupt(packet: dict) -> dict:
    """Turn a human_gate interrupt payload into a resume value. Always
    returns a dict with {outcome, note, source}."""
    mode = os.environ.get("HUMAN_GATE_MODE", HUMAN_GATE_MODE)
    if mode == "interactive":
        return _prompt_stdin(packet)
    if mode == "queue":
        return {
            "outcome": "HOLD",
            "note": "queued for review — awaiting clerk decision",
            "source": "queued",
        }
    return _demo(packet)


# ---------------------------------------------------------------------------
# Demo mode — fixture lookup
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_fixture() -> dict:
    path = Path(HUMAN_GATE_FIXTURE_PATH)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _demo(packet: dict) -> dict:
    invoice_number = packet.get("invoice_number", "")
    fixture = _load_fixture()
    entry = fixture.get(invoice_number) or fixture.get("default") or {}
    outcome = str(entry.get("outcome", "HOLD")).upper()
    if outcome not in VALID:
        outcome = "HOLD"
    return {
        "outcome": outcome,
        "note": entry.get("note", "demo fixture default"),
        "source": "demo",
    }


# ---------------------------------------------------------------------------
# Interactive mode — stdin prompt
# ---------------------------------------------------------------------------

def _prompt_stdin(packet: dict) -> dict:
    _print_packet(packet)
    while True:
        try:
            raw = input("Decision [approve/reject/hold]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return {
                "outcome": "HOLD",
                "note": "no answer provided (stdin closed) — held for later review",
                "source": "interactive",
            }
        if not raw:
            continue
        parts = raw.split(maxsplit=1)
        outcome = parts[0].upper()
        note = parts[1] if len(parts) > 1 else ""
        if outcome in VALID:
            return {
                "outcome": outcome,
                "note": note or f"clerk decision at {datetime.now(timezone.utc).isoformat()}",
                "source": "interactive",
            }
        print(f"  invalid: {outcome!r}. Try approve / reject / hold.")


def _print_packet(packet: dict) -> None:
    print("\n" + "=" * 72)
    print(
        f"HUMAN REVIEW REQUIRED — {packet.get('invoice_number')}"
        f"  vendor={packet.get('vendor_name')!r}"
    )
    print("=" * 72)
    total = packet.get("stated_total_usd")
    currency = packet.get("currency") or "USD"
    if total is not None:
        print(f"Total: ${total} {currency}")
    print()
    print("Findings:")
    for f in packet.get("findings", []):
        print(f"  [{f['severity']:8s}] {f['code']}: {f['message']}")
    print()
    print("Adjudicator rationale:")
    print(f"  {packet.get('rationale', '')}")
    scribe = packet.get("scribe_note")
    if scribe:
        print()
        print("Scribe note:")
        print(f"  {scribe}")
    print("-" * 72)
