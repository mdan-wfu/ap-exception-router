"""Prior-invoice lookup from the audit store.

Returns three distinguishable states:
  1. found=True                       — a prior settled run of this number exists
  2. found=False, store_populated=True — no prior of THIS number, but the store
                                          has other records (this is evidence:
                                          the system has run, just not for this)
  3. found=False, store_populated=False — the audit store has no records at all
                                          (INFRASTRUCTURE FACT, not evidence:
                                          the pipeline has never persisted a run
                                          yet). Do not use this to conclude
                                          anything about the invoice.

The third state is what fixed the INV-1004 bug documented in DECISIONS.md,
where the Adjudicator read an empty store as "no prior submission genuinely
exists" and rejected on false certainty.
"""
from __future__ import annotations

from src.store.canonical import normalize_invoice_number
from src.tools.audit_store import AuditStore
from src.tools.models import PriorInvoiceQuery, PriorInvoiceResult


def get_prior_invoice(
    query: PriorInvoiceQuery,
    audit_store: AuditStore | None = None,
) -> PriorInvoiceResult:
    store = audit_store if audit_store is not None else AuditStore()
    store_populated = store.has_any_runs()

    try:
        normalized = normalize_invoice_number(query.invoice_number)
    except ValueError:
        return PriorInvoiceResult(
            invoice_number=query.invoice_number,
            found=False,
            store_populated=store_populated,
            semantic_hash=None,
            stated_total_usd=None,
            source_file=None,
            prior_outcome=None,
        )

    row = store.prior_invoice_row(normalized)
    if row is None:
        return PriorInvoiceResult(
            invoice_number=normalized,
            found=False,
            store_populated=store_populated,
            semantic_hash=None,
            stated_total_usd=None,
            source_file=None,
            prior_outcome=None,
        )

    return PriorInvoiceResult(
        invoice_number=normalized,
        found=True,
        store_populated=store_populated,
        semantic_hash=row.semantic_hash,
        stated_total_usd=row.stated_total_usd,
        source_file=row.source_file,
        prior_outcome=row.outcome,
    )
