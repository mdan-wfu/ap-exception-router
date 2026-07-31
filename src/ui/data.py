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

PROVIDED_DIR = "data/invoices"
ADVERSARIAL_DIR = "data/adversarial"


def _conn() -> sqlite3.Connection:
    # Resolve at call time so tests that monkeypatch src.config.AUDIT_DB_PATH
    # after this module is imported still take effect (a from-import would
    # cache the module-load value and defeat isolation).
    from src import config as _cfg
    c = sqlite3.connect(str(_cfg.AUDIT_DB_PATH))
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
    """Every run in the store, one row per invoice.

    IMPORTANT — every row carries `effective_outcome`, the authoritative
    state after model → human → amendments has resolved. All views, filters,
    counts, tab-membership checks MUST route through this field (never the
    raw `outcome` column) — otherwise amended state doesn't reach the right
    view. See DECISIONS 2026-07-31 effective-outcome-everywhere for why
    this is enforced at the query layer rather than left to each view."""
    _ensure_amendments_table()
    with _conn() as c:
        rows = c.execute("""
            SELECT r.id, r.invoice_number, r.vendor_name, r.source_file,
                   r.source_format, r.stated_total_usd, r.currency, r.outcome,
                   r.human_outcome, r.human_note, r.rationale, r.scribe_note,
                   (SELECT COUNT(*) FROM findings f WHERE f.run_id = r.id) AS n_findings,
                   (SELECT COALESCE(SUM(cost_usd), 0) FROM model_calls m
                    WHERE m.run_id = r.id) AS cost_usd,
                   (SELECT COUNT(*) FROM tool_calls t WHERE t.run_id = r.id) AS n_tools,
                   (SELECT COUNT(*) FROM model_calls m WHERE m.run_id = r.id) AS n_calls
            FROM runs r
            ORDER BY r.invoice_number
        """).fetchall()
        # Latest amendment per invoice, so we compute effective_outcome
        amend_rows = c.execute("""
            SELECT invoice_number, new_outcome
            FROM decision_amendments
            WHERE id IN (
                SELECT MAX(id) FROM decision_amendments GROUP BY invoice_number
            )
        """).fetchall()
        latest_amendment = {r["invoice_number"]: r["new_outcome"] for r in amend_rows}
        # Amendment count per invoice (surfaces "amended" chip)
        counts = {
            r["invoice_number"]: r["n"]
            for r in c.execute("""
                SELECT invoice_number, COUNT(*) AS n
                FROM decision_amendments GROUP BY invoice_number
            """).fetchall()
        }

    result = []
    for r in rows:
        d = dict(r)
        d["corpus"] = corpus_of(d["source_file"])
        d["is_duplicate_pair"] = d["invoice_number"] == "INV-1004"
        d["auto_resolved"] = _is_auto_resolved(d["human_note"])
        d["amendment_count"] = counts.get(d["invoice_number"], 0)
        d["latest_amendment"] = latest_amendment.get(d["invoice_number"])
        # Effective outcome: amendment > human > model.
        d["effective_outcome"] = (
            d["latest_amendment"] or d["human_outcome"] or d["outcome"]
        )
        # Membership shortcuts for view filters.
        d["is_resolved"] = d["effective_outcome"] in ("APPROVE", "REJECT")
        d["is_held"] = d["effective_outcome"] == "HOLD"
        d["is_awaiting"] = (
            d["outcome"] == "ESCALATE"
            and d["effective_outcome"] in ("ESCALATE", None)
        )
        d["is_straight_through"] = (
            d["outcome"] == "APPROVE"
            and not d["human_outcome"]
            and d["amendment_count"] == 0
        )
        # Attribution — three-way. Straight-through APPROVEs never touched
        # by a clerk must NOT be labeled "clerk" anywhere on the surface.
        # See DECISIONS 2026-07-31 straight-through-attribution.
        if d["auto_resolved"]:
            d["source_kind"] = "fixture"
            d["source_label"] = "demo fixture"
        elif d["human_outcome"] or d["amendment_count"] > 0:
            d["source_kind"] = "clerk"
            d["source_label"] = "clerk"
        else:
            d["source_kind"] = "system"
            d["source_label"] = "system · straight-through"
        result.append(d)

    # Enrich with due-date info for the queue's urgency sort. Re-extract
    # each source (deterministic adapters return instantly; LLM extractors
    # hit committed cassettes) — this is a page-load-time cost of ~200ms
    # for 20 invoices, all cache hits. Cheap enough not to warrant a
    # schema change to the runs table.
    _enrich_due_dates(result)

    # Sort so awaiting-decision items surface first — the manager's ordering.
    order = {"ESCALATE": 0, "REJECT": 1, "HOLD": 1, "APPROVE": 2, "FAILED": 3}
    result.sort(key=lambda x: (order.get(x["effective_outcome"], 9), x["invoice_number"]))
    return result


