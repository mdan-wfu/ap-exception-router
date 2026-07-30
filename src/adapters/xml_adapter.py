"""XML adapter.

INV-1014 is the only XML file in the corpus. It carries `<currency>EUR</currency>`
in the header; the currency is passed through to `Money`, which handles FX
conversion at construction. The adapter does NOT convert — that would hide
the native value that must survive as evidence.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from src.adapters._common import money, parse_date
from src.schema import Invoice, LineItem, Money
from src.store.canonical import (
    canonicalize_item,
    normalize_invoice_number,
    parse_vendor,
)


def extract(path: Path) -> Invoice:
    raw_bytes = path.read_bytes()
    tree = ET.parse(path)
    root = tree.getroot()

    header = root.find("header")
    totals = root.find("totals")

    invoice_number_raw = _text(header, "invoice_number", "")
    invoice_number = (
        normalize_invoice_number(invoice_number_raw) if invoice_number_raw else ""
    )
    vendor_raw = _text(header, "vendor", "")
    vendor_name, vendor_claims = parse_vendor(vendor_raw)
    currency = _text(header, "currency", "USD")

    date_raw = _text(header, "date") or None
    due_date_raw = _text(header, "due_date") or None

    line_items = []
    items_el = root.find("line_items")
    if items_el is not None:
        for item in items_el.findall("item"):
            raw_item = _text(item, "name", "")
            canonical, _ = canonicalize_item(raw_item)
            qty = int(_text(item, "quantity", "0"))
            unit_price = Money(
                amount_native=Decimal(_text(item, "unit_price", "0") or "0"),
                currency=currency,
            )
            amount_txt = _text(item, "amount")
            line_amount = (
                Money(amount_native=Decimal(amount_txt), currency=currency)
                if amount_txt else None
            )
            line_items.append(LineItem(
                raw_item_name=raw_item,
                canonical_item=canonical,
                quantity=qty,
                unit_price=unit_price,
                line_amount=line_amount,
            ))

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
        stated_subtotal=money(_text(totals, "subtotal"), currency),
        stated_tax=money(_text(totals, "tax_amount"), currency),
        stated_total=money(_text(totals, "total"), currency),
        payment_terms=_text(root, "payment_terms") or None,
        source_file=str(path),
        source_format="xml",
        extraction_confidence=1.0,
        file_hash=Invoice.compute_file_hash(raw_bytes),
    )


def _text(el, tag: str, default: str | None = None) -> str | None:
    if el is None:
        return default
    child = el.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()
