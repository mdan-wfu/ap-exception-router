"""Validator registry, per-invoice composition, batch duplicate check,
and the CLAUDE.md §2.2 hard-guardrail predicate.
"""
from __future__ import annotations

from pathlib import Path

from src.schema import Finding, Invoice, Severity
from src.validators.reference import Reference
from src.validators import (
    arithmetic,
    duplicates,
    extraction,
    inventory,
    policy,
    pricing,
    signals,
    terms,
    vendor,
)


# Order is stable for reproducible run traces. Findings from later validators
# do not depend on findings from earlier ones — every validator is independent.
_PER_INVOICE = (
    extraction.check,
    arithmetic.check,
    inventory.check,
    pricing.check,
    vendor.check,
    terms.check,
    policy.check,
    signals.check,
)


def run_validators(invoice: Invoice, reference: Reference) -> list[Finding]:
    """Compose every per-invoice validator. Order preserved for auditability."""
    findings: list[Finding] = []
    for check in _PER_INVOICE:
        findings.extend(check(invoice, reference))
    return findings


def find_duplicates(invoices: list[Invoice]) -> list[tuple[Invoice, Finding]]:
    """Batch-scoped duplicate detection. Distinct signature because it needs
    the full set of invoices to group by normalized invoice_number."""
    return duplicates.find(invoices)


def has_critical(findings: list[Finding]) -> bool:
    """CLAUDE.md §2.2 hard guardrail predicate.

    The Adjudicator may downgrade or escalate, but never auto-approve past
    a CRITICAL finding. Implemented as a pure predicate so Phase 5 wires to
    it rather than reimplementing the check.
    """
    return any(f.severity == Severity.CRITICAL for f in findings)