def _is_auto_resolved(note: str | None) -> bool:
    """Demo-mode fixture resolutions carry a note starting with 'demo fixture'.
    A dashboard reviewer must never mistake one of these for a real human
    decision — see B5 of the Phase 11 dashboard revision."""
    return bool(note) and note.strip().lower().startswith("demo fixture")


def _enrich_due_dates(rows: list[dict[str, Any]]) -> None:
    """Populate `due_date` / `due_date_status` / `days_until_due` / `sort_key`
    on each row via a re-extraction pass. Deterministic adapters return
    instantly; LLM extractors hit their committed cassettes in replay
    mode. Zero API calls. Mutates rows in place.

    Status values (see DECISIONS 2026-07-31 queue-null-date-sort-top):
      - "no_date"  → due_date null or unparseable (INV-1003 'yesterday',
                     INV-1009 null). Sorted to the TOP — an invoice you
                     can't date is a problem, not a low priority.
      - "overdue"  → past today
      - "due_soon" → within 7 days
      - "future"   → beyond 7 days"""
    from datetime import date, datetime
    today = date.today()
    for r in rows:
        ext, _err = re_extract(r["source_file"])
        due = ext.invoice.due_date if ext and ext.invoice else None
        due_parsed: date | None = None
        if due:
            try:
                due_parsed = datetime.fromisoformat(str(due)).date()
            except (ValueError, TypeError):
                due_parsed = None
        r["due_date"] = str(due) if due else None
        if due_parsed is None:
            r["due_date_status"] = "no_date"
            r["days_until_due"] = None
        else:
            delta = (due_parsed - today).days
            r["days_until_due"] = delta
            if delta < 0:
                r["due_date_status"] = "overdue"
            elif delta <= 7:
                r["due_date_status"] = "due_soon"
            else:
                r["due_date_status"] = "future"


def queue_sort_key(row: dict[str, Any], mode: str = "due_date") -> tuple:
    """Sort key for the worklist. `mode='due_date'` (default) puts
    unparseable dates first, then overdue soonest, then future ascending.
    `mode='amount'` sorts by Amount Payable descending (materiality)."""
    if mode == "amount":
        return (-float(row.get("stated_total_usd") or 0), row["invoice_number"])
    # due_date mode
    status = row.get("due_date_status", "no_date")
    days = row.get("days_until_due")
    # Priority buckets: no_date first (0), then by days (nulls -> 0 within bucket)
    status_bucket = {"no_date": 0, "overdue": 1, "due_soon": 2, "future": 3}
    return (status_bucket.get(status, 9),
            days if days is not None else 0,
            row["invoice_number"])


def queue_progress(rows: list[dict[str, Any]]) -> dict[str, int]:
    """`{ needed, done }` — for the "N of M reviewed" indicator above
    the queue. Anything that required a decision counts toward `needed`;
    anything with an effective terminal outcome counts toward `done`."""
    needed = sum(1 for r in rows if r["outcome"] == "ESCALATE")
    done = sum(
        1 for r in rows
        if r["outcome"] == "ESCALATE" and r["effective_outcome"] in ("APPROVE", "REJECT")
    )
    return {"needed": needed, "done": done}


