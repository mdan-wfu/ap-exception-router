"""Minimal audit store for the vendor-history and prior-invoice tools.

Phase 6 fills this in with the real RunRecord persistence. For now it is
a thin SQLite wrapper that returns empty results on an empty store —
which is the correct answer for a first-time vendor or a first-time
invoice number.

The schema is intentionally narrow: only the fields the two tools read.
Phase 6 can extend it without breaking the tool contracts.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_DB = REPO_ROOT / "runs" / "audit.sqlite"


@dataclass(frozen=True)
class RunHistoryRow:
    invoice_number: str
    vendor_name: str
    stated_total_usd: Decimal
    semantic_hash: str
    source_file: str
    outcome: str            # APPROVE | REJECT | ESCALATE | FAILED
    finished_at: str        # ISO-8601


class AuditStore:
    def __init__(self, path: Path | str = DEFAULT_AUDIT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS run_records (
                    invoice_number    TEXT NOT NULL,
                    vendor_name       TEXT NOT NULL,
                    stated_total_usd  REAL NOT NULL,
                    semantic_hash     TEXT NOT NULL,
                    source_file       TEXT NOT NULL,
                    outcome           TEXT NOT NULL,
                    finished_at       TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_records_number ON run_records(invoice_number)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_records_vendor ON run_records(vendor_name)"
            )
            conn.commit()

    # -- Writes (used by tests now, by Phase 6 later) ---------------------

    def record(self, row: RunHistoryRow) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO run_records "
                "(invoice_number, vendor_name, stated_total_usd, semantic_hash, "
                " source_file, outcome, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row.invoice_number, row.vendor_name, float(row.stated_total_usd),
                    row.semantic_hash, row.source_file, row.outcome, row.finished_at,
                ),
            )
            conn.commit()

    # -- Reads ------------------------------------------------------------

    def vendor_history_rows(self, vendor_name: str) -> list[RunHistoryRow]:
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT invoice_number, vendor_name, stated_total_usd, semantic_hash, "
                "source_file, outcome, finished_at "
                "FROM run_records WHERE lower(vendor_name) = lower(?) "
                "ORDER BY finished_at",
                (vendor_name,),
            )
            return [
                RunHistoryRow(
                    invoice_number=r[0],
                    vendor_name=r[1],
                    stated_total_usd=Decimal(str(r[2])),
                    semantic_hash=r[3],
                    source_file=r[4],
                    outcome=r[5],
                    finished_at=r[6],
                )
                for r in cursor.fetchall()
            ]

    def prior_invoice_row(self, invoice_number: str) -> RunHistoryRow | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT invoice_number, vendor_name, stated_total_usd, semantic_hash, "
                "source_file, outcome, finished_at "
                "FROM run_records WHERE invoice_number = ? "
                "ORDER BY finished_at LIMIT 1",
                (invoice_number,),
            ).fetchone()
            if row is None:
                return None
            return RunHistoryRow(
                invoice_number=row[0],
                vendor_name=row[1],
                stated_total_usd=Decimal(str(row[2])),
                semantic_hash=row[3],
                source_file=row[4],
                outcome=row[5],
                finished_at=row[6],
            )
