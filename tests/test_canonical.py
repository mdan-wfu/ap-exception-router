"""Canonicalization tests.

Covers every specified case from the Phase 1 task, plus the intended
INV-1004 collision between `invoice_1004.json` and `invoice_1004_revised.json`.
"""
import pytest

from src.store.canonical import (
    canonicalize_item,
    normalize_invoice_number,
    parse_vendor,
)


# ---------------------------------------------------------------------------
# canonicalize_item
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Widget A", "WidgetA"),
    ("WidgetB", "WidgetB"),
    ("Gadget X", "GadgetX"),
    ("WidgetA (rush order)", "WidgetA"),
    ("widgeta", "WidgetA"),
])
def test_canonicalize_item_matches(raw: str, expected: str) -> None:
    canonical, _ops = canonicalize_item(raw)
    assert canonical == expected


@pytest.mark.parametrize("raw", ["WidgetC", "SuperGizmo", "", "   "])
def test_canonicalize_item_no_match(raw: str) -> None:
    canonical, _ops = canonicalize_item(raw)
    assert canonical is None


def test_canonicalize_item_records_operations() -> None:
    """The operations string should show what was normalized."""
    _, ops = canonicalize_item("Widget A")
    assert "collapsed whitespace" in ops

    _, ops = canonicalize_item("WidgetA (rush order)")
    assert "stripped parenthetical" in ops

    _, ops = canonicalize_item("widgeta")
    assert "case-folded" in ops


def test_canonicalize_item_no_fuzzy_matching() -> None:
    """WidgetC must NOT resolve to WidgetA. Fuzzy matching is banned for items."""
    canonical, _ = canonicalize_item("WidgetC")
    assert canonical is None


# ---------------------------------------------------------------------------
# normalize_invoice_number
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("INV-1013", "INV-1013"),
    ("INV 1012", "INV-1012"),
    ("1002", "INV-1002"),
    ("Inv #: 1002", "INV-1002"),
    ("inv-1015", "INV-1015"),
])
def test_normalize_invoice_number(raw: str, expected: str) -> None:
    assert normalize_invoice_number(raw) == expected


def test_normalize_invoice_number_empty_raises() -> None:
    with pytest.raises(ValueError):
        normalize_invoice_number("")


def test_normalize_invoice_number_no_digits_raises() -> None:
    with pytest.raises(ValueError):
        normalize_invoice_number("INV-NO-NUMBER")


def test_inv_1004_collision_is_intended() -> None:
    """invoice_1004 and invoice_1004_revised both collapse to INV-1004."""
    assert normalize_invoice_number("1004") == "INV-1004"
    assert normalize_invoice_number("INV-1004") == "INV-1004"


# ---------------------------------------------------------------------------
# parse_vendor
# ---------------------------------------------------------------------------

def test_parse_vendor_formerly_claim() -> None:
    primary, claims = parse_vendor("QuickShip Distributers (formerly FastShip Ltd.)")
    assert primary == "QuickShip Distributers"
    assert claims == ["formerly FastShip Ltd."]


def test_parse_vendor_no_claim() -> None:
    primary, claims = parse_vendor("Widgets Inc.")
    assert primary == "Widgets Inc."
    assert claims == []


def test_parse_vendor_empty() -> None:
    primary, claims = parse_vendor("")
    assert primary == ""
    assert claims == []
