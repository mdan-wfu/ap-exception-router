"""Text adapter — LLM extraction driven by prompts/extractor.md.

Also serves as the fallback path for the deterministic adapters: pass raw
text via `extract_text_content()` when a JSON/CSV/XML file fails to parse.

Repair loop: on Pydantic ValidationError, feed the error back to the model
and retry, capped at MAX_REPAIR_ATTEMPTS.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.adapters._common import parse_date
from src.config import MAX_REPAIR_ATTEMPTS
from src.llm import CassetteStore, LLMProvider
from src.schema import (
    AdditionalCharge,
    Correction,
    Invoice,
    LineItem,
    Money,
)
from src.store.canonical import (
    canonicalize_item,
    normalize_invoice_number,
    parse_vendor,
)


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "extractor.md"


# ---------------------------------------------------------------------------
# Extraction schema — what we ask the LLM to return.
#
# Deliberately separate from `Invoice`: no computed fields, no provenance
# (source_file, file_hash, semantic_hash), no minLength/maxLength/pattern
# constraints — see CLAUDE.md §3a for the xAI schema restrictions.
# ---------------------------------------------------------------------------

class _ExtLineItem(BaseModel):
    raw_item_name: str
    quantity: int = 0
    unit_price_amount: float = 0
    line_amount: float | None = None
    note: str | None = None
    confidence: float = 1.0


class _ExtAdditionalCharge(BaseModel):
    label: str
    amount: float


class _ExtCorrection(BaseModel):
    field_path: str
    original: str
    corrected: str
    reason: str


class ExtractedInvoice(BaseModel):
    invoice_number_raw: str | None = None
    vendor_raw: str = ""
    vendor_claims: list[str] = Field(default_factory=list)
    vendor_address: str | None = None
    vendor_email: str | None = None
    date_raw: str | None = None
    invoice_date: str | None = None
    due_date_raw: str | None = None
    due_date: str | None = None
    currency: str = "USD"
    line_items: list[_ExtLineItem] = Field(default_factory=list)
    additional_charges: list[_ExtAdditionalCharge] = Field(default_factory=list)
    stated_subtotal: float | None = None
    stated_tax: float | None = None
    stated_total: float | None = None
    payment_terms: str | None = None
    references: list[str] = Field(default_factory=list)
    notes: str | None = None
    corrections: list[_ExtCorrection] = Field(default_factory=list)
    extraction_confidence: float = 1.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_provider: LLMProvider | None = None


def _get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = LLMProvider(cassette_store=CassetteStore())
    return _provider


def set_provider(provider: LLMProvider) -> None:
    """Test hook: inject a provider (e.g., with an explicit CassetteStore)."""
    global _provider
    _provider = provider


def extract(path: Path) -> Invoice:
    """Extract an Invoice from a .txt file (or any text source)."""
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8", errors="replace")
    extracted = _extract_via_llm(text, prompt_name="extractor")
    return _to_invoice(
        extracted,
        source_file=str(path),
        source_format=path.suffix.lstrip(".") or "txt",
        raw_bytes=raw_bytes,
    )


def extract_from_text(
    text: str,
    *,
    source_file: str,
    source_format: str,
    raw_bytes: bytes,
) -> Invoice:
    """Fallback entrypoint used by the router when a deterministic parse fails
    or field coverage is too low. `raw_bytes` is the source file's bytes so the
    file_hash reflects the on-disk artifact, not the extracted text."""
    extracted = _extract_via_llm(text, prompt_name="extractor")
    return _to_invoice(
        extracted,
        source_file=source_file,
        source_format=source_format,
        raw_bytes=raw_bytes,
    )


# ---------------------------------------------------------------------------
# LLM call + repair loop
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE: str | None = None


def _load_prompt() -> str:
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        _PROMPT_TEMPLATE = PROMPT_PATH.read_text()
    return _PROMPT_TEMPLATE


def _extract_via_llm(document_text: str, *, prompt_name: str) -> ExtractedInvoice:
    provider = _get_provider()
    template = _load_prompt()
    prompt_body = template.replace("<<DOCUMENT>>", document_text)

    messages = [{"role": "user", "content": prompt_body}]
    last_error: str | None = None

    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        if last_error is not None:
            # Repair loop: append the error and ask the model to fix it
            messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "Your previous response failed schema validation with:\n\n"
                        f"{last_error}\n\n"
                        "Return a corrected JSON object matching the schema."
                    ),
                }
            ]

        result = provider.chat(
            messages,
            response_schema=ExtractedInvoice,
            prompt_name=prompt_name,
        )

        if isinstance(result.parsed, ExtractedInvoice):
            return result.parsed

        # Try one manual validation to capture a good error message
        try:
            return ExtractedInvoice.model_validate_json(result.content or "{}")
        except ValidationError as exc:
            last_error = str(exc)
            if attempt >= MAX_REPAIR_ATTEMPTS:
                raise
    raise RuntimeError("unreachable: repair loop exited without return or raise")


# ---------------------------------------------------------------------------
# ExtractedInvoice -> Invoice
# ---------------------------------------------------------------------------

def _to_invoice(
    extracted: ExtractedInvoice,
    *,
    source_file: str,
    source_format: str,
    raw_bytes: bytes,
) -> Invoice:
    invoice_number_raw = extracted.invoice_number_raw or ""
    invoice_number = (
        normalize_invoice_number(invoice_number_raw) if invoice_number_raw else ""
    )
    vendor_name, parsed_claims = parse_vendor(extracted.vendor_raw or "")
    # Union of parsed claims and any additional claims the LLM listed
    merged_claims = list(dict.fromkeys(parsed_claims + list(extracted.vendor_claims)))

    currency = extracted.currency or "USD"

    line_items = [
        _to_line_item(li, currency) for li in extracted.line_items
    ]

    additional = [
        AdditionalCharge(
            label=ac.label,
            amount=Money(amount_native=Decimal(str(ac.amount)), currency=currency),
        )
        for ac in extracted.additional_charges
    ]

    # Trust the model's parsed dates only if we can re-parse them cleanly.
    # A model that returns `invoice_date: "yesterday"` is a repair-loop bug;
    # we drop that value and preserve the raw literal.
    invoice_date = _revalidate_date(extracted.invoice_date, extracted.date_raw)
    due_date = _revalidate_date(extracted.due_date, extracted.due_date_raw)

    corrections = [
        Correction(
            field_path=c.field_path,
            original=c.original,
            corrected=c.corrected,
            reason=c.reason,
        )
        for c in extracted.corrections
    ]

    return Invoice(
        invoice_number_raw=invoice_number_raw,
        invoice_number=invoice_number,
        vendor_raw=extracted.vendor_raw or "",
        vendor_name=vendor_name,
        vendor_claims=merged_claims,
        vendor_address=extracted.vendor_address,
        vendor_email=extracted.vendor_email,
        date_raw=extracted.date_raw,
        invoice_date=invoice_date,
        due_date_raw=extracted.due_date_raw,
        due_date=due_date,
        line_items=line_items,
        additional_charges=additional,
        stated_subtotal=_money(extracted.stated_subtotal, currency),
        stated_tax=_money(extracted.stated_tax, currency),
        stated_total=_money(extracted.stated_total, currency),
        payment_terms=extracted.payment_terms,
        references=list(extracted.references),
        notes=extracted.notes,
        source_file=source_file,
        source_format=source_format,
        corrections=corrections,
        extraction_confidence=extracted.extraction_confidence,
        file_hash=Invoice.compute_file_hash(raw_bytes),
    )


def _to_line_item(li: _ExtLineItem, currency: str) -> LineItem:
    canonical, _ = canonicalize_item(li.raw_item_name)
    return LineItem(
        raw_item_name=li.raw_item_name,
        canonical_item=canonical,
        quantity=li.quantity,
        unit_price=Money(
            amount_native=Decimal(str(li.unit_price_amount)),
            currency=currency,
        ),
        line_amount=(
            Money(amount_native=Decimal(str(li.line_amount)), currency=currency)
            if li.line_amount is not None else None
        ),
        note=li.note,
        confidence=li.confidence,
    )


def _money(value: Any, currency: str) -> Money | None:
    if value is None:
        return None
    return Money(amount_native=Decimal(str(value)), currency=currency)


def _revalidate_date(parsed: str | None, raw: str | None) -> str | None:
    """Only accept a parsed date if we can re-parse it deterministically.
    Guards against the model returning literal `yesterday` as `invoice_date`."""
    if parsed is None:
        return None
    return parse_date(parsed)
