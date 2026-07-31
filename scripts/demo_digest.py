"""Canonical projection of `runs/audit.sqlite` after `make demo`.

The property this locks is per-invoice semantics — outcome, findings,
cost, node sequence, scribe conclusion — NOT CLI presentation. See
DECISIONS 2026-07-31 demo-digest-replaces-stdout-hash for why.

Prints one line per invoice (sorted by invoice_number), then the md5
of the concatenated projection as the final line. That final md5 is
the enforceable regression check.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from src.config import AUDIT_DB_PATH


def _project() -> tuple[list[str], str]:
    """Return (line_per_invoice, md5_of_concatenation).

    Fields:
      invoice_number
      | outcome                     (model's outcome — audit-store field)
      | human_outcome | none        (or `none` literal)
      | sorted finding codes, comma-joined
      | stated_total_usd rounded to 2dp
      | summed model-call cost_usd rounded to 2dp
      | sorted nodes_fired, comma-joined
      | sha256 of scribe_note (or `none`)
      | settlement_type (or `none`)
    """
    if not Path(AUDIT_DB_PATH).exists():
        raise SystemExit(
            f"no audit store at {AUDIT_DB_PATH}. Run `make demo` first."
        )
    conn = sqlite3.connect(str(AUDIT_DB_PATH))
    conn.row_factory = sqlite3.Row

    runs = conn.execute("""
        SELECT id, invoice_number, outcome, human_outcome,
               stated_total_usd, scribe_note, nodes_fired
        FROM runs ORDER BY invoice_number
    """).fetchall()

    lines: list[str] = []
    for r in runs:
        codes = sorted(row["code"] for row in conn.execute(
            "SELECT code FROM findings WHERE run_id = ?", (r["id"],)
        ).fetchall())
        cost = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM model_calls WHERE run_id = ?",
            (r["id"],),
        ).fetchone()[0]
        settlement = conn.execute("""
            SELECT settlement_type FROM settlements
            WHERE invoice_number = ?
              AND lower(COALESCE(vendor_name, '')) = lower(COALESCE(?, ''))
            ORDER BY id DESC LIMIT 1
        """, (r["invoice_number"], _vendor_of(conn, r["id"]))).fetchone()
        settlement_type = settlement["settlement_type"] if settlement else "none"

        nodes = sorted(json.loads(r["nodes_fired"]) if r["nodes_fired"] else [])
        scribe_hash = (
            hashlib.sha256(r["scribe_note"].encode()).hexdigest()[:16]
            if r["scribe_note"] else "none"
        )
        line = " | ".join([
            r["invoice_number"],
            r["outcome"],
            r["human_outcome"] or "none",
            ",".join(codes) if codes else "-",
            f"{float(r['stated_total_usd'] or 0):.2f}",
            f"{float(cost):.2f}",
            ",".join(nodes),
            scribe_hash,
            settlement_type,
        ])
        lines.append(line)

    conn.close()
    blob = "\n".join(lines).encode()
    return lines, hashlib.md5(blob).hexdigest()


def _vendor_of(conn: sqlite3.Connection, run_id: int) -> str:
    r = conn.execute(
        "SELECT vendor_name FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    return (r["vendor_name"] or "") if r else ""


def main() -> int:
    lines, digest = _project()
    print("# demo-digest — canonical projection of runs/audit.sqlite")
    print("# format: invoice | outcome | human_outcome | findings | total | cost | nodes | scribe_hash | settlement")
    for line in lines:
        print(line)
    print()
    print(f"md5: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
