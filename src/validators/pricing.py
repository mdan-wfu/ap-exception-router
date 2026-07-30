"""Pricing validator.

Comparison in USD after FX conversion — never natively. INV-1014's WidgetB
at €475 is $541.50 in USD, 8.3% over the $500 contract. Compared natively
it looks like a 5% discount. Getting the currency wrong flips the sign of
the finding.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from src.config import PRICE_TOLERANCE
from src.schema import Finding, Invoice, Severity
from src.validators.reference import Reference


_TOLERANCE = Decimal(str(PRICE_TOLERANCE))


def check(invoice: Invoice, reference: Reference) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(_vs_reference(invoice, reference))
    findings.extend(_intra_invoice(invoice))
    return findings


def _vs_reference(invoice: Invoice, reference: Reference) -> list[Finding]:
    out: list[Finding] = []
    # Report once per canonical item, not once per line.
    reported_over: set[str] = set()
    reported_under: set[str] = set()
    reported_missing: set[str] = set()

    for idx, li in enumerate(invoice.line_items):
        if li.canonical_item is None:
            key = li.raw_item_name or f"<line {idx}>"
            if key in reported_missing:
                continue
            reported_missing.add(key)
            out.append(Finding(
                code="PR-004",
                severity=Severity.LOW,
                message=f"No reference price available for unknown item {li.raw_item_name!r}",
                evidence="canonical_item is None",
                field_path=f"line_items[{idx}].unit_price",
            ))
            continue

        record = reference.find_inventory(li.canonical_item)
        if record is None or record.reference_unit_price is None:
            if li.canonical_item in reported_missing:
                continue
            reported_missing.add(li.canonical_item)
            out.append(Finding(
                code="PR-004",
                severity=Severity.LOW,
                message=f"No reference unit price for {li.canonical_item}",
                evidence="inventory row has reference_unit_price = NULL",
                field_path=f"line_items[{idx}].unit_price",
            ))
            continue

        ref_price = record.reference_unit_price
        actual = li.unit_price.amount_usd
        upper = ref_price * (Decimal("1") + _TOLERANCE)
        lower = ref_price * (Decimal("1") - _TOLERANCE)

        if actual > upper and li.canonical_item not in reported_over:
            reported_over.add(li.canonical_item)
            pct = ((actual - ref_price) / ref_price * Decimal("100")).quantize(Decimal("0.1"))
            out.append(Finding(
                code="PR-001",
                severity=Severity.HIGH,
                message=(
                    f"{li.canonical_item}: unit price ${actual} exceeds "
                    f"reference ${ref_price} by {pct}% (tolerance {PRICE_TOLERANCE * 100}%)"
                ),
                evidence=(
                    f"native={li.unit_price.amount_native} {li.unit_price.currency}, "
                    f"usd={actual}, ref={ref_price}"
                ),
                field_path=f"line_items[{idx}].unit_price",
            ))
        elif actual < lower and li.canonical_item not in reported_under:
            reported_under.add(li.canonical_item)
            pct = ((ref_price - actual) / ref_price * Decimal("100")).quantize(Decimal("0.1"))
            out.append(Finding(
                code="PR-002",
                severity=Severity.LOW,
                message=(
                    f"{li.canonical_item}: unit price ${actual} is {pct}% below "
                    f"reference ${ref_price} (tolerance {PRICE_TOLERANCE * 100}%)"
                ),
                evidence=f"usd={actual}, ref={ref_price}",
                field_path=f"line_items[{idx}].unit_price",
            ))
    return out


def _intra_invoice(invoice: Invoice) -> list[Finding]:
    """Same canonical item at multiple different unit prices on one invoice."""
    prices: dict[str, set[Decimal]] = defaultdict(set)
    for li in invoice.line_items:
        if li.canonical_item is None:
            continue
        prices[li.canonical_item].add(li.unit_price.amount_usd)

    out: list[Finding] = []
    for canonical, price_set in prices.items():
        if len(price_set) > 1:
            sorted_prices = sorted(price_set)
            out.append(Finding(
                code="PR-003",
                severity=Severity.MEDIUM,
                message=(
                    f"{canonical} appears at multiple prices within the invoice: "
                    f"{', '.join(f'${p}' for p in sorted_prices)}"
                ),
                evidence=f"prices={sorted_prices}",
                field_path="line_items",
            ))
    return out
