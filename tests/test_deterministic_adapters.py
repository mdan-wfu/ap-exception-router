"""Tests for the three deterministic adapters: JSON, CSV, XML.

Each adapter is tested against the corpus trap that motivates its design.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from src.adapters.csv_adapter import extract as extract_csv
from src.adapters.json_adapter import extract as extract_json
from src.adapters.xml_adapter import extract as extract_xml

INVOICES = Path("data/invoices")


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def test_json_inv_1005_missing_amount_leaves_line_amount_none() -> None:
    """Line items with no `amount` field must not compute one."""
    inv = extract_json(INVOICES / "invoice_1005.json")
    for li in inv.line_items:
        assert li.line_amount is None, f"{li.raw_item_name} got a synthesized amount"


def test_json_inv_1013_preserves_eight_distinct_line_items() -> None:
    """Three WidgetA rows must NOT merge into one."""
    inv = extract_json(INVOICES / "invoice_1013.json")
    assert len(inv.line_items) == 8
    widget_a = [li for li in inv.line_items if li.canonical_item == "WidgetA"]
    assert len(widget_a) == 3


def test_json_inv_1009_permissive_bad_data() -> None:
    """Empty vendor, null address, negative quantity must all survive."""
    inv = extract_json(INVOICES / "invoice_1009.json")
    assert inv.vendor_name == ""
    assert inv.vendor_address is None
    assert inv.due_date is None
    assert inv.line_items[0].quantity == -5
    assert inv.stated_total.amount_native == Decimal("-250.00")


def test_json_inv_1004_revised_captures_revision_and_notes() -> None:
    inv = extract_json(INVOICES / "invoice_1004_revised.json")
    assert "revision:R1" in inv.references
    assert inv.notes is not None
    assert "Revised invoice" in inv.notes


def test_json_inv_1013_grand_total_arithmetic_error_survives_extraction() -> None:
    """The +$50 discrepancy in the JSON must land in stated_total as-is —
    the adapter reports what the document claims, Phase 4 recomputes."""
    inv = extract_json(INVOICES / "invoice_1013.json")
    assert inv.stated_total.amount_native == Decimal("22562.80")
    assert inv.stated_subtotal.amount_native == Decimal("21040.00")
    assert inv.stated_tax.amount_native == Decimal("1472.80")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def test_csv_inv_1006_vertical_preserves_both_line_items() -> None:
    """DictReader would drop the first `item` row — positional parse must not."""
    inv = extract_csv(INVOICES / "invoice_1006.csv")
    assert len(inv.line_items) == 2
    assert {li.canonical_item for li in inv.line_items} == {"WidgetA", "WidgetB"}
    assert inv.line_items[0].quantity == 5
    assert inv.line_items[1].quantity == 3


def test_csv_inv_1007_row_per_item_with_summary() -> None:
    inv = extract_csv(INVOICES / "invoice_1007.csv")
    assert len(inv.line_items) == 3
    assert inv.stated_subtotal.amount_native == Decimal("14750.00")
    assert inv.stated_tax.amount_native == Decimal("885.00")
    assert inv.stated_total.amount_native == Decimal("15525.00")


def test_csv_inv_1007_us_date_parses_to_iso() -> None:
    """01/28/2026 must parse as MM/DD/YYYY, not DD/MM."""
    inv = extract_csv(INVOICES / "invoice_1007.csv")
    assert inv.date_raw == "01/28/2026"
    assert inv.invoice_date == "2026-01-28"
    assert inv.due_date == "2026-02-28"


def test_csv_inv_1015_iso_date_survives() -> None:
    inv = extract_csv(INVOICES / "invoice_1015.csv")
    assert inv.invoice_date == "2026-01-29"


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------

def test_xml_inv_1014_eur_conversion_at_money_construction() -> None:
    """WidgetB at €475 → $541.50 per CLAUDE.md §6."""
    inv = extract_xml(INVOICES / "invoice_1014.xml")
    assert inv.line_items[0].unit_price.currency == "EUR"
    widget_b = next(li for li in inv.line_items if li.canonical_item == "WidgetB")
    assert widget_b.unit_price.amount_native == Decimal("475.00")
    assert widget_b.unit_price.amount_usd == Decimal("475.00") * Decimal("1.14")


def test_xml_inv_1014_stated_total_is_native_eur() -> None:
    """Adapter must not convert. Money keeps both native and USD."""
    inv = extract_xml(INVOICES / "invoice_1014.xml")
    assert inv.stated_total.currency == "EUR"
    assert inv.stated_total.amount_native == Decimal("4125.00")
