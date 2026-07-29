"""Schema tests — specifically the file_hash / semantic_hash duplicate-detection split.

Rationale (mirrored in DECISIONS.md):
  file_hash fires zero times on this corpus because a .txt invoice and its
  rendered .pdf are never byte-identical. INV-1011's PDF omits the subtotal
  and tax lines its .txt source contains, so a hash that covers those fields
  would misclassify the pair as DP-002 (same number, differing content) when
  the correct classification is DP-001 (same invoice, two files).
"""
from decimal import Decimal

from src.schema import Invoice, LineItem, Money


def _inv_1011(
    source_format: str,
    stated_subtotal: Money | None,
    stated_tax: Money | None,
    file_hash: str,
) -> Invoice:
    """Construct an INV-1011 with identical line items and total in every form,
    varying only the fields that the semantic hash deliberately excludes."""
    return Invoice(
        invoice_number_raw="INV-1011",
        invoice_number="INV-1011",
        vendor_raw="Summit Manufacturing Co.",
        vendor_name="Summit Manufacturing Co.",
        source_file=f"invoice_1011.{source_format}",
        source_format=source_format,
        file_hash=file_hash,
        line_items=[
            LineItem(
                raw_item_name="WidgetA",
                canonical_item="WidgetA",
                quantity=6,
                unit_price=Money(amount_native=Decimal("250"), currency="USD"),
                line_amount=Money(amount_native=Decimal("1500"), currency="USD"),
            ),
            LineItem(
                raw_item_name="WidgetB",
                canonical_item="WidgetB",
                quantity=3,
                unit_price=Money(amount_native=Decimal("500"), currency="USD"),
                line_amount=Money(amount_native=Decimal("1500"), currency="USD"),
            ),
        ],
        stated_subtotal=stated_subtotal,
        stated_tax=stated_tax,
        stated_total=Money(amount_native=Decimal("3000"), currency="USD"),
    )


def test_inv_1011_txt_and_pdf_share_semantic_hash_but_differ_on_file_hash() -> None:
    txt = _inv_1011(
        source_format="txt",
        stated_subtotal=Money(amount_native=Decimal("3000"), currency="USD"),
        stated_tax=Money(amount_native=Decimal("0"), currency="USD"),
        file_hash="txt-bytes-hash-marker",
    )
    pdf = _inv_1011(
        source_format="pdf",
        stated_subtotal=None,  # PDF renders only "Total:" — no subtotal/tax lines
        stated_tax=None,
        file_hash="pdf-bytes-hash-marker",
    )
    assert txt.file_hash != pdf.file_hash, "raw bytes differ; file_hash must too"
    assert txt.semantic_hash == pdf.semantic_hash, (
        "semantic core is identical (number, vendor, line items, total); "
        "subtotal/tax/format must not enter the hash"
    )


def _inv_1004(
    line_quantity: int,
    unit_price_native: str,
    total_native: str,
    file_hash: str,
) -> Invoice:
    return Invoice(
        invoice_number_raw="INV-1004",
        invoice_number="INV-1004",
        vendor_raw="Reliable Components Inc.",
        vendor_name="Reliable Components Inc.",
        source_file="invoice_1004.json",
        source_format="json",
        file_hash=file_hash,
        line_items=[
            LineItem(
                raw_item_name="WidgetA",
                canonical_item="WidgetA",
                quantity=line_quantity,
                unit_price=Money(amount_native=Decimal(unit_price_native), currency="USD"),
            ),
        ],
        stated_total=Money(amount_native=Decimal(total_native), currency="USD"),
    )


def test_inv_1004_and_revised_produce_differing_semantic_hash() -> None:
    """Line items and totals genuinely differ between the two files —
    the semantic hash must NOT collide, or dedupe eats the escalation case."""
    original = _inv_1004(line_quantity=6, unit_price_native="315", total_native="1890",
                         file_hash="orig-bytes")
    revised = _inv_1004(line_quantity=12, unit_price_native="495", total_native="5940",
                        file_hash="rev-bytes")
    assert original.semantic_hash != revised.semantic_hash


def test_semantic_hash_stable_across_construction() -> None:
    """Constructing the same invoice twice yields the same semantic hash."""
    a = _inv_1011("txt",
                  Money(amount_native=Decimal("3000"), currency="USD"),
                  Money(amount_native=Decimal("0"), currency="USD"),
                  "a")
    b = _inv_1011("txt",
                  Money(amount_native=Decimal("3000"), currency="USD"),
                  Money(amount_native=Decimal("0"), currency="USD"),
                  "b")
    assert a.semantic_hash == b.semantic_hash


def test_compute_file_hash_helper() -> None:
    """compute_file_hash exists and is SHA-256 of the raw bytes."""
    import hashlib
    h = Invoice.compute_file_hash(b"hello")
    assert h == hashlib.sha256(b"hello").hexdigest()