def corpus_summary(corpus: str) -> dict[str, Any]:
    """Aggregate metrics for one corpus. Counts by EFFECTIVE outcome so
    amendments reach the numbers on the hero row, not just the detail page."""
    rows = [r for r in list_runs() if r["corpus"] == corpus]
    total = len(rows)
    if total == 0:
        return {"total": 0, "approve": 0, "reject": 0, "escalate": 0,
                "held": 0, "cost": 0.0, "cost_per_invoice": 0.0,
                "straight_through_pct": 0.0, "queue_depth": 0}
    by_eff = {"APPROVE": 0, "REJECT": 0, "ESCALATE": 0, "HOLD": 0}
    for r in rows:
        eff = r["effective_outcome"]
        by_eff[eff] = by_eff.get(eff, 0) + 1
    total_cost = sum(float(r["cost_usd"] or 0) for r in rows)
    straight = sum(1 for r in rows if r["is_straight_through"])
    awaiting = sum(1 for r in rows if r["is_awaiting"])
    return {
        "total": total,
        "approve": by_eff["APPROVE"],
        "reject": by_eff["REJECT"],
        "escalate": by_eff["ESCALATE"],
        "held": by_eff["HOLD"],
        "cost": total_cost,
        "cost_per_invoice": total_cost / total,
        "straight_through_pct": 100 * straight / total,
        "queue_depth": awaiting + by_eff["HOLD"],
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
    """Detail-view row lookup. Includes the same derived fields
    (effective_outcome / is_awaiting / is_held / is_resolved /
    amendment_count / auto_resolved) that list_runs computes, so the
    detail template can rely on them without a separate enrichment
    pass. See DECISIONS 2026-07-31 effective-outcome-everywhere."""
    _ensure_amendments_table()
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM runs WHERE invoice_number = ? ORDER BY id DESC LIMIT 1",
            (invoice_number,),
        ).fetchone()
        latest = c.execute("""
            SELECT new_outcome FROM decision_amendments
            WHERE invoice_number = ? ORDER BY id DESC LIMIT 1
        """, (invoice_number,)).fetchone()
        n_amend = c.execute("""
            SELECT COUNT(*) FROM decision_amendments WHERE invoice_number = ?
        """, (invoice_number,)).fetchone()[0]
    if r is None:
        return None
    d = dict(r)
    d["corpus"] = corpus_of(d["source_file"])
    d["nodes_fired"] = json.loads(d["nodes_fired"]) if d["nodes_fired"] else []
    d["auto_resolved"] = _is_auto_resolved(d.get("human_note"))
    d["amendment_count"] = n_amend
    d["latest_amendment"] = latest["new_outcome"] if latest else None
    d["effective_outcome"] = (
        d["latest_amendment"] or d.get("human_outcome") or d["outcome"]
    )
    d["is_resolved"] = d["effective_outcome"] in ("APPROVE", "REJECT")
    d["is_held"] = d["effective_outcome"] == "HOLD"
    d["is_awaiting"] = (
        d["outcome"] == "ESCALATE"
        and d["effective_outcome"] in ("ESCALATE", None)
    )
    d["is_straight_through"] = (
        d["outcome"] == "APPROVE"
        and not d.get("human_outcome")
        and d["amendment_count"] == 0
    )
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

