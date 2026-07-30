"""Vendor lookup tools. Return facts. Never verdicts.

Reuses the fuzzy matcher and threshold from `src/validators/vendor.py`
so there is one implementation of "which vendors look like this one" —
both the deterministic validator and the Adjudicator's investigation tool
use the same score.
"""
from __future__ import annotations

from decimal import Decimal

from src.tools.audit_store import AuditStore
from src.tools.models import (
    VendorFuzzyCandidate,
    VendorHistoryQuery,
    VendorHistoryResult,
    VendorMasterRow,
    VendorRecordQuery,
    VendorRecordResult,
)
from src.validators.reference import Reference
from src.validators.vendor import FUZZY_THRESHOLD, score_candidates

# The tool uses a lower investigative threshold than the validator so the
# Adjudicator sees neighborhood context (e.g. FastShip Ltd. as a candidate
# for `QuickShip Distributers` at ratio ~0.34). The validator stays strict
# at FUZZY_THRESHOLD to keep VN-002 free of false positives.
TOOL_FUZZY_THRESHOLD = 0.30
TOOL_TOP_N = 5


_reference: Reference | None = None


def _ref() -> Reference:
    global _reference
    if _reference is None:
        _reference = Reference()
    return _reference


def _to_master_row(v) -> VendorMasterRow:
    return VendorMasterRow(
        name=v.name,
        domain=v.domain,
        status=v.status,
        contracted_terms=v.contracted_terms,
        relationship_since=v.relationship_since,
    )


def get_vendor_record(query: VendorRecordQuery) -> VendorRecordResult:
    """Look up a vendor by name.

    Returns the master row on exact (case-insensitive) match, plus every
    fuzzy candidate above `FUZZY_THRESHOLD` with its raw score. Candidates
    are returned even when there is an exact match — the presence of a
    near-neighbor is factual context the Adjudicator may use.
    """
    reference = _ref()
    exact = reference.find_vendor(query.name)

    scored = score_candidates(
        query.name, reference,
        min_score=TOOL_FUZZY_THRESHOLD, top_n=TOOL_TOP_N,
    )
    candidates: list[VendorFuzzyCandidate] = [
        VendorFuzzyCandidate(
            name=record.name,
            score=round(score, 4),
            below_threshold=score < FUZZY_THRESHOLD,
            status=record.status,
            relationship_since=record.relationship_since,
        )
        for score, record in scored
        # Skip the exact match — it is returned separately
        if exact is None or record.name != exact.name
    ]

    # Also surface any master vendor whose name appears as a substring of the
    # query (the `(formerly FastShip Ltd.)` shape when a caller passes the full
    # raw vendor string). Substring hits get score 1.0 so they sort to the top.
    target = query.name.strip().lower() if query.name else ""
    if target:
        for record in reference.vendors.values():
            if record.name.lower() in target and all(c.name != record.name for c in candidates):
                candidates.append(VendorFuzzyCandidate(
                    name=record.name,
                    score=1.0,
                    below_threshold=False,
                    status=record.status,
                    relationship_since=record.relationship_since,
                ))
        candidates.sort(key=lambda c: c.score, reverse=True)

    return VendorRecordResult(
        query=query.name,
        exact_match=_to_master_row(exact) if exact is not None else None,
        fuzzy_candidates=candidates,
        match_threshold=FUZZY_THRESHOLD,
    )


def get_vendor_invoice_history(
    query: VendorHistoryQuery,
    audit_store: AuditStore | None = None,
) -> VendorHistoryResult:
    """Prior invoices for this vendor from the audit store.

    An empty store returns zeros — this is a first-time payee, not an error.
    """
    store = audit_store if audit_store is not None else AuditStore()
    rows = store.vendor_history_rows(query.name)

    if not rows:
        return VendorHistoryResult(
            vendor_name=query.name,
            invoice_count=0,
            total_value_usd=Decimal("0"),
            first_seen=None,
            last_seen=None,
            prior_outcomes={},
        )

    outcomes: dict[str, int] = {}
    for r in rows:
        outcomes[r.outcome] = outcomes.get(r.outcome, 0) + 1

    return VendorHistoryResult(
        vendor_name=query.name,
        invoice_count=len(rows),
        total_value_usd=sum((r.stated_total_usd for r in rows), Decimal("0")),
        first_seen=rows[0].finished_at,
        last_seen=rows[-1].finished_at,
        prior_outcomes=outcomes,
    )
