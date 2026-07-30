"""Prior-invoice lookup from the audit store.

Empty store returns `found=False` for every query — that is the correct
"first submission under this number" answer, not an error. This is the
tool that makes INV-1004 vs INV-1004_revised resolvable: the Adjudicator
uses it to check whether the original was already settled.
"""
from __future__ import annotations

from src.store.canonical import normalize_invoice_number
from src.tools.audit_store import AuditStore
from src.tools.models import PriorInvoiceQuery, PriorInvoiceResult


def get_prior_invoice(
    query: PriorInvoiceQuery,
    audit_store: AuditStore | None = None,
) -> PriorInvoiceResult:
    try:
        normalized = normalize_invoice_number(query.invoice_number)
    except ValueError:
        # Query with no extractable digit run → unmatchable, not an error
        return PriorInvoiceResult(
            invoice_number=query.invoice_number,
            found=False,
            semantic_hash=None,
            stated_total_usd=None,
            source_file=None,
            prior_outcome=None,
        )

    store = audit_store if audit_store is not None else AuditStore()
    row = store.prior_invoice_row(normalized)
    if row is None:
        return PriorInvoiceResult(
            invoice_number=normalized,
            found=False,
            semantic_hash=None,
            stated_total_usd=None,
            source_file=None,
            prior_outcome=None,
        )

    return PriorInvoiceResult(
        invoice_number=normalized,
        found=True,
        semantic_hash=row.semantic_hash,
        stated_total_usd=row.stated_total_usd,
        source_file=row.source_file,
        prior_outcome=row.outcome,
    )
