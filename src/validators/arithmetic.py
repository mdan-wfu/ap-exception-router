"""Arithmetic validator.

Cent-exact comparison in USD, with a 1-cent rounding allowance and NO
percentage tolerance. Percentage tolerance on arithmetic is how $50
grand-total errors hide inside "close enough".

Rules that require special care:
  - Tax: only checked when a tax_rate is stated. INV-1010 states a tax
    amount with no rate — do NOT infer a rate and then flag the result.
    We do not have tax_rate on the schema; we approximate by checking
    tax as a component of the grand-total sum, not as an independent check.
  - Grand total: sum MUST include `additional_charges` or INV-1010's
    $150 shipping produces a false finding on a clean invoice.
"""
from __future__ import annotations

from decimal import Decimal

from src.schema import Finding, Invoice, Severity
from src.validators.reference import Reference


_CENT = Decimal("0.01")


def check(invoice: Invoice, reference: Reference) -> list[Finding]:  # noqa: ARG001
    findings: list[Finding] = []

    findings.extend(_line_totals(invoice))
    findings.extend(_subtotal(invoice))
    findings.extend(_grand_total(invoice))
    findings.extend(_negatives(invoice))

    return findings


def _line_totals(invoice: Invoice) -> list[Finding]:
    out: list[Finding] = []
    for idx, li in enumerate(invoice.line_items):
        if li.line_amount is None:
            continue
        expected = (Decimal(li.quantity) * li.unit_price.amount_usd)
        diff = abs(li.line_amount.amount_usd - expected)
        if diff > _CENT:
            out.append(Finding(
                code="AR-001",
                severity=Severity.MEDIUM,
                message=(
                    f"Line {idx} ({li.raw_item_name}): stated amount "
                    f"${li.line_amount.amount_usd} != {li.quantity} × "
                    f"${li.unit_price.amount_usd} = ${expected}"
                ),
                evidence=f"diff=${diff}",
                field_path=f"line_items[{idx}].line_amount",
            ))
    return out


def _subtotal(invoice: Invoice) -> list[Finding]:
    if invoice.stated_subtotal is None:
        return []
    # Sum stated line_amounts where present; otherwise fall back to price × qty.
    total = Decimal("0")
    for li in invoice.line_items:
        if li.line_amount is not None:
            total += li.line_amount.amount_usd
        else:
            total += Decimal(li.quantity) * li.unit_price.amount_usd
    diff = abs(invoice.stated_subtotal.amount_usd - total)
    if diff > _CENT:
        return [Finding(
            code="AR-002",
            severity=Severity.HIGH,
            message=(
                f"Stated subtotal ${invoice.stated_subtotal.amount_usd} does not "
                f"equal the sum of line amounts (${total})"
            ),
            evidence=f"diff=${diff}",
            field_path="stated_subtotal",
        )]
    return []


def _grand_total(invoice: Invoice) -> list[Finding]:
    if invoice.stated_total is None:
        return []

    # Prefer the stated subtotal if given; otherwise use the sum of lines.
    if invoice.stated_subtotal is not None:
        subtotal = invoice.stated_subtotal.amount_usd
    else:
        subtotal = sum(
            (li.line_amount.amount_usd if li.line_amount is not None
             else Decimal(li.quantity) * li.unit_price.amount_usd)
            for li in invoice.line_items
        ) or Decimal("0")

    tax = invoice.stated_tax.amount_usd if invoice.stated_tax is not None else Decimal("0")
    extras = sum(
        (ac.amount.amount_usd for ac in invoice.additional_charges),
        start=Decimal("0"),
    )
    expected = subtotal + tax + extras
    diff = abs(invoice.stated_total.amount_usd - expected)
    if diff > _CENT:
        return [Finding(
            code="AR-004",
            severity=Severity.HIGH,
            message=(
                f"Stated total ${invoice.stated_total.amount_usd} does not equal "
                f"subtotal (${subtotal}) + tax (${tax}) + extras (${extras}) "
                f"= ${expected}"
            ),
            evidence=f"diff=${diff}",
            field_path="stated_total",
        )]
    return []


def _negatives(invoice: Invoice) -> list[Finding]:
    out: list[Finding] = []
    for idx, li in enumerate(invoice.line_items):
        if li.quantity < 0:
            out.append(Finding(
                code="AR-005",
                severity=Severity.CRITICAL,
                message=f"Line {idx} ({li.raw_item_name}): negative quantity {li.quantity}",
                evidence=f"quantity={li.quantity}",
                field_path=f"line_items[{idx}].quantity",
            ))
    if invoice.stated_total is not None and invoice.stated_total.amount_usd < 0:
        out.append(Finding(
            code="AR-006",
            severity=Severity.CRITICAL,
            message=f"Stated total is negative: ${invoice.stated_total.amount_usd}",
            evidence=f"stated_total.amount_usd={invoice.stated_total.amount_usd}",
            field_path="stated_total",
        ))
    return out
