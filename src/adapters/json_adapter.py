"""JSON adapter.

Records what the document claims. Never computes. Never rejects. Corpus
traps handled explicitly:
  - INV-1005: line items with no `amount` field → `line_amount=None`,
    the arithmetic validator computes and compares in Phase 4.
  - INV-1013: 8 line items, 3 repeating `WidgetA` → all preserved distinctly.
  - INV-1009: nulls, empty strings, negative quantity → all survive.
  - INV-1004_revised: `revision` field and `notes` string → notes goes to
    `notes`; `revision` lands in `references` (Phase 4 uses it for DP-003).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.adapters._common import money, parse_date
from src.schema import Invoice, LineItem
from src.store.canonical import (
    canonicalize_item,
    normalize_invoice_number,
    parse_vendor,
)


def extract(path: Path) -> Invoice:
    raw_bytes = path.read_bytes()
    data = json.loads(raw_bytes)

    invoice_number_raw = str(data.get("invoice_number", "") or "")
    invoice_number = (
        normalize_invoice_number(invoice_number_raw)
        if invoice_number_raw
        else ""
    )

    vendor_raw, vendor_addr, vendor_email = _parse_vendor_block(data.get("vendor"))
    vendor_name, vendor_claims = parse_vendor(vendor_raw)

    currency = str(data.get("currency", "USD"))

    date_raw = _to_str_or_none(data.get("date"))
    invoice_date = parse_date(date_raw)
    due_date_raw = _to_str_or_none(data.get("due_date"))
    due_date = parse_date(due_date_raw)

    line_items = [
        _build_line_item(row, currency)
        for row in (data.get("line_items") or [])
    ]

    references: list[str] = []
    revision = data.get("revision")
    if revision:
        references.append(f"revision:{revision}")

    return Invoice(
        invoice_number_raw=invoice_number_raw,
        invoice_number=invoice_number,
        vendor_raw=vendor_raw,
        vendor_name=vendor_name,
        vendor_claims=vendor_claims,
        vendor_address=vendor_addr,
        vendor_email=vendor_email,
        date_raw=date_raw,
        invoice_date=invoice_date,
        due_date_raw=due_date_raw,
        due_date=due_date,
        line_items=line_items,
        additional_charges=[],
        stated_subtotal=money(data.get("subtotal"), currency),
        stated_tax=money(data.get("tax_amount"), currency),
        stated_total=money(data.get("total"), currency),
        payment_terms=_to_str_or_none(data.get("payment_terms")),
        references=references,
        notes=_to_str_or_none(data.get("notes")),
        source_file=str(path),
        source_format="json",
        corrections=[],
        extraction_confidence=1.0,
        file_hash=Invoice.compute_file_hash(raw_bytes),
    )


def _parse_vendor_block(vendor: Any) -> tuple[str, str | None, str | None]:
    """Vendor may be a dict {name, address, email?} or a bare string."""
    if isinstance(vendor, dict):
        name = str(vendor.get("name", "") or "")
        addr = _to_str_or_none(vendor.get("address"))
        email = _to_str_or_none(vendor.get("email"))
        return name, addr, email
    if isinstance(vendor, str):
        return vendor, None, None
    return "", None, None


def _to_str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v if v != "" else ""
    return str(v)


def _build_line_item(row: dict[str, Any], currency: str) -> LineItem:
    raw_item = str(row.get("item", "") or "")
    canonical, _ops = canonicalize_item(raw_item)
    unit_price = money(row.get("unit_price"), currency)
    # unit_price must be Money — coerce zero-price case if the source omitted it
    if unit_price is None:
        from decimal import Decimal
        from src.schema import Money

        unit_price = Money(amount_native=Decimal("0"), currency=currency)
    return LineItem(
        raw_item_name=raw_item,
        canonical_item=canonical,
        quantity=int(row.get("quantity", 0)),
        unit_price=unit_price,
        line_amount=money(row.get("amount"), currency),
        note=_to_str_or_none(row.get("note")),
    )