def re_extract(source_file: str) -> tuple[Any, str | None]:
    """Re-run the adapter to recover extraction fields the audit store
    doesn't persist (line items, corrections, extraction_confidence).
    Deterministic adapters return instantly; LLM adapters hit their
    committed cassettes in replay mode. Zero API calls.

    Returns `(extraction, error_message)`:
      - success: `(ExtractionResult, None)`
      - missing file: `(None, "source file not found: ...")`
      - cassette miss on an LLM adapter, parse error, etc.: `(None, "<reason>")`

    The caller renders whatever the audit store has plus a small note where
    the enrichment would be, rather than 500-ing the page. Failure causes
    to expect: missing source file, a path recorded from a different
    checkout that no longer exists, a cassette miss for a file recorded
    under different conditions."""
    path = Path(source_file)
    if not path.exists():
        return None, f"source file not found: {source_file}"
    try:
        from src.adapters.router import extract as router_extract
        return router_extract(path), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


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

    # Degrade per row, not per page: a single unreadable source file must
    # not break the diff view — surface a placeholder entry for the missing
    # side and render whatever's readable for the rest.
    entries = []
    for f in files:
        ext, err = re_extract(f)
        if ext is None:
            entries.append({
                "source_file": f,
                "adapter_used": None,
                "invoice": None,
                "raw_text": _safe_source_text(f),
                "extraction_error": err,
            })
            continue
        entries.append({
            "source_file": f,
            "adapter_used": ext.adapter_used,
            "invoice": ext.invoice,
            "raw_text": _safe_source_text(f),
            "extraction_error": None,
        })
    return entries


def _safe_source_text(source_file: str) -> str:
    """read_source_text but never raises — swallows file-not-found so the
    duplicate-diff view still renders even if one member is missing."""
    try:
        return read_source_text(source_file)
    except Exception as exc:
        return f"(source unavailable: {type(exc).__name__}: {exc})"


# ---------------------------------------------------------------------------
# Human queue actions
# ---------------------------------------------------------------------------

def record_human_decision(
    invoice_number: str, human_outcome: str, human_note: str
) -> None:
    """Update the run's human_outcome + human_note. Never overwrites the
    model's decision (that stays in `outcome`), per Phase 6 policy.

    Also invokes settlement when the human decides APPROVE or REJECT — the
    bug this fixes: dashboard actions previously wrote the decision to the
    audit store but never triggered `mock_payment` / rejection settlement,
    so human-approved invoices never appeared in the Payments ledger. See
    DECISIONS 2026-07-31 dashboard-settlement-fix for why this is a direct
    settle-invocation rather than a LangGraph checkpoint resume (graph
    doesn't actually use interrupt() — human_gate returns synchronously
    in every mode — so completed runs have no paused state to resume)."""
    with _conn() as c:
        c.execute("""
            UPDATE runs SET human_outcome = ?, human_note = ?
            WHERE id = (
                SELECT id FROM runs WHERE invoice_number = ?
                ORDER BY id DESC LIMIT 1
            )
        """, (human_outcome, human_note, invoice_number))
        c.commit()
    # Settle only for terminal decisions. HOLD leaves the run resumable —
    # no settlement, item lands on /held for later action.
    if human_outcome in ("APPROVE", "REJECT"):
        _invoke_settlement(invoice_number, human_outcome, human_note)


def _invoke_settlement(
    invoice_number: str, effective_outcome: str, note: str,
) -> str | None:
    """Invoke the settle node's logic directly for a dashboard-driven human
    decision. Reconstructs the minimal state the settle node reads, then
    calls it. The settle node's own idempotency check (prior_paid_settlement
    guard on invoice+vendor) prevents double-payment; this path does NOT
    bypass it.

    Returns the settle_result string, or None if the invoice cannot be
    reconstructed (source file missing or unreadable — degrades
    gracefully rather than 500-ing the request)."""
    from src.nodes.settle import settle
    from src.schema import Decision, Outcome

    run = get_run(invoice_number)
    if run is None:
        return None
    ext, _err = re_extract(run["source_file"])
    if ext is None:
        return None

    # The settle node expects a Decision object. Rebuild from the run row.
    try:
        model_outcome = Outcome(run["outcome"])
    except ValueError:
        model_outcome = Outcome.ESCALATE
    decision = Decision(
        outcome=model_outcome,
        rationale=run["rationale"] or "",
        confidence=1.0,
    )

    state = {
        "invoice": ext.invoice,
        "decision": decision,
        "human_outcome": effective_outcome,
        "human_note": note,
        "human_queued": False,
    }
    try:
        result = settle(state)
    except Exception as exc:  # pragma: no cover
        return f"SETTLE_ERROR: {type(exc).__name__}: {exc}"
    return result.get("settlement_result")


