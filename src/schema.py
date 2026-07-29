"""Canonical Pydantic v2 models.

Design notes worth carrying forward:

- Every monetary value is `Decimal`. Never `float`. Cent-level comparison on
  floats fabricates arithmetic findings.
- Every monetary value stores both `amount_native` and `amount_usd`. Both are
  evidence; neither is overwritten with the other. FX conversion is applied
  at construction, using `FX_RATES` from `src.config`.
- `raw_item_name` and `canonical_item` are both retained on every line item.
  The raw name is evidence, the canonical name is the lookup key.
- Every field in `Invoice` exists because a specific corpus document needs it.
  What has no slot here is silently lost by extraction. See DECISIONS.md.
- Stated totals ≠ computed totals. This schema stores what the DOCUMENT claims.
  Recomputation happens in Phase 4 (`src/validators/arithmetic.py`).
- `Finding` codes are stable identifiers; see CLAUDE.md §5 and
  `docs/exception-taxonomy.md` (Phase 7).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.config import FX_RATES

# Amounts are quantized to this precision before hashing so that "250" and
# "250.00" produce the same semantic hash. Cent precision is not enough:
# unit prices can carry sub-cent fractions in some contract structures.
_HASH_QUANTIZE = Decimal("0.0001")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Outcome(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Money — the atomic monetary value. Native + USD always retained.
# ---------------------------------------------------------------------------

class Money(BaseModel):
    amount_native: Decimal
    currency: str
    amount_usd: Decimal

    model_config = ConfigDict(str_strip_whitespace=True, frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _fill_usd(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        native = data.get("amount_native")
        currency = data.get("currency")
        given_usd = data.get("amount_usd")
        if given_usd is None and native is not None and currency is not None:
            native_dec = Decimal(str(native))
            if currency == "USD":
                rate = Decimal("1")
            elif currency in FX_RATES:
                rate = Decimal(str(FX_RATES[currency]))
            else:
                raise ValueError(
                    f"No FX rate configured for currency: {currency!r}. "
                    f"Extend src.config.FX_RATES."
                )
            data["amount_usd"] = native_dec * rate
        return data


# ---------------------------------------------------------------------------
# Correction — one repair the extractor made to a raw value.
# ---------------------------------------------------------------------------

class Correction(BaseModel):
    model_config = ConfigDict(frozen=True)

    field_path: str
    original: str
    corrected: str
    reason: str


# ---------------------------------------------------------------------------
# LineItem — one row on an invoice. Raw + canonical both retained.
# ---------------------------------------------------------------------------

class LineItem(BaseModel):
    # Frozen: an Invoice is frozen and derives its semantic_hash from its
    # line items. If LineItem were mutable, invoice.line_items[0].quantity = 999
    # would silently change the invoice's semantic identity without invalidating
    # the cached hash.
    model_config = ConfigDict(frozen=True)

    raw_item_name: str
    canonical_item: str | None = None
    quantity: int
    unit_price: Money
    line_amount: Money | None = None
    note: str | None = None
    confidence: float = 1.0
    provenance: str | None = None


# ---------------------------------------------------------------------------
# AdditionalCharge — non-line-item charges (INV-1010 shipping, etc.).
# ---------------------------------------------------------------------------

class AdditionalCharge(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    amount: Money


# ---------------------------------------------------------------------------
# Invoice — the canonical extracted invoice.
# ---------------------------------------------------------------------------

class Invoice(BaseModel):
    # Frozen. Downstream code that needs a different Invoice constructs a new
    # one via `.model_copy(update=...)`, which triggers `_fill_semantic_hash`
    # again. A mutable Invoice could drift from its own semantic_hash — the
    # hash is computed once at construction, and mutating a field after that
    # would leave the cached value stale.
    model_config = ConfigDict(frozen=True)

    # Identifiers — raw and normalized both retained
    invoice_number_raw: str
    invoice_number: str  # normalized to INV-{digits} via canonical.normalize_invoice_number

    # Vendor — raw, primary name, secondary claims, contact
    vendor_raw: str
    vendor_name: str
    vendor_claims: list[str] = Field(default_factory=list)
    vendor_address: str | None = None
    vendor_email: str | None = None

    # Dates as strings — parsing/validation happens in the terms validator
    invoice_date: str | None = None
    due_date: str | None = None

    # What the DOCUMENT claims
    line_items: list[LineItem] = Field(default_factory=list)
    additional_charges: list[AdditionalCharge] = Field(default_factory=list)
    stated_subtotal: Money | None = None
    stated_tax: Money | None = None
    stated_total: Money | None = None

    # Meta / provenance
    payment_terms: str | None = None
    references: list[str] = Field(default_factory=list)
    notes: str | None = None

    source_file: str
    source_format: str
    corrections: list[Correction] = Field(default_factory=list)
    extraction_confidence: float = 1.0

    # Two hashes serve two different duplicate-detection questions.
    #
    #   file_hash     — SHA-256 of raw file bytes. Answers "is this the same
    #                    file?". Zero matches on this corpus, because a .txt
    #                    invoice and its rendered .pdf are never byte-identical.
    #
    #   semantic_hash — SHA-256 of the invoice's semantic core (normalized
    #                    number, vendor, sorted line-item tuples, stated total
    #                    in USD). Deliberately EXCLUDES subtotal, tax, notes,
    #                    references, and source format so that INV-1011's
    #                    txt/pdf pair — where the PDF omits subtotal/tax lines
    #                    — classifies as DP-001 (same invoice, two files) and
    #                    not DP-002 (same number, differing content).
    #                    INV-1004 vs INV-1004_revised still separate here,
    #                    because line items and totals genuinely differ.
    file_hash: str
    semantic_hash: str = ""

    @staticmethod
    def compute_file_hash(content: bytes) -> str:
        """SHA-256 of raw file bytes. Answers 'is this the same file?'."""
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def compute_semantic_hash(
        invoice_number: str,
        vendor_name: str,
        line_items: list[LineItem],
        stated_total: Money | None,
    ) -> str:
        """SHA-256 over the invoice's semantic core. See field docstring above."""
        items_key = sorted(
            [
                [
                    li.canonical_item or li.raw_item_name,
                    li.quantity,
                    format(li.unit_price.amount_usd.quantize(_HASH_QUANTIZE), "f"),
                ]
                for li in line_items
            ]
        )
        total_str = (
            format(stated_total.amount_usd.quantize(_HASH_QUANTIZE), "f")
            if stated_total is not None
            else ""
        )
        blob = json.dumps(
            {
                "invoice_number": invoice_number,
                "vendor_name": vendor_name,
                "items": items_key,
                "total_usd": total_str,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    @model_validator(mode="after")
    def _fill_semantic_hash(self) -> "Invoice":
        # `frozen=True` prevents `self.semantic_hash = ...`; bypass via
        # `object.__setattr__` since we are still inside the validator, before
        # the instance is exposed to any caller.
        if not self.semantic_hash:
            object.__setattr__(
                self,
                "semantic_hash",
                self.compute_semantic_hash(
                    self.invoice_number,
                    self.vendor_name,
                    self.line_items,
                    self.stated_total,
                ),
            )
        return self


# ---------------------------------------------------------------------------
# Finding — one deterministic check emission.
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    code: str
    severity: Severity
    message: str
    evidence: str
    field_path: str | None = None


# ---------------------------------------------------------------------------
# Decision — Adjudicator output.
# ---------------------------------------------------------------------------

class Decision(BaseModel):
    outcome: Outcome
    rationale: str
    critic_challenge: str | None = None
    revised: bool = False
    confidence: float


# ---------------------------------------------------------------------------
# Trace entries: model calls + tool calls.
# ---------------------------------------------------------------------------

class ModelCall(BaseModel):
    """A single LLM API call.

    All three identifiers are stored:
      - `requested_model` — what we asked for
      - `resolved_model`  — what `response.model` echoed back (on xAI, this
                             is the alias verbatim; it does NOT prove which
                             weights served the request)
      - `system_fingerprint` — the opaque backend build identifier; the ONLY
                             field that would detect a silent alias remap.
                             None if the provider omits it.
    """
    model_config = ConfigDict(protected_namespaces=())

    requested_model: str
    resolved_model: str
    system_fingerprint: str | None = None
    prompt_name: str | None = None
    tokens_in: int
    tokens_out: int
    latency_ms: float
    cost_usd: Decimal = Decimal("0")
    timestamp: datetime


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: Any
    latency_ms: float
    timestamp: datetime


# ---------------------------------------------------------------------------
# RunRecord — full audit trail for a single invoice.
# ---------------------------------------------------------------------------

class RunRecord(BaseModel):
    invoice_number: str | None
    source_file: str
    nodes_fired: list[str] = Field(default_factory=list)
    model_calls: list[ModelCall] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    decision: Decision | None = None
    started_at: datetime
    finished_at: datetime | None = None
    terminal_status: Outcome
    failure_reason: str | None = None
