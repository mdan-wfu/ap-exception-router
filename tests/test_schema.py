"""Schema tests — file_hash / semantic_hash duplicate-detection split.

Two levels of test here:

  1. Pure unit tests on the hash functions themselves — always run.
     They exercise `compute_file_hash` and `compute_semantic_hash` directly
     so Phase 2 changes cannot silently break the hash design.

  2. Integration tests that exercise the txt / pdf / json adapters against
     real corpus files (INV-1011 txt/pdf pair, INV-1004 vs INV-1004_revised).
     SKIPPED until Phase 3 wires the adapters up. Remove the skip markers
     as part of Phase 3, at which point the `share_semantic_hash /
     differ_on_file_hash` invariant is verified end to end.
"""
from decimal import Decimal
import hashlib

import pytest

from src.schema import Invoice, LineItem, Money


# ---------------------------------------------------------------------------
# Pure unit tests — no adapters required
# ---------------------------------------------------------------------------

def test_compute_file_hash_matches_sha256() -> None:
    assert Invoice.compute_file_hash(b"hello") == hashlib.sha256(b"hello").hexdigest()


def _line(item: str, qty: int, price: str) -> LineItem:
    return LineItem(
        raw_item_name=item,
        canonical_item=item,
        quantity=qty,
        unit_price=Money(amount_native=Decimal(price), currency="USD"),
    )


def test_compute_semantic_hash_is_deterministic() -> None:
    line = _line("WidgetA", 6, "250")
    total = Money(amount_native=Decimal("1500"), currency="USD")
    a = Invoice.compute_semantic_hash("INV-1011", "Summit", [line], total)
    b = Invoice.compute_semantic_hash("INV-1011", "Summit", [line], total)
    assert a == b
    assert len(a) == 64  # SHA-256 hex


def test_compute_semantic_hash_ignores_line_item_order() -> None:
    """The hash sorts line-item tuples, so upstream ordering shouldn't affect it."""
    total = Money(amount_native=Decimal("3000"), currency="USD")
    forward = [_line("WidgetA", 6, "250"), _line("WidgetB", 3, "500")]
    reversed_ = list(reversed(forward))
    assert (
        Invoice.compute_semantic_hash("INV-1011", "Summit", forward, total)
        == Invoice.compute_semantic_hash("INV-1011", "Summit", reversed_, total)
    )


def test_compute_semantic_hash_treats_250_and_250_00_as_equal() -> None:
    """Quantization means "250" and "250.00" produce the same hash — so
    format drift between adapters doesn't create phantom differences."""
    total = Money(amount_native=Decimal("1500"), currency="USD")
    h_int = Invoice.compute_semantic_hash("INV-X", "V", [_line("WidgetA", 6, "250")], total)
    h_cent = Invoice.compute_semantic_hash("INV-X", "V", [_line("WidgetA", 6, "250.00")], total)
    assert h_int == h_cent


def test_compute_semantic_hash_changes_when_total_changes() -> None:
    line = _line("WidgetA", 1, "100")
    h1 = Invoice.compute_semantic_hash(
        "INV-X", "V", [line], Money(amount_native=Decimal("100"), currency="USD")
    )
    h2 = Invoice.compute_semantic_hash(
        "INV-X", "V", [line], Money(amount_native=Decimal("200"), currency="USD")
    )
    assert h1 != h2


def test_compute_semantic_hash_changes_when_line_items_change() -> None:
    total = Money(amount_native=Decimal("1500"), currency="USD")
    h1 = Invoice.compute_semantic_hash("INV-X", "V", [_line("WidgetA", 6, "250")], total)
    h2 = Invoice.compute_semantic_hash("INV-X", "V", [_line("WidgetA", 5, "300")], total)
    assert h1 != h2


# ---------------------------------------------------------------------------
# Integration tests — SKIPPED until Phase 3 adds adapters.
# Remove the skip markers as part of Phase 3.
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="requires Phase 3 adapters (text_adapter, pdf_adapter)")
def test_inv_1011_txt_and_pdf_share_semantic_hash_but_differ_on_file_hash() -> None:
    """INV-1011 exists as both .txt and rendered .pdf. The PDF omits the
    subtotal and tax lines the txt source contains, so:
      - file_hash MUST differ (different raw bytes)
      - semantic_hash MUST match (same invoice number, vendor, line items, total)
    This is the DP-001 (same invoice, two files) vs DP-002 (same number,
    differing content) discrimination.
    """
    from src.adapters.text_adapter import extract as extract_txt  # Phase 3
    from src.adapters.pdf_adapter import extract as extract_pdf   # Phase 3

    txt = extract_txt("data/invoices/invoice_1011.txt")
    pdf = extract_pdf("data/invoices/invoice_1011.pdf")
    assert txt.file_hash != pdf.file_hash
    assert txt.semantic_hash == pdf.semantic_hash


@pytest.mark.skip(reason="requires Phase 3 adapter (json_adapter)")
def test_inv_1004_and_revised_produce_differing_semantic_hash() -> None:
    """INV-1004 and INV-1004_revised share the same normalized invoice number
    (dedupe key) but their line items and totals genuinely differ. Semantic
    hash MUST NOT collide, or dedupe eats the flagship escalation case.
    """
    from src.adapters.json_adapter import extract as extract_json  # Phase 3

    original = extract_json("data/invoices/invoice_1004.json")
    revised = extract_json("data/invoices/invoice_1004_revised.json")
    assert original.semantic_hash != revised.semantic_hash