def human_queue() -> list[dict[str, Any]]:
    """Runs awaiting a genuine human decision — filter on the EFFECTIVE
    outcome so amendments back to escalate (rare but possible) surface
    correctly, and amendments to APPROVE/REJECT/HOLD leave this view."""
    return [r for r in list_runs() if r["is_awaiting"]]


def held_queue() -> list[dict[str, Any]]:
    """Held items — effective outcome is HOLD, from either a fixture
    resolution, a dashboard action, OR an amendment. Held is never
    terminal; items remain fully actionable."""
    return [r for r in list_runs() if r["is_held"]]


def resolved_queue() -> list[dict[str, Any]]:
    """Runs whose EFFECTIVE outcome is APPROVE or REJECT (fixture, clerk,
    or amendment). Excludes HOLD — a held item is not resolved."""
    return [r for r in list_runs() if r["is_resolved"]]


# ---------------------------------------------------------------------------
# Payments ledger — every disbursement that actually fired
# ---------------------------------------------------------------------------

def payments_ledger() -> list[dict[str, Any]]:
    """Every PAID settlement in the store, most recent first, enriched
    with reversal state and authorizer attribution.

    Membership rule: an invoice appears here iff `mock_payment` fired
    for it — i.e. a PAID settlement row exists. That is NOT the same
    as "current effective outcome is APPROVE": an APPROVE that got
    amended to REJECT still shows here because the money left the
    building regardless of what the decision says now. A ledger that
    quietly dropped reversed payments would be worse than no ledger.
    See DECISIONS 2026-07-31 payments-membership-rule."""
    _ensure_amendments_table()
    with _conn() as c:
        # settlements.run_id is NULL by design (settle node writes the
        # settlement before route_outcome persists the run — Phase 6),
        # so we join by (invoice_number, vendor_name) instead.
        rows = c.execute("""
            SELECT s.invoice_number, s.vendor_name, s.amount_usd,
                   s.mock_payment_ref, s.settled_at,
                   r.source_file, r.outcome AS model_outcome,
                   r.human_outcome, r.human_note
            FROM settlements s
            LEFT JOIN runs r
              ON r.invoice_number = s.invoice_number
             AND lower(COALESCE(r.vendor_name, '')) = lower(COALESCE(s.vendor_name, ''))
            WHERE s.settlement_type = 'PAID'
            ORDER BY s.id DESC
        """).fetchall()
        latest_amend = {}
        amend_reason = {}
        for a in c.execute("""
            SELECT invoice_number, new_outcome, reason
            FROM decision_amendments
            WHERE id IN (
                SELECT MAX(id) FROM decision_amendments GROUP BY invoice_number
            )
        """).fetchall():
            latest_amend[a["invoice_number"]] = a["new_outcome"]
            amend_reason[a["invoice_number"]] = a["reason"]

    out = []
    for r in rows:
        d = dict(r)
        eff = latest_amend.get(d["invoice_number"]) or d["human_outcome"] or d["model_outcome"]
        d["reversal_required"] = eff != "APPROVE"
        d["reversal_reason"] = amend_reason.get(d["invoice_number"]) if d["reversal_required"] else None
        if d["human_outcome"] == "APPROVE":
            if _is_auto_resolved(d["human_note"]):
                d["authorizer"] = "demo fixture (auto)"
                d["authorizer_kind"] = "fixture"
            else:
                d["authorizer"] = "clerk"
                d["authorizer_kind"] = "clerk"
        else:
            d["authorizer"] = "system · straight-through"
            d["authorizer_kind"] = "system"
        d["corpus"] = corpus_of(d["source_file"] or "")
        out.append(d)
    return out


def payments_total() -> dict[str, Any]:
    rows = payments_ledger()
    total = sum(float(r["amount_usd"] or 0) for r in rows)
    return {
        "count": len(rows),
        "total_usd": total,
        "reversal_count": sum(1 for r in rows if r["reversal_required"]),
        "reversal_total_usd": sum(
            float(r["amount_usd"] or 0) for r in rows if r["reversal_required"]
        ),
    }


