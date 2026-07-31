"""Read-only queries against the audit store — no pipeline execution.

Every dashboard view is answered from `runs/audit.sqlite` (populated by
`make demo` / `make demo-adversarial`) plus a re-extraction pass over the
source file to recover fields the audit store doesn't persist
(line items, corrections, extraction confidence). Re-extraction hits
the extractor cassettes in replay mode — zero API calls.

Provided-corpus and authored-adversarial invoices are distinguished by
their `source_file` prefix and never blended in aggregate metrics.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.config import AUDIT_DB_PATH


PROVIDED_DIR = "data/invoices"
ADVERSARIAL_DIR = "data/adversarial"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(AUDIT_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def corpus_of(source_file: str) -> str:
    """Which set the invoice belongs to. The dashboard uses this to keep
    provided and authored numbers visibly separate."""
    if ADVERSARIAL_DIR in source_file:
        return "adversarial"
    return "provided"


# ---------------------------------------------------------------------------
# Queue view
# ---------------------------------------------------------------------------

def list_runs() -> list[dict[str, Any]]:
    """Every run in the store, one row per invoice, sorted so escalations
    surface first (the manager's actual job)."""
    with _conn() as c:
        rows = c.execute("""
            SELECT r.id, r.invoice_number, r.vendor_name, r.source_file,
                   r.source_format, r.stated_total_usd, r.currency, r.outcome,
                   r.human_outcome, r.rationale,
                   (SELECT COUNT(*) FROM findings f WHERE f.run_id = r.id) AS n_findings,
                   (SELECT COALESCE(SUM(cost_usd), 0) FROM model_calls m
                    WHERE m.run_id = r.id) AS cost_usd,
                   (SELECT COUNT(*) FROM tool_calls t WHERE t.run_id = r.id) AS n_tools,
                   (SELECT COUNT(*) FROM model_calls m WHERE m.run_id = r.id) AS n_calls
            FROM runs r
            ORDER BY r.invoice_number
        """).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["corpus"] = corpus_of(d["source_file"])
        d["is_duplicate_pair"] = d["invoice_number"] == "INV-1004"
        result.append(d)
    # escalations first, then rejects, then approves — the manager's ordering
    order = {"ESCALATE": 0, "REJECT": 1, "APPROVE": 2, "FAILED": 3}
    result.sort(key=lambda x: (order.get(x["outcome"], 9), x["invoice_number"]))
    return result


def corpus_summary(corpus: str) -> dict[str, Any]:
    """Aggregate metrics for one corpus (provided or adversarial)."""
    rows = [r for r in list_runs() if r["corpus"] == corpus]
    total = len(rows)
    if total == 0:
        return {"total": 0, "approve": 0, "reject": 0, "escalate": 0,
                "cost": 0.0, "cost_per_invoice": 0.0, "straight_through_pct": 0.0}
    by_outcome = {"APPROVE": 0, "REJECT": 0, "ESCALATE": 0}
    for r in rows:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
    total_cost = sum(float(r["cost_usd"] or 0) for r in rows)
    straight_through = sum(
        1 for r in rows if r["outcome"] == "APPROVE" and not r["human_outcome"]
    )
    return {
        "total": total,
        "approve": by_outcome["APPROVE"],
        "reject": by_outcome["REJECT"],
        "escalate": by_outcome["ESCALATE"],
        "cost": total_cost,
        "cost_per_invoice": total_cost / total,
        "straight_through_pct": 100 * straight_through / total,
        "queue_depth": by_outcome["ESCALATE"],
    }


def findings_by_prefix(corpus: str) -> list[tuple[str, str, int]]:
    """(prefix, domain, count) sorted descending by count."""
    with _conn() as c:
        rows = c.execute("""
            SELECT f.code, r.source_file FROM findings f
            JOIN runs r ON f.run_id = r.id
        """).fetchall()
    filtered = [r for r in rows if corpus_of(r["source_file"]) == corpus]
    domains = {
        "EX": "extraction", "AR": "arithmetic", "IN": "inventory",
        "PR": "pricing", "VN": "vendor", "TM": "terms",
        "DP": "duplicates", "PO": "policy", "FR": "fraud signals",
    }
    counts: dict[str, int] = {}
    for r in filtered:
        prefix = r["code"].split("-")[0]
        counts[prefix] = counts.get(prefix, 0) + 1
    return sorted(
        [(p, domains.get(p, ""), n) for p, n in counts.items()],
        key=lambda t: -t[2],
    )


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------

def get_run(invoice_number: str) -> dict[str, Any] | None:
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM runs WHERE invoice_number = ? ORDER BY id DESC LIMIT 1",
            (invoice_number,),
        ).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["corpus"] = corpus_of(d["source_file"])
    d["nodes_fired"] = json.loads(d["nodes_fired"]) if d["nodes_fired"] else []
    return d


def get_findings(run_id: int) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("""
            SELECT code, severity, message, evidence, field_path
            FROM findings WHERE run_id = ?
            ORDER BY CASE severity
                WHEN 'CRITICAL' THEN 0
                WHEN 'HIGH' THEN 1
                WHEN 'MEDIUM' THEN 2
                WHEN 'LOW' THEN 3
                WHEN 'INFO' THEN 4
                ELSE 5 END,
                code
        """, (run_id,)).fetchall()
    return [dict(r) for r in rows]


