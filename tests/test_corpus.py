"""Corpus-level behavioural tests.

Every file in data/invoices/ must produce an Invoice. Five specific behaviours
called out by the Phase 3 task are asserted directly.

These tests use whatever cassettes are on disk. Run once in `auto`/`live` mode
to seed cassettes, then all subsequent runs are pure `replay`.
"""
import os
from pathlib import Path

import pytest

from src.adapters.router import extract

INVOICES = Path("data/invoices")


# ---------------------------------------------------------------------------
# The full corpus loads
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    sorted(
        p for p in INVOICES.iterdir()
        if p.suffix.lower() in {".txt", ".pdf", ".json", ".csv", ".xml"}
    ),
    ids=lambda p: p.name,
)
def test_every_corpus_file_produces_an_invoice(path: Path) -> None:
    r = extract(path)
    assert r.invoice is not None
    assert r.invoice.invoice_number.startswith("INV-") or r.invoice.invoice_number == ""


# ---------------------------------------------------------------------------
# Task 7's five behavioural assertions
# ---------------------------------------------------------------------------

def test_inv_1012_declares_both_ocr_corrections_and_captures_fastship_claim() -> None:
    """The Phase 0b silent-repair failure must now be a declared repair."""
    r = extract(INVOICES / "invoice_1012.txt")
    inv = r.invoice

    # Both OCR substitutions declared
    originals = {c.original for c in inv.corrections}
    assert any("2O26" in o for o in originals), (
        f"Expected the `2O26` OCR correction to be declared. "
        f"Got corrections: {[c.model_dump() for c in inv.corrections]}"
    )
    assert any("O0" in o or "O.0" in o for o in originals), (
        f"Expected the `$3,500.O0` OCR correction to be declared. "
        f"Got corrections: {[c.model_dump() for c in inv.corrections]}"
    )

    # FastShip claim captured
    assert any("FastShip" in claim for claim in inv.vendor_claims), (
        f"Expected `formerly FastShip Ltd.` in vendor_claims. "
        f"Got: {inv.vendor_claims}"
    )


def test_inv_1013_preserves_exactly_eight_line_items_via_pdf() -> None:
    """pdfplumber preserves the table; collapse would be a regression."""
    r = extract(INVOICES / "invoice_1013.pdf")
    assert len(r.invoice.line_items) == 8


def test_inv_1003_yesterday_stays_in_due_date_raw() -> None:
    """The literal `yesterday` survives; the parsed field is null."""
    r = extract(INVOICES / "invoice_1003.txt")
    assert r.invoice.due_date is None
    assert r.invoice.due_date_raw is not None
    assert "yesterday" in r.invoice.due_date_raw.lower()


def test_inv_1006_vertical_csv_gives_exactly_two_line_items() -> None:
    """The repeated `item` key must not silently drop a line."""
    r = extract(INVOICES / "invoice_1006.csv")
    assert len(r.invoice.line_items) == 2


def test_inv_1010_shipping_lands_in_additional_charges() -> None:
    """The $150 shipping must not be folded into stated_subtotal."""
    r = extract(INVOICES / "invoice_1010.txt")
    shipping = [
        ac for ac in r.invoice.additional_charges
        if "shipping" in ac.label.lower()
    ]
    assert len(shipping) == 1, (
        f"Expected one shipping additional_charge. "
        f"Got: {[ac.model_dump() for ac in r.invoice.additional_charges]}"
    )
    assert shipping[0].amount.amount_native == 150