def current_hold_or_amendment_reason(invoice_number: str) -> dict[str, Any] | None:
    """The reason a reviewer needs to see above-the-fold on the detail
    page. Precedence: latest amendment > current HOLD note. Returns
    None if there's nothing to surface."""
    _ensure_amendments_table()
    with _conn() as c:
        a = c.execute("""
            SELECT new_outcome, reason, timestamp
            FROM decision_amendments
            WHERE invoice_number = ? ORDER BY id DESC LIMIT 1
        """, (invoice_number,)).fetchone()
    if a:
        return {"kind": "amendment", "outcome": a["new_outcome"],
                "reason": a["reason"], "at": a["timestamp"]}
    run = get_run(invoice_number)
    if run and run.get("human_outcome") == "HOLD" and run.get("human_note"):
        return {"kind": "hold", "outcome": "HOLD",
                "reason": run["human_note"], "at": run.get("finished_at")}
    return None


def demo_fixture_active() -> bool:
    """True if ANY run in the store carries a demo-fixture human_note.
    Used to decide whether the top-of-page 'demo mode' banner shows.
    See DECISIONS 2026-07-31 demo-banner-not-per-row."""
    return any(r["auto_resolved"] for r in list_runs())


def has_mixed_decisions(rows: list[dict[str, Any]]) -> bool:
    """True if a list contains BOTH fixture and non-fixture decisions.
    When a table mixes the two, per-row chips distinguish them; when
    every row is one kind, the banner carries it."""
    kinds = {bool(r["auto_resolved"]) for r in rows if r["effective_outcome"]}
    return len(kinds) > 1


# ---------------------------------------------------------------------------
# Amendments — append-only decision history (B7)
# ---------------------------------------------------------------------------

