"""
EVAL-ONLY ground truth extracted verbatim from generate_pdfs.py.

*** NEVER IMPORT THIS FROM src/ ***

Enforced by tests/test_integrity.py. If extraction can see these answers,
accuracy numbers are fiction.

Source of every literal below is generate_pdfs.py at the repo root. Values
are copied without interpretation.
"""

# ---------------------------------------------------------------------------
# INV-1011 — from generate_pdfs.py:create_clean_invoice
# ---------------------------------------------------------------------------
INV_1011 = {
    "invoice_number": "INV-1011",
    "vendor": "Summit Manufacturing Co.",
    "date": "2026-01-20",
    "due_date": "2026-02-20",
    # items = [("WidgetA", 6, 250.00), ("WidgetB", 3, 500.00)]
    "line_items": [
        ("WidgetA", 6, 250.00),
        ("WidgetB", 3, 500.00),
    ],
    # PDF renders only "Total:" — no subtotal or tax line.
    # Sum of line amounts: 6*250 + 3*500 = 3000
    "total_pdf": 3000.00,
    # The .txt counterpart carries a Subtotal + Tax(0%) + Total block that the
    # PDF omits. This is the "less complete" pairing CLAUDE.md §6 references.
    "pdf_missing_lines": ["Subtotal", "Tax", "explicit Total label pair"],
}


# ---------------------------------------------------------------------------
# INV-1012 — from generate_pdfs.py:create_messy_invoice
# The `lines = [...]` list is the source of truth. Corruption is DELIBERATE.
# ---------------------------------------------------------------------------
INV_1012_TEXT_LINES = [
    "                    I N V O I C E",
    "",
    "  FROM:  QuickShip Distributers",
    "         (formerly FastShip Ltd.)",
    "",
    "  INV NO:    INV 1012",
    "  DATE:      26-Jan-2O26",           # letter O deliberately in the year
    "  DUE:       25-Feb-2026",
    "",
    "  TO:    ACME Corp",
    "         Attn: Accounts Payble",
    "",
    "  ----------------------------------------",
    "  ITEM          QTY    PRICE     TOTAL",
    "  ----------------------------------------",
    "  Widget A       12    $250     $3,000.00",
    "  WidgetB         7    $500     $3,500.O0",  # letter O in the cent digits
    "  Gadget X        4    $750     $3,000.00",
    "  ----------------------------------------",
    "                  SUBTOTAL:     $9,500.00",
    "                  TAX (5%):       $475.00",
    "                  TOTAL:        $9,975.00",
    "",
    "  NOTES: Ref PO-20260115. Deliver to",
    "         warehouse dock B. Contact Jim",
    "         at ext 4421 with questions.",
    "",
    "  Terms: Net 30",
]

INV_1012 = {
    "invoice_number_true": "INV-1012",           # displayed as `INV 1012`
    "vendor_primary": "QuickShip Distributers",
    "vendor_claim": "formerly FastShip Ltd.",
    "date_true": "2026-01-26",                    # source shows `26-Jan-2O26`
    "due_date_true": "2026-02-25",
    "line_items": [
        # (raw_name, qty, unit_price, line_amount)
        ("Widget A", 12, 250, 3000.00),
        ("WidgetB", 7, 500, 3500.00),
        ("Gadget X", 4, 750, 3000.00),
    ],
    "subtotal": 9500.00,
    "tax": 475.00,
    "total": 9975.00,
    "po_reference": "PO-20260115",
    "payment_terms": "Net 30",
    "ocr_corruptions": [
        # (field, source_token, corrected_token, rule)
        ("date", "26-Jan-2O26", "26-Jan-2026", "O -> 0 in date token"),
        ("line_amount.WidgetB", "$3,500.O0", "$3,500.00", "O -> 0 in currency token"),
    ],
}


# ---------------------------------------------------------------------------
# INV-1013 — from generate_pdfs.py:create_bulk_invoice
# ---------------------------------------------------------------------------
INV_1013 = {
    "invoice_number": "INV-1013",
    "vendor": "Atlas Industrial Supply",
    "date": "2026-01-24",
    "due_date": "2026-03-24",
    "payment_terms": "Net 60",
    # bulk_items = [...] in the script
    "line_items": [
        # (raw_item, qty, unit_price, note)
        ("WidgetA", 15, 250.00, ""),
        ("WidgetB", 10, 500.00, ""),
        ("GadgetX",  5, 750.00, ""),
        ("WidgetA",  5, 240.00, "Volume discount"),
        ("WidgetB",  8, 480.00, "Volume discount"),
        ("GadgetX",  3, 750.00, "Expedited"),
        ("WidgetA",  2, 250.00, "Replacement"),
        ("GadgetX",  1, 750.00, "Sample"),
    ],
    # From the script: running_total = sum(qty*price) = 21040
    "subtotal_computed": 21040.00,
    # tax = running_total * 0.07
    "tax_rate": 0.07,
    "tax_computed": 1472.80,
    # grand_total = running_total + tax = 22512.80
    "grand_total_computed": 22512.80,
    # But the script renders `f"${grand_total + 50:,.2f}"` — the +$50 is DELIBERATE.
    "grand_total_rendered": 22562.80,
    "grand_total_discrepancy": 50.00,
    # Aggregate quantities per canonical item (matches CLAUDE.md §6 assertion)
    "aggregate_quantities": {"WidgetA": 22, "WidgetB": 18, "GadgetX": 9},
}
