"""`pick_retained` — the fix for the INV-1011 payment_terms miss.

Batch orchestrators (`main.py`, `src/batch.py`, `eval/run_eval.py`) used
to select which file of a duplicate pair to process by taking whichever
sorted alphabetically first. That threw away the completeness score that
`find_duplicates` already computed for DP-001. On INV-1011 that cost the
`payment_terms` field: the PDF (less complete) beat the TXT.

`pick_retained` now sits between the extraction pass and the graph loop.
This file locks its behavior on the three real corpus duplicate pairs
plus a constructed equal-completeness pair for the tie-break.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.adapters.router import extract as router_extract
from src.validators import pick_retained, select_batch_retentions


CORPUS = Path("data/invoices")


def _extract(name: str):
    return router_extract(CORPUS / name).invoice


# ---------------------------------------------------------------------------
# Real corpus duplicate pairs
# ---------------------------------------------------------------------------

def test_inv1011_pair_selects_txt_because_it_carries_payment_terms():
    """The eval miss that motivated this fix. TXT has stated_subtotal,
    stated_tax, and payment_terms; the PDF has none of them (CLAUDE.md §6
    calls the PDF 'less complete than its txt source')."""
    pdf = _extract("invoice_1011.pdf")
    txt = _extract("invoice_1011.txt")
    chosen = pick_retained([pdf, txt])
    assert chosen.source_file.endswith("invoice_1011.txt"), (
        f"expected txt (more complete), got {chosen.source_file}"
    )
    assert chosen.payment_terms == "Net 30"


def test_inv1012_pair_selects_by_completeness_deterministically():
    """Both members of the INV-1012 pair extract subtotal/tax/terms.
    Whichever the rule prefers, it must be one specific file and stable."""
    pdf = _extract("invoice_1012.pdf")
    txt = _extract("invoice_1012.txt")
    chosen_ab = pick_retained([pdf, txt])
    chosen_ba = pick_retained([txt, pdf])
    assert chosen_ab.source_file == chosen_ba.source_file, (
        "pick_retained must be stable across input ordering"
    )
    # Which one? Both have all four completeness fields on this corpus,
    # so we fall through to tie-break by basename ("invoice_1012.pdf" <
    # "invoice_1012.txt" alphabetically → pdf).
    assert chosen_ab.source_file.endswith("invoice_1012.pdf")


def test_inv1013_pair_selects_by_completeness_deterministically():
    """Same shape as 1012: whichever the rule picks, must be stable."""
    json_inv = _extract("invoice_1013.json")
    pdf = _extract("invoice_1013.pdf")
    chosen_ab = pick_retained([json_inv, pdf])
    chosen_ba = pick_retained([pdf, json_inv])
    assert chosen_ab.source_file == chosen_ba.source_file
    # JSON extraction of 1013 gives full subtotal/tax/terms; PDF also gets
    # them via the LLM extractor. Both have 8 line items. Alphabetical
    # tie-break: "invoice_1013.json" < "invoice_1013.pdf" → json.
    assert chosen_ab.source_file.endswith("invoice_1013.json")


# ---------------------------------------------------------------------------
# Constructed equal-completeness pair — verifies alphabetical tie-break
# ---------------------------------------------------------------------------

def test_equal_completeness_falls_back_to_alphabetical_basename():
    """When two Invoices have identical completeness scores, alphabetical
    basename decides. Ensures the selection is fully deterministic even
    when both members are equally information-rich."""
    from decimal import Decimal
    from src.schema import Invoice, LineItem, Money

    def make(source_file: str) -> Invoice:
        usd = lambda x: Money(
            amount_native=Decimal(str(x)), currency="USD",
            amount_usd=Decimal(str(x)), fx_rate=None, fx_rate_as_of=None,
        )
        return Invoice(
            invoice_number_raw="INV-EQ", invoice_number="INV-EQ",
            vendor_raw="VendorEQ", vendor_name="VendorEQ",
            invoice_date="2026-01-01", due_date="2026-02-01",
            line_items=[LineItem(
                raw_item_name="X", canonical_item="X",
                quantity=1, unit_price=usd(10),
            )],
            stated_subtotal=usd(10),
            stated_tax=usd(0),
            stated_total=usd(10),
            payment_terms="Net 30",
            vendor_address="somewhere",
            source_file=source_file, source_format="json",
            file_hash="h", semantic_hash="h",
        )

    a = make("data/invoices/z_later.json")
    b = make("data/invoices/a_earlier.json")
    # Feed in both orderings — result must be the alphabetically earlier basename.
    assert pick_retained([a, b]).source_file.endswith("a_earlier.json")
    assert pick_retained([b, a]).source_file.endswith("a_earlier.json")


def test_singleton_group_returns_sole_member():
    """A group of one (invoice with no duplicate) returns unchanged."""
    inv = _extract("invoice_1001.txt")
    assert pick_retained([inv]) is inv


# ---------------------------------------------------------------------------
# select_batch_retentions — the scoping guard that prevents pick_retained
# from being applied to DP-002 (differing-content) groups
# ---------------------------------------------------------------------------

def test_inv1004_dp002_keeps_original_because_completeness_must_not_choose_between_submissions():
    """DP-002 group: invoice_1004.json (2 line items) and
    invoice_1004_revised.json (3 line items, revision marker) have
    DIFFERENT semantic hashes — they are genuinely different submissions
    under the same number, not two files of the same invoice. Completeness
    scoring must NOT auto-pick between them. The near-miss during the first
    fix attempt: pick_retained tie-broke on line_items and swapped to the
    revised submission, changing what the Adjudicator saw for INV-1004 and
    breaking recorded cassettes. Correct behavior: alphabetical-first
    (invoice_1004.json), leave the DP-002 "which is authoritative" to the
    human gate."""
    original = _extract("invoice_1004.json")
    revised = _extract("invoice_1004_revised.json")
    # Distinct hashes confirm this is a DP-002 group
    assert original.semantic_hash != revised.semantic_hash

    retentions = select_batch_retentions([original, revised])
    assert len(retentions) == 1
    # Alphabetical basename: "invoice_1004.json" < "invoice_1004_revised.json"
    assert any(sf.endswith("invoice_1004.json") for sf in retentions)
    assert not any(sf.endswith("invoice_1004_revised.json") for sf in retentions)


def test_inv1011_dp001_applies_completeness_and_selects_txt():
    """DP-001 group: invoice_1011.txt and invoice_1011.pdf share a
    semantic_hash (same invoice, two files). Completeness rule applies →
    the TXT (which carries stated_subtotal, stated_tax, payment_terms)
    wins over the PDF (which has none of them). This is the exact miss
    the eval surfaced."""
    txt = _extract("invoice_1011.txt")
    pdf = _extract("invoice_1011.pdf")
    assert txt.semantic_hash == pdf.semantic_hash, (
        "INV-1011 pair must share a semantic_hash for the DP-001 path to apply"
    )

    retentions = select_batch_retentions([txt, pdf])
    assert len(retentions) == 1
    assert any(sf.endswith("invoice_1011.txt") for sf in retentions)


def test_select_batch_retentions_over_full_corpus_shape():
    """Realistic mixed batch: singletons + one DP-001 pair + one DP-002
    pair. Confirms the three code paths (singleton pass-through, DP-001
    collapse, DP-002 alphabetical) all fire in one call."""
    invs = [
        _extract("invoice_1001.txt"),           # singleton
        _extract("invoice_1004.json"),          # DP-002 with revised
        _extract("invoice_1004_revised.json"),
        _extract("invoice_1011.txt"),           # DP-001 with pdf
        _extract("invoice_1011.pdf"),
    ]
    retentions = select_batch_retentions(invs)
    kept = sorted(Path(sf).name for sf in retentions)
    assert kept == [
        "invoice_1001.txt",
        "invoice_1004.json",          # DP-002 → alphabetical-first
        "invoice_1011.txt",           # DP-001 → completeness winner
    ]
