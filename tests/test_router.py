"""Router fallback scope tests.

Structured formats fall back to the LLM only on a parse exception, never
on low field coverage. A successful parse of a sparse-but-valid document
is authoritative — the source omitted those fields deliberately, and
letting the LLM invent them is exactly what prompts/extractor.md forbids.
"""
import json
from pathlib import Path
from unittest.mock import patch

from src.adapters.router import extract


# ---------------------------------------------------------------------------
# Sparse-but-valid JSON does NOT fall back
# ---------------------------------------------------------------------------

def test_sparse_valid_json_does_not_fall_back(tmp_path: Path) -> None:
    """Hand-constructed JSON with 1/4 core fields present (well below any
    plausible coverage threshold). The parse succeeds, so no fallback."""
    p = tmp_path / "sparse.json"
    p.write_text(json.dumps({
        "invoice_number": "INV-9998",
        # deliberately missing: vendor, line_items, total
    }))

    # If the router made an LLM call, this patch would trip
    with patch("src.adapters.text_adapter.extract_from_text") as sentinel:
        result = extract(p)
        sentinel.assert_not_called()

    assert result.llm_fallback is False
    assert result.fallback_reason is None
    assert result.invoice.invoice_number == "INV-9998"
    assert result.invoice.vendor_name == ""
    assert result.invoice.line_items == []
    assert result.invoice.stated_total is None


def test_malformed_json_falls_back_to_llm(tmp_path: Path) -> None:
    """Parse EXCEPTION is still a valid fallback trigger. Only coverage
    was removed — genuinely broken files must still route to the LLM."""
    p = tmp_path / "broken.json"
    p.write_text("{not: valid json")

    with patch("src.adapters.text_adapter.extract_from_text") as sentinel:
        # Have the sentinel return a valid-shaped Invoice so the router completes
        from decimal import Decimal
        from src.schema import Invoice
        sentinel.return_value = Invoice(
            invoice_number_raw="", invoice_number="",
            vendor_raw="", vendor_name="",
            source_file=str(p), source_format="json",
            file_hash=Invoice.compute_file_hash(b""),
        )
        result = extract(p)

    assert sentinel.called, "malformed JSON must still trigger LLM fallback"
    assert result.llm_fallback is True
    assert result.fallback_reason is not None
    assert "parse_error" in result.fallback_reason


# ---------------------------------------------------------------------------
# INV-1009 regression: existing behavior must not change
# ---------------------------------------------------------------------------

def test_inv_1009_still_parses_deterministically_no_fallback() -> None:
    """The corpus's known-permissive JSON: empty vendor, negative qty,
    negative total, null due date. Must not trigger fallback."""
    result = extract(Path("data/invoices/invoice_1009.json"))
    assert result.adapter_used == "json"
    assert result.llm_fallback is False
    assert result.invoice.vendor_name == ""
    assert result.invoice.line_items[0].quantity == -5
