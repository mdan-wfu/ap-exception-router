"""CSV adapter — two incompatible shapes, detected on header width.

Shape A (vertical key–value, INV-1006):
    field,value
    invoice_number,INV-1006
    item,WidgetA
    quantity,5
    unit_price,250.00
    item,WidgetB       ← csv.DictReader into a dict silently drops this
    ...

Shape B (row-per-line-item with trailing summary, INV-1007 / INV-1015):
    Invoice Number,Vendor,Date,Due Date,Item,Qty,Unit Price,Line Total
    INV-1007,MegaWidgets Corp,01/28/2026,02/28/2026,WidgetA,20,250.00,5000.00
    ...
    ,,,,,,Subtotal:,14750.00                ← summary rows: blank leading cols

INV-1007 uses US MM/DD/YYYY dates; INV-1015 uses ISO. `parse_date` handles both.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from src.adapters._common import money, parse_date
from src.schema import Invoice, LineItem, Money
from src.store.canonical import (
    canonicalize_item,
    normalize_invoice_number,
    parse_vendor,
)


def extract(path: Path) -> Invoice:
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError(f"{path}: empty CSV")

    header = [c.strip() for c in rows[0]]

    if len(header) <= 2 and header[0].lower() == "field":
        return _parse_vertical(path, raw_bytes, rows)
    return _parse_row_per_item(path, raw_bytes, rows)


# ---------------------------------------------------------------------------
# Vertical (INV-1006)
# ---------------------------------------------------------------------------

def _parse_vertical(path: Path, raw_bytes: bytes, rows: list[list[str]]) -> Invoice:
    """Parse positionally — `csv.DictReader` would drop repeated `item` keys."""
    scalars: dict[str, str] = {}
    line_items_raw: list[tuple[str, int, float]] = []
    current: dict[str, Any] = {}

    def _flush() -> None:
        if current:
            line_items_raw.append((
                current.get("item", ""),
                int(current.get("quantity", 0) or 0),
                float(current.get("unit_price", 0) or 0),
            ))

    for row in rows[1:]:
        if len(row) < 2:
            continue
        key = row[0].strip().lower()
        val = row[1].strip()
        if key == "item":
            _flush()
            current = {"item": val}
        elif key in ("quantity", "unit_price"):
            current[key] = val
        else:
            scalars[key] = val
    _flush()

    invoice_number_raw = scalars.get("invoice_number", "")
    invoice_number = (
        normalize_invoice_number(invoice_number_raw) if invoice_number_raw else ""
    )
    vendor_raw = scalars.get("vendor", "")
    vendor_name, vendor_claims = parse_vendor(vendor_raw)
    currency = scalars.get("currency", "USD")

    line_items = [
        _build_line_item(raw, qty, price, currency)
        for (raw, qty, price) in line_items_raw
    ]

    date_raw = scalars.get("date") or None
    due_date_raw = scalars.get("due_date") or None

    return Invoice(
        invoice_number_raw=invoice_number_raw,
        invoice_number=invoice_number,
        vendor_raw=vendor_raw,
        vendor_name=vendor_name,
        vendor_claims=vendor_claims,
        date_raw=date_raw,
        invoice_date=parse_date(date_raw),
        due_date_raw=due_date_raw,
        due_date=parse_date(due_date_raw),
        line_items=line_items,
        stated_subtotal=money(scalars.get("subtotal"), currency),
        stated_tax=money(scalars.get("tax") or scalars.get("tax_amount"), currency),
        stated_total=money(scalars.get("total"), currency),
        payment_terms=scalars.get("payment_terms") or None,
        source_file=str(path),
        source_format="csv",
        extraction_confidence=1.0,
        file_hash=Invoice.compute_file_hash(raw_bytes),
    )


# ---------------------------------------------------------------------------
# Row-per-item (INV-1007 / INV-1015)
# ---------------------------------------------------------------------------

def _parse_row_per_item(path: Path, raw_bytes: bytes, rows: list[list[str]]) -> Invoice:
    header = [c.strip() for c in rows[0]]

    line_items_raw: list[dict[str, str]] = []
    summary: dict[str, str] = {}
    invoice_number_raw = ""
    vendor_raw = ""
    date_raw = ""
    due_date_raw = ""

    for row in rows[1:]:
        # Summary rows: blank leading columns
        if not row or not row[0].strip():
            non_empty = [c.strip() for c in row if c.strip()]
            if len(non_empty) >= 2:
                label = non_empty[-2].rstrip(":")
                value = non_empty[-1]
                # Bucket: `Subtotal` -> subtotal, `Tax (6%)` -> tax, `Total` -> total
                bucket = label.split()[0].lower()
                summary[bucket] = value
            continue

        row_data = dict(zip(header, [c.strip() for c in row]))
        if not invoice_number_raw:
            invoice_number_raw = row_data.get("Invoice Number", "")
            vendor_raw = row_data.get("Vendor", "")
            date_raw = row_data.get("Date", "")
            due_date_raw = row_data.get("Due Date", "")
        line_items_raw.append(row_data)

    invoice_number = (
        normalize_invoice_number(invoice_number_raw) if invoice_number_raw else ""
    )
    vendor_name, vendor_claims = parse_vendor(vendor_raw)
    currency = "USD"

    line_items = []
    for row in line_items_raw:
        raw_item = row.get("Item", "")
        canonical, _ = canonicalize_item(raw_item)
        try:
            qty = int(row.get("Qty", "0") or 0)
        except ValueError:
            qty = 0
        line_items.append(LineItem(
            raw_item_name=raw_item,
            canonical_item=canonical,
            quantity=qty,
            unit_price=Money(
                amount_native=_decimal(row.get("Unit Price", "0")),
                currency=currency,
            ),
            line_amount=money(row.get("Line Total"), currency),
        ))

    return Invoice(
        invoice_number_raw=invoice_number_raw,
        invoice_number=invoice_number,
        vendor_raw=vendor_raw,
        vendor_name=vendor_name,
        vendor_claims=vendor_claims,
        date_raw=date_raw or None,
        invoice_date=parse_date(date_raw),
        due_date_raw=due_date_raw or None,
        due_date=parse_date(due_date_raw),
        line_items=line_items,
        stated_subtotal=money(summary.get("subtotal"), currency),
        stated_tax=money(summary.get("tax"), currency),
        stated_total=money(summary.get("total"), currency),
        source_file=str(path),
        source_format="csv",
        extraction_confidence=1.0,
        file_hash=Invoice.compute_file_hash(raw_bytes),
    )


def _build_line_item(raw_item: str, qty: int, price: float, currency: str) -> LineItem:
    canonical, _ = canonicalize_item(raw_item)
    return LineItem(
        raw_item_name=raw_item,
        canonical_item=canonical,
        quantity=qty,
        unit_price=Money(amount_native=_decimal(price), currency=currency),
        line_amount=None,
    )


def _decimal(v: Any):
    from decimal import Decimal

    if v is None or v == "":
        return Decimal("0")
    return Decimal(str(v))
