"""Policy validator: threshold + near-threshold + FX notification."""
from __future__ import annotations

from decimal import Decimal

from src.config import APPROVAL_THRESHOLD_USD, NEAR_THRESHOLD_BAND
from src.schema import Finding, Invoice, Severity
from src.validators.reference import Reference


_THRESHOLD = Decimal(str(APPROVAL_THRESHOLD_USD))
_BAND = Decimal(str(NEAR_THRESHOLD_BAND))


def check(invoice: Invoice, reference: Reference) -> list[Finding]:  # noqa: ARG001
    if invoice.stated_total is None:
        return []

    findings: list[Finding] = []
    total = invoice.stated_total.amount_usd

    if total > _THRESHOLD:
        findings.append(Finding(
            code="PO-001",
            severity=Severity.HIGH,
            message=(
                f"Total ${total} exceeds the ${_THRESHOLD} auto-approval threshold"
            ),
            evidence=f"total_usd={total}, threshold={_THRESHOLD}",
            field_path="stated_total",
        ))
    elif _THRESHOLD * (Decimal("1") - _BAND) <= total <= _THRESHOLD:
        findings.append(Finding(
            code="PO-002",
            severity=Severity.MEDIUM,
            message=(
                f"Total ${total} sits within {_BAND * 100}% below the "
                f"${_THRESHOLD} threshold — potential structuring signature"
            ),
            evidence=(
                f"total_usd={total}, band=[{_THRESHOLD * (Decimal('1') - _BAND)}, "
                f"{_THRESHOLD}]"
            ),
            field_path="stated_total",
        ))

    if invoice.stated_total.currency != "USD":
        findings.append(Finding(
            code="PO-003",
            severity=Severity.INFO,
            message=(
                f"FX conversion applied: {invoice.stated_total.amount_native} "
                f"{invoice.stated_total.currency} -> ${invoice.stated_total.amount_usd}"
            ),
            evidence=(
                f"native={invoice.stated_total.amount_native} "
                f"{invoice.stated_total.currency}, usd={invoice.stated_total.amount_usd}"
            ),
            field_path="stated_total",
        ))

    return findings
