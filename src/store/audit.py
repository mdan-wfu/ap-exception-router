"""Audit persistence — the queryable record of everything the pipeline did.

Design principle: **queryable, not just dumped**. The dashboard in Phase 11
needs to answer "show all escalations", "what did the Adjudicator ask on
INV-1012", "what did this cost" without deserializing every blob. Findings
and model calls live as related rows, not a JSON column on `runs`.

Schema:

  runs             one row per invoice run (identifiers, outcome, timing,
                   human resolution if any, scribe note)
  findings         one row per Finding — indexed on code and severity
  model_calls      one row per LLM call — full four-category token breakdown,
                   cost recomputed at read time from tokens (never frozen)
  tool_calls       one row per read-only lookup — arguments and result as JSON
  settlements      one row per settlement (PAID | REJECTED | QUEUED),
                   idempotency: a PAID row on (invoice_number, vendor_name)
                   cannot be inserted twice — see prior_paid_settlement()

Separate DB from reference.db. Gitignored via the *.db pattern. Rebuilt by
`make audit-reset`.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.config import AUDIT_DB_PATH


# ---------------------------------------------------------------------------
# Row types the tool layer reads (kept minimal — DB has more columns)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunHistoryRow:
    invoice_number: str
    vendor_name: str
    stated_total_usd: Decimal
    semantic_hash: str
    source_file: str
    outcome: str
    finished_at: str


@dataclass(frozen=True)
class PriorSettlement:
    invoice_number: str
    vendor_name: str
    settlement_type: str        # "PAID" | "REJECTED" | "QUEUED"
    amount_usd: Decimal | None
    mock_payment_ref: str | None
    settled_at: str


# ---------------------------------------------------------------------------
# AuditStore
# ---------------------------------------------------------------------------

class AuditStore:
    def __init__(self, path: Path | str | None = None) -> None:
        # Resolve at call time (not def time) so tests can monkeypatch
        # src.config.AUDIT_DB_PATH and the change flows through to instances
        # constructed inside node bodies with no explicit path argument.
        if path is None:
            from src import config as _cfg
            path = _cfg.AUDIT_DB_PATH
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # -- Schema ---------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_number            TEXT NOT NULL,
                    vendor_name               TEXT,
                    source_file               TEXT NOT NULL,
                    source_format             TEXT,
                    stated_total_usd          REAL,
                    currency                  TEXT,
                    semantic_hash             TEXT,
                    outcome                   TEXT NOT NULL,
                    rationale                 TEXT,
                    critic_challenge          TEXT,
                    revision_occurred         INTEGER,
                    guardrail_override_fired  INTEGER,
                    guardrail_override_reason TEXT,
                    scribe_note               TEXT,
                    nodes_fired               TEXT,   -- JSON array
                    started_at                TEXT,
                    finished_at               TEXT NOT NULL,
                    terminal_status           TEXT NOT NULL,
                    failure_reason            TEXT,
                    human_outcome             TEXT,   -- APPROVE | REJECT | HOLD | NULL
                    human_note                TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_runs_invoice_number ON runs(invoice_number);
                CREATE INDEX IF NOT EXISTS idx_runs_vendor         ON runs(vendor_name);
                CREATE INDEX IF NOT EXISTS idx_runs_outcome        ON runs(outcome);
                CREATE INDEX IF NOT EXISTS idx_runs_finished_at    ON runs(finished_at);

                CREATE TABLE IF NOT EXISTS findings (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    code        TEXT NOT NULL,
                    severity    TEXT NOT NULL,
                    message     TEXT,
                    evidence    TEXT,
                    field_path  TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_findings_run_id   ON findings(run_id);
                CREATE INDEX IF NOT EXISTS idx_findings_code     ON findings(code);
                CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);

                CREATE TABLE IF NOT EXISTS model_calls (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id                INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    prompt_name           TEXT,
                    requested_model       TEXT,
                    resolved_model        TEXT,
                    system_fingerprint    TEXT,
                    prompt_tokens         INTEGER,
                    cached_prompt_tokens  INTEGER,
                    completion_tokens     INTEGER,
                    reasoning_tokens      INTEGER,
                    latency_ms            REAL,
                    cost_usd              REAL,       -- derived at write time from tokens
                    timestamp             TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_model_calls_run_id      ON model_calls(run_id);
                CREATE INDEX IF NOT EXISTS idx_model_calls_prompt_name ON model_calls(prompt_name);

                CREATE TABLE IF NOT EXISTS tool_calls (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    name         TEXT NOT NULL,
                    arguments    TEXT,   -- JSON
                    result       TEXT,   -- JSON
                    latency_ms   REAL,
                    timestamp    TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tool_calls_run_id ON tool_calls(run_id);
                CREATE INDEX IF NOT EXISTS idx_tool_calls_name   ON tool_calls(name);

                CREATE TABLE IF NOT EXISTS settlements (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id            INTEGER REFERENCES runs(id) ON DELETE CASCADE,
                    invoice_number    TEXT NOT NULL,
                    vendor_name       TEXT NOT NULL,
                    settlement_type   TEXT NOT NULL,   -- PAID | REJECTED | QUEUED
                    amount_usd        REAL,
                    mock_payment_ref  TEXT,
                    reason            TEXT,
                    settled_at        TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_settlements_dedup ON settlements(invoice_number, vendor_name, settlement_type);
                CREATE INDEX IF NOT EXISTS idx_settlements_run_id ON settlements(run_id);
            """)
            conn.commit()

    # -- Writes ---------------------------------------------------------------

    def record_run(self, *, invoice, decision, findings, scribe_note: str | None,
                   nodes_fired: list[str], model_calls: list,
                   tool_calls: list, started_at: str | None, finished_at: str,
                   terminal_status: str, failure_reason: str | None,
                   human_outcome: str | None, human_note: str | None,
                   revision_occurred: bool, guardrail_override_fired: bool,
                   guardrail_override_reason: str | None,
                   critic_challenges: list[str]) -> int:
        """Insert one row into `runs` and its dependent tables. Returns run_id."""
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO runs "
                "(invoice_number, vendor_name, source_file, source_format, "
                " stated_total_usd, currency, semantic_hash, outcome, rationale, "
                " critic_challenge, revision_occurred, guardrail_override_fired, "
                " guardrail_override_reason, scribe_note, nodes_fired, started_at, "
                " finished_at, terminal_status, failure_reason, human_outcome, human_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    invoice.invoice_number,
                    invoice.vendor_name,
                    invoice.source_file,
                    invoice.source_format,
                    float(invoice.stated_total.amount_usd) if invoice.stated_total else None,
                    invoice.stated_total.currency if invoice.stated_total else None,
                    invoice.semantic_hash,
                    decision.outcome.value if decision else "FAILED",
                    decision.rationale if decision else None,
                    "; ".join(critic_challenges) if critic_challenges else None,
                    int(bool(revision_occurred)),
                    int(bool(guardrail_override_fired)),
                    guardrail_override_reason,
                    scribe_note,
                    json.dumps(nodes_fired),
                    started_at,
                    finished_at,
                    terminal_status,
                    failure_reason,
                    human_outcome,
                    human_note,
                ),
            )
            run_id = cursor.lastrowid

            for f in findings:
                conn.execute(
                    "INSERT INTO findings (run_id, code, severity, message, evidence, field_path) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, f.code, f.severity.value, f.message, f.evidence, f.field_path),
                )
            for mc in model_calls:
                conn.execute(
                    "INSERT INTO model_calls "
                    "(run_id, prompt_name, requested_model, resolved_model, system_fingerprint, "
                    " prompt_tokens, cached_prompt_tokens, completion_tokens, reasoning_tokens, "
                    " latency_ms, cost_usd, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id, mc.prompt_name, mc.requested_model, mc.resolved_model,
                        mc.system_fingerprint, mc.prompt_tokens, mc.cached_prompt_tokens,
                        mc.completion_tokens, mc.reasoning_tokens, mc.latency_ms,
                        float(mc.cost_usd), mc.timestamp.isoformat(),
                    ),
                )
            for tc in tool_calls:
                conn.execute(
                    "INSERT INTO tool_calls (run_id, name, arguments, result, latency_ms, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        run_id, tc.name,
                        json.dumps(tc.arguments, sort_keys=True),
                        json.dumps(tc.result, sort_keys=True, default=str),
                        tc.latency_ms, tc.timestamp.isoformat(),
                    ),
                )
            conn.commit()
            return run_id

    def record_failed_run(self, *, source_file: str, invoice_number: str,
                          source_format: str | None, error_type: str,
                          error_message: str, node: str | None) -> int:
        """Persist a FAILED run when the pipeline crashed before producing an
        Invoice or a Decision (typically inside triage/extraction). The row
        has no findings, no model_calls, no tool_calls attached — those live
        on the child tables, which required a run_id we didn't have when the
        exception propagated. Everything a reviewer needs to understand what
        happened lives on the runs row itself: source_file, terminal_status
        FAILED, failure_reason (type + message), and nodes_fired = [node]."""
        with self._conn() as conn:
            reason = f"{error_type}: {error_message}"
            nodes_fired = json.dumps([node] if node else [])
            cursor = conn.execute(
                "INSERT INTO runs "
                "(invoice_number, vendor_name, source_file, source_format, "
                " stated_total_usd, currency, semantic_hash, outcome, rationale, "
                " critic_challenge, revision_occurred, guardrail_override_fired, "
                " guardrail_override_reason, scribe_note, nodes_fired, started_at, "
                " finished_at, terminal_status, failure_reason, human_outcome, human_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    invoice_number,
                    None,                       # vendor_name
                    source_file,
                    source_format,
                    None,                       # stated_total_usd
                    None,                       # currency
                    None,                       # semantic_hash
                    "FAILED",                   # outcome
                    None,                       # rationale
                    None,                       # critic_challenge
                    0, 0,                       # revision_occurred, guardrail_override_fired
                    None,                       # guardrail_override_reason
                    None,                       # scribe_note
                    nodes_fired,
                    None,                       # started_at
                    datetime.now(timezone.utc).isoformat(),
                    "FAILED",                   # terminal_status
                    reason,                     # failure_reason
                    None, None,                 # human_outcome, human_note
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def record_settlement(self, *, run_id: int | None, invoice_number: str,
                          vendor_name: str, settlement_type: str,
                          amount_usd: Decimal | None, mock_payment_ref: str | None,
                          reason: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settlements "
                "(run_id, invoice_number, vendor_name, settlement_type, "
                " amount_usd, mock_payment_ref, reason, settled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, invoice_number, vendor_name, settlement_type,
                    float(amount_usd) if amount_usd is not None else None,
                    mock_payment_ref, reason,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    # -- Reads (tool layer) ---------------------------------------------------

    def has_any_runs(self) -> bool:
        """True iff the audit store has recorded at least one run.

        Distinguishing 'store empty' from 'no matching record' is what
        prevents the Adjudicator from treating an infrastructure gap as a
        business fact (the INV-1004 bug — see DECISIONS.md)."""
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM runs LIMIT 1").fetchone()
            return row is not None

    def prior_invoice_row(self, invoice_number: str) -> RunHistoryRow | None:
        """Most recent SETTLED run for this invoice number, if any."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT r.invoice_number, r.vendor_name, r.stated_total_usd, "
                "       r.semantic_hash, r.source_file, r.outcome, r.finished_at "
                "FROM runs r "
                "JOIN settlements s ON s.run_id = r.id "
                "WHERE r.invoice_number = ? "
                "  AND s.settlement_type IN ('PAID', 'REJECTED') "
                "ORDER BY r.finished_at DESC LIMIT 1",
                (invoice_number,),
            ).fetchone()
        if row is None:
            return None
        return RunHistoryRow(
            invoice_number=row[0], vendor_name=row[1],
            stated_total_usd=Decimal(str(row[2])) if row[2] is not None else Decimal("0"),
            semantic_hash=row[3] or "",
            source_file=row[4], outcome=row[5], finished_at=row[6],
        )

    def vendor_history_rows(self, vendor_name: str) -> list[RunHistoryRow]:
        """All settled runs for this vendor, oldest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT r.invoice_number, r.vendor_name, r.stated_total_usd, "
                "       r.semantic_hash, r.source_file, r.outcome, r.finished_at "
                "FROM runs r "
                "JOIN settlements s ON s.run_id = r.id "
                "WHERE lower(r.vendor_name) = lower(?) "
                "  AND s.settlement_type IN ('PAID', 'REJECTED') "
                "ORDER BY r.finished_at ASC",
                (vendor_name,),
            ).fetchall()
        return [
            RunHistoryRow(
                invoice_number=r[0], vendor_name=r[1],
                stated_total_usd=Decimal(str(r[2])) if r[2] is not None else Decimal("0"),
                semantic_hash=r[3] or "",
                source_file=r[4], outcome=r[5], finished_at=r[6],
            )
            for r in rows
        ]

    def prior_paid_settlement(self, invoice_number: str,
                              vendor_name: str) -> PriorSettlement | None:
        """Idempotency check: has this (invoice_number, vendor_name) already
        been PAID? A hit here means refuse settlement to prevent double-pay."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT invoice_number, vendor_name, settlement_type, amount_usd, "
                "       mock_payment_ref, settled_at FROM settlements "
                "WHERE invoice_number = ? AND vendor_name = ? "
                "  AND settlement_type = 'PAID' LIMIT 1",
                (invoice_number, vendor_name),
            ).fetchone()
        if row is None:
            return None
        return PriorSettlement(
            invoice_number=row[0], vendor_name=row[1], settlement_type=row[2],
            amount_usd=Decimal(str(row[3])) if row[3] is not None else None,
            mock_payment_ref=row[4], settled_at=row[5],
        )

    # -- Test / legacy compat -------------------------------------------------

    def record(self, row: RunHistoryRow) -> None:
        """Compatibility shim: record a minimal run row (invoice_number,
        vendor, total, hash, source, outcome, finished_at) plus a matching
        settlement so vendor_history_rows / prior_invoice_row see it.

        Used by existing tests in tests/test_tools.py. New code should use
        record_run() with the full RunRecord context.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO runs "
                "(invoice_number, vendor_name, source_file, stated_total_usd, "
                " semantic_hash, outcome, finished_at, terminal_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.invoice_number, row.vendor_name, row.source_file,
                    float(row.stated_total_usd), row.semantic_hash, row.outcome,
                    row.finished_at, row.outcome,
                ),
            )
            run_id = cursor.lastrowid
            # Settlement so reads work
            conn.execute(
                "INSERT INTO settlements "
                "(run_id, invoice_number, vendor_name, settlement_type, "
                " amount_usd, settled_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id, row.invoice_number, row.vendor_name,
                    "PAID" if row.outcome == "APPROVE" else "REJECTED",
                    float(row.stated_total_usd), row.finished_at,
                ),
            )
            conn.commit()
