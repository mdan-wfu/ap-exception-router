"""Inventory validator.

The single most important check: aggregate by canonical_item BEFORE
comparing to stock. INV-1013's per-line quantities all pass; the sum
does not (WidgetA 22/15, WidgetB 18/10, GadgetX 9/5).

Three distinct findings — do not collapse them, the Adjudicator needs
the distinction:
  IN-001  unknown item (canonical_item is None)
  IN-002  known but inactive with zero stock (FakeItem)
  IN-003  aggregate quantity exceeds standing stock
"""
from __future__ import annotations

from collections import defaultdict

from src.schema import Finding, Invoice, Severity
from src.validators.reference import Reference


def check(invoice: Invoice, reference: Reference) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(_unknown_and_zero_stock(invoice, reference))
    findings.extend(_aggregate_exceeds_stock(invoice, reference))
    return findings


def _unknown_and_zero_stock(invoice: Invoice, reference: Reference) -> list[Finding]:
    out: list[Finding] = []
    # De-duplicate per raw item so one finding per distinct item, not per line.
    seen_unknown: set[str] = set()
    seen_zero: set[str] = set()

    for idx, li in enumerate(invoice.line_items):
        if li.canonical_item is None:
            key = li.raw_item_name or f"<line {idx}>"
            if key in seen_unknown:
                continue
            seen_unknown.add(key)
            out.append(Finding(
                code="IN-001",
                severity=Severity.HIGH,
                message=f"Unknown item: {li.raw_item_name!r} is not in the inventory catalog",
                evidence=f"raw_item_name={li.raw_item_name!r}",
                field_path=f"line_items[{idx}].raw_item_name",
            ))
            continue

        record = reference.find_inventory(li.canonical_item)
        if record is None:
            continue  # canonicalize_item returned a name, but it's not in DB
        if not record.active and record.stock == 0:
            if li.canonical_item in seen_zero:
                continue
            seen_zero.add(li.canonical_item)
            out.append(Finding(
                code="IN-002",
                severity=Severity.HIGH,
                message=f"Item {li.canonical_item} is inactive with zero stock",
                evidence=f"stock={record.stock}, active={record.active}",
                field_path=f"line_items[{idx}].canonical_item",
            ))
    return out


def _aggregate_exceeds_stock(invoice: Invoice, reference: Reference) -> list[Finding]:
    """Sum quantities per canonical item, then compare to standing stock.
    Standing stock (not depleting) per the assumption in CLAUDE.md §6."""
    totals: dict[str, int] = defaultdict(int)
    for li in invoice.line_items:
        if li.canonical_item is None:
            continue
        totals[li.canonical_item] += li.quantity

    out: list[Finding] = []
    for canonical, aggregate in totals.items():
        record = reference.find_inventory(canonical)
        if record is None or not record.active:
            continue
        if aggregate > record.stock:
            out.append(Finding(
                code="IN-003",
                severity=Severity.HIGH,
                message=(
                    f"Aggregate demand for {canonical} is {aggregate} units, "
                    f"exceeding standing stock of {record.stock}"
                ),
                evidence=f"aggregate={aggregate}, stock={record.stock}",
                field_path="line_items",
            ))
    return out