def _ensure_amendments_table() -> None:
    """Add-only schema — doesn't touch existing tables or LLM message
    content, no cassette risk. Idempotent."""
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS decision_amendments (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number            TEXT NOT NULL,
                timestamp                 TEXT NOT NULL,
                new_outcome               TEXT NOT NULL,
                reason                    TEXT NOT NULL,
                prior_outcome             TEXT,
                payment_reversal_required INTEGER NOT NULL DEFAULT 0,
                mock_payment_ref          TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_amendments_invoice
                ON decision_amendments(invoice_number);
        """)
        c.commit()


def record_amendment(
    invoice_number: str, new_outcome: str, reason: str,
) -> dict[str, Any]:
    """Append a new amendment. NEVER overwrites — original decisions stay in
    runs.outcome (model) and runs.human_outcome (human); amendments layer on
    top in this table.

    If the currently-effective outcome was APPROVE and a PAID settlement
    exists, the amendment flags `payment_reversal_required` and records the
    mock_payment_ref that would need to be reversed operationally. The
    system cannot un-call a payment; the flag is honest about that."""
    from datetime import datetime, timezone
    _ensure_amendments_table()

    # What's the currently-effective outcome? Human override wins over model.
    run = get_run(invoice_number)
    if run is None:
        raise ValueError(f"no run for {invoice_number}")
    prior = run.get("human_outcome") or run["outcome"]

    payment_ref = None
    reversal = 0
    if prior == "APPROVE":
        settlement = get_settlement(invoice_number, run["vendor_name"] or "")
        if settlement and settlement.get("settlement_type") == "PAID":
            payment_ref = settlement.get("mock_payment_ref")
            reversal = 1 if new_outcome != "APPROVE" else 0

    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute("""
            INSERT INTO decision_amendments
              (invoice_number, timestamp, new_outcome, reason,
               prior_outcome, payment_reversal_required, mock_payment_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (invoice_number, now, new_outcome, reason,
              prior, reversal, payment_ref))
        c.commit()

    # If the amendment MAKES an invoice payable that wasn't before (e.g.
    # REJECT → APPROVE, or HOLD → APPROVE, or ESCALATE → APPROVE with no
    # prior PAID row), invoke settle. The settle node's idempotency check
    # protects against firing twice; if a PAID row already exists, it
    # refuses. If the amendment moves AWAY from APPROVE on an already-PAID
    # row, we do NOT attempt to un-settle — the reversal flag above is the
    # honest representation; the payment stands.
    if new_outcome == "APPROVE" and not (prior == "APPROVE" and payment_ref):
        _invoke_settlement(invoice_number, "APPROVE",
                            f"amendment: {reason[:80]}")

    return {
        "invoice_number": invoice_number, "timestamp": now,
        "new_outcome": new_outcome, "reason": reason,
        "prior_outcome": prior,
        "payment_reversal_required": bool(reversal),
        "mock_payment_ref": payment_ref,
    }


def amendments_for(invoice_number: str) -> list[dict[str, Any]]:
    _ensure_amendments_table()
    with _conn() as c:
        rows = c.execute("""
            SELECT id, timestamp, new_outcome, reason, prior_outcome,
                   payment_reversal_required, mock_payment_ref
            FROM decision_amendments
            WHERE invoice_number = ?
            ORDER BY id ASC
        """, (invoice_number,)).fetchall()
    return [dict(r) for r in rows]


def decision_history(invoice_number: str) -> list[dict[str, Any]]:
    """Full decision chain for the detail view, in order:
       model outcome → human decision (if any) → amendments (any number).
    Each entry: {actor, outcome, at, note, extra}."""
    run = get_run(invoice_number)
    if run is None:
        return []
    chain: list[dict[str, Any]] = [{
        "actor": "model", "outcome": run["outcome"],
        "at": run.get("finished_at"),
        "note": run.get("rationale"),
        "extra": None,
    }]
    if run.get("human_outcome"):
        chain.append({
            "actor": "human", "outcome": run["human_outcome"],
            "at": None,
            "note": run.get("human_note"),
            "extra": None,
        })
    for a in amendments_for(invoice_number):
        chain.append({
            "actor": "amendment",
            "outcome": a["new_outcome"],
            "at": a["timestamp"],
            "note": a["reason"],
            "extra": {
                "prior_outcome": a["prior_outcome"],
                "payment_reversal_required": bool(a["payment_reversal_required"]),
                "mock_payment_ref": a["mock_payment_ref"],
            },
        })
    return chain


def effective_outcome(invoice_number: str) -> str | None:
    """The currently-authoritative outcome after model → human → amendments."""
    chain = decision_history(invoice_number)
    return chain[-1]["outcome"] if chain else None


# ---------------------------------------------------------------------------
# Uploads (B8) — pathway for a reviewer to test her own invoice from the UI
# ---------------------------------------------------------------------------

UPLOAD_DIR = Path("data/uploads")


def uploads_dir() -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR


def save_upload(filename: str, contents: bytes) -> Path:
    """Persist an uploaded invoice to data/uploads/ (gitignored). Sanitizes
    the filename to prevent path traversal; unknown suffixes fall back to
    .txt so the router still recognizes them."""
    from pathlib import PurePath
    safe = PurePath(filename).name  # strip any dir components
    if not safe or safe.startswith("."):
        safe = "upload.txt"
    if PurePath(safe).suffix.lower() not in {".txt", ".pdf", ".json", ".csv", ".xml"}:
        safe = safe + ".txt"
    dest = uploads_dir() / safe
    # Uniquify if the same name already exists
    i = 1
    while dest.exists():
        stem = PurePath(safe).stem
        suf = PurePath(safe).suffix
        dest = uploads_dir() / f"{stem}_{i}{suf}"
        i += 1
    dest.write_bytes(contents)
    return dest


def xai_key_configured() -> bool:
    """Whether an XAI_API_KEY is present that could authorize a live call.
    Returns True only for a real key (rejects the replay-placeholder we
    inject in provider.py when no key is set)."""
    import os
    key = os.environ.get("XAI_API_KEY", "")
    if not key or key == "replay-mode-placeholder":
        return False
    # xAI keys start with `xai-`; anything else is not real.
    return key.startswith("xai-")


# ---------------------------------------------------------------------------
# Code legend (B4) — parse the plain-English meanings from taxonomy prose
# ---------------------------------------------------------------------------

def finding_code_legend() -> list[dict[str, Any]]:
    """Build the code legend from docs/exception-taxonomy.md. Reads the
    same table rows the get_policy tool reads (trigger + rationale), but
    doesn't return anything the tool doesn't; this is a READ over the
    taxonomy doc, not a write, so it cannot invalidate cassettes.

    Returns one entry per code with prefix, domain, code, trigger,
    rationale, and a short one-line summary derived from the trigger."""
    from src.tools.policy_tool import _parse_taxonomy
    entries = _parse_taxonomy()
    domains = {
        "EX": "extraction", "AR": "arithmetic", "IN": "inventory",
        "PR": "pricing", "VN": "vendor", "TM": "terms",
        "DP": "duplicates", "PO": "policy", "FR": "fraud signals",
    }
    result = []
    for code in sorted(entries):
        row = entries[code]
        result.append({
            "code": code,
            "prefix": code.split("-")[0],
            "domain": domains.get(code.split("-")[0], ""),
            "trigger": row.get("trigger", ""),
            "rationale": row.get("rationale", ""),
        })
    return result


# One-liners for the finding-chip hover tooltip. Hand-authored so they're
# short enough to fit in a tooltip; kept in-code so a taxonomy edit doesn't
# force a re-run of anything.
FINDING_SUMMARIES: dict[str, str] = {
    "EX-001": "Extractor made silent repairs (OCR / formatting).",
    "AR-001": "A stated line amount ≠ quantity × unit_price.",
    "AR-002": "Stated subtotal ≠ sum of line amounts.",
    "AR-003": "Stated tax ≠ subtotal × stated rate. (Documented; not implemented.)",
    "AR-004": "Stated total ≠ subtotal + tax + additional_charges. Signed delta.",
    "AR-005": "A line has quantity < 0.",
    "AR-006": "Stated total is negative.",
    "IN-001": "Item not in inventory catalog — can't fulfill unknown SKU.",
    "IN-002": "Item present but inactive with zero stock (discontinued).",
    "IN-003": "Aggregated quantity of a canonical item exceeds available stock.",
    "PR-001": "Unit price above reference by more than 5% (overcharge).",
    "PR-002": "Unit price below reference by more than 5% (undercharge / teaser).",
    "PR-003": "Same canonical item at multiple prices within one invoice.",
    "PR-004": "No reference price for this item — comparison unavailable.",
    "VN-001": "Vendor not found in the master.",
    "VN-002": "No exact match but a fuzzy candidate or vendor_claims hit.",
    "VN-003": "Email domain cannot be verified against the master.",
    "VN-004": "Vendor claim references an INACTIVE master vendor (rename ambiguity).",
    "VN-005": "Vendor name is empty.",
    "TM-001": "Due date inconsistent with invoice_date + payment terms.",
    "TM-002": "Stated terms differ from the vendor's contracted terms.",
    "TM-003": "Due date unparseable or on/before invoice date.",
    "DP-001": "Multiple files with matching semantic_hash — dedupe resolved.",
    "DP-002": "Multiple files with same invoice_number, DIFFERENT content — double-pay risk.",
    "DP-003": "A revision marker is present in the duplicate group.",
    "PO-001": "Total exceeds $10,000 approval threshold — manager required.",
    "PO-002": "Total sits within 5% below threshold — structuring signature.",
    "PO-003": "Native currency ≠ USD; FX conversion was applied.",
    "FR-001": "Urgency / pressure language in source text.",
    "FR-002": "Non-standard payment channel requested (wire, gift card, bitcoin).",
    "FR-003": "Suspicious vendor address (matches a high-profile address list).",
}
