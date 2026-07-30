"""Terms validator: due date vs invoice_date + payment terms."""
from __future__ import annotations

import re
from datetime import date, timedelta

from src.config import TERMS_TOLERANCE_DAYS
from src.schema import Finding, Invoice, Severity
from src.validators.reference import Reference


_NET_TERMS = re.compile(r"net\s+(\d+)", re.IGNORECASE)


def _parse_terms_days(terms: str | None) -> int | None:
    if not terms:
        return None
    m = _NET_TERMS.search(terms)
    return int(m.group(1)) if m else None


def _parse_iso(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return date.fromisoformat(d)
    except ValueError:
        return None


def check(invoice: Invoice, reference: Reference) -> list[Finding]:
    findings: list[Finding] = []

    invoice_date = _parse_iso(invoice.invoice_date)
    due_date = _parse_iso(invoice.due_date)
    terms_days = _parse_terms_days(invoice.payment_terms)

    # TM-003: unparseable due date, or due date on/before invoice date
    if invoice.due_date_raw and due_date is None:
        findings.append(Finding(
            code="TM-003",
            severity=Severity.HIGH,
            message=f"Due date is unparseable: {invoice.due_date_raw!r}",
            evidence=f"due_date_raw={invoice.due_date_raw!r}, due_date=None",
            field_path="due_date_raw",
        ))
    elif invoice_date and due_date and due_date <= invoice_date:
        findings.append(Finding(
            code="TM-003",
            severity=Severity.HIGH,
            message=(
                f"Due date {due_date.isoformat()} is on or before invoice date "
                f"{invoice_date.isoformat()}"
            ),
            evidence=f"invoice_date={invoice_date}, due_date={due_date}",
            field_path="due_date",
        ))

    # TM-001: due date inconsistent with terms (only when everything parses)
    if invoice_date and due_date and terms_days is not None:
        expected = invoice_date + timedelta(days=terms_days)
        diff_days = abs((due_date - expected).days)
        if diff_days > TERMS_TOLERANCE_DAYS:
            findings.append(Finding(
                code="TM-001",
                severity=Severity.MEDIUM,
                message=(
                    f"Due date {due_date.isoformat()} is {diff_days} days off from "
                    f"invoice_date + terms ({expected.isoformat()}), tolerance "
                    f"{TERMS_TOLERANCE_DAYS}"
                ),
                evidence=(
                    f"invoice_date={invoice_date}, terms={terms_days}, "
                    f"expected={expected}, due={due_date}, diff_days={diff_days}"
                ),
                field_path="due_date",
            ))

    # TM-002: stated terms differ from contract
    master = reference.find_vendor(invoice.vendor_name) if invoice.vendor_name else None
    if master and invoice.payment_terms and master.contracted_terms:
        if invoice.payment_terms.strip().lower() != master.contracted_terms.strip().lower():
            findings.append(Finding(
                code="TM-002",
                severity=Severity.LOW,
                message=(
                    f"Stated terms {invoice.payment_terms!r} differ from "
                    f"contracted terms {master.contracted_terms!r} for "
                    f"{master.name!r}"
                ),
                evidence=(
                    f"stated={invoice.payment_terms!r}, "
                    f"contract={master.contracted_terms!r}"
                ),
                field_path="payment_terms",
            ))

    return findings