def get_tool_calls(run_id: int) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("""
            SELECT id, name, arguments, result, latency_ms
            FROM tool_calls WHERE run_id = ? ORDER BY id
        """, (run_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["arguments"] = json.loads(d["arguments"]) if d["arguments"] else {}
        except (TypeError, ValueError):
            pass
        try:
            d["result"] = json.loads(d["result"]) if d["result"] else {}
        except (TypeError, ValueError):
            pass
        out.append(d)
    return out


def get_model_calls(run_id: int) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("""
            SELECT prompt_name, prompt_tokens, cached_prompt_tokens,
                   completion_tokens, reasoning_tokens, latency_ms, cost_usd
            FROM model_calls WHERE run_id = ? ORDER BY id
        """, (run_id,)).fetchall()
    return [dict(r) for r in rows]


def cost_by_node_type(run_id: int) -> list[dict[str, Any]]:
    """Collapse per-turn prompt_names into buckets for the cost table."""
    def bucket(name: str) -> str:
        if not name: return "(unknown)"
        if name.startswith("adjudicator_revised"): return "adjudicator_revised"
        if name.startswith("adjudicator"): return "adjudicator"
        if name.startswith("critic"): return "critic"
        if name.startswith("scribe"): return "scribe"
        if name.startswith("extractor"): return "extractor"
        return name

    calls = get_model_calls(run_id)
    agg: dict[str, dict[str, float]] = {}
    for c in calls:
        b = bucket(c["prompt_name"])
        d = agg.setdefault(b, {"node": b, "n": 0, "cost": 0.0,
                                "prompt": 0, "cached": 0, "completion": 0, "reasoning": 0})
        d["n"] += 1
        d["cost"] += float(c["cost_usd"] or 0)
        d["prompt"] += c["prompt_tokens"] or 0
        d["cached"] += c["cached_prompt_tokens"] or 0
        d["completion"] += c["completion_tokens"] or 0
        d["reasoning"] += c["reasoning_tokens"] or 0
    return sorted(agg.values(), key=lambda x: -x["cost"])


def get_settlement(invoice_number: str, vendor_name: str) -> dict[str, Any] | None:
    with _conn() as c:
        r = c.execute("""
            SELECT settlement_type, amount_usd, mock_payment_ref, reason, settled_at
            FROM settlements
            WHERE invoice_number = ? AND lower(vendor_name) = lower(?)
            ORDER BY id DESC LIMIT 1
        """, (invoice_number, vendor_name or "")).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# Re-extraction — pull the Invoice object back from source
# ---------------------------------------------------------------------------

def re_extract(source_file: str):
    """Re-run the adapter to recover extraction fields the audit store
    doesn't persist (line items, corrections, extraction_confidence).
    Deterministic adapters return instantly; LLM adapters hit their
    committed cassettes in replay mode. Zero API calls."""
    from src.adapters.router import extract as router_extract
    path = Path(source_file)
    if not path.exists():
        return None
    return router_extract(path)


def read_source_text(source_file: str) -> str:
    """Raw source contents for the left column of the detail view.
    For PDFs, hand back a pdfplumber-extracted text rendering — the model
    saw text, not the pixels."""
    path = Path(source_file)
    if not path.exists():
        return f"(source file not found: {source_file})"
    if path.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    return path.read_text()


# ---------------------------------------------------------------------------
# Duplicate diff (INV-1004)
# ---------------------------------------------------------------------------

def get_duplicate_pair(invoice_number: str) -> list[dict[str, Any]]:
    """Every file recorded under this invoice_number, with its Invoice re-
    extracted so the diff shows real extracted content, not just DB rows."""
    # For INV-1004: two files (invoice_1004.json + invoice_1004_revised.json).
    # The batch loop only processes the alphabetical-first per DP-002 rule
    # (Phase 10 duplicate-selection decision), so only one is in `runs`.
    # For the diff, load BOTH source files directly.
    if invoice_number == "INV-1004":
        files = [
            "data/invoices/invoice_1004.json",
            "data/invoices/invoice_1004_revised.json",
        ]
    else:
        # Generic: query runs table + also probe sibling files
        with _conn() as c:
            rows = c.execute(
                "SELECT DISTINCT source_file FROM runs WHERE invoice_number = ?",
                (invoice_number,),
            ).fetchall()
        files = [r["source_file"] for r in rows]

    entries = []
    for f in files:
        ext = re_extract(f)
        if ext is None:
            continue
        entries.append({
            "source_file": f,
            "adapter_used": ext.adapter_used,
            "invoice": ext.invoice,
            "raw_text": read_source_text(f),
        })
    return entries


# ---------------------------------------------------------------------------
# Human queue actions
# ---------------------------------------------------------------------------

def record_human_decision(
    invoice_number: str, human_outcome: str, human_note: str
) -> None:
    """Update the run's human_outcome + human_note. Never overwrites the
    model's decision (that stays in `outcome`), per Phase 6 policy."""
    with _conn() as c:
        c.execute("""
            UPDATE runs SET human_outcome = ?, human_note = ?
            WHERE id = (
                SELECT id FROM runs WHERE invoice_number = ?
                ORDER BY id DESC LIMIT 1
            )
        """, (human_outcome, human_note, invoice_number))
        c.commit()


def human_queue() -> list[dict[str, Any]]:
    """Escalated runs that haven't been resolved (no human_outcome yet)."""
    return [
        r for r in list_runs()
        if r["outcome"] == "ESCALATE" and not r.get("human_outcome")
    ]
