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
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.config import FX_RATES


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

    model_config = ConfigDict(str_strip_whitespace=True)

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
    field_path: str
    original: str
    corrected: str
    reason: str


# ---------------------------------------------------------------------------
# LineItem — one row on an invoice. Raw + canonical both retained.
# ---------------------------------------------------------------------------

class LineItem(BaseModel):
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
    label: str
    amount: Money


# ---------------------------------------------------------------------------
# Invoice — the canonical extracted invoice.
# ---------------------------------------------------------------------------

class Invoice(BaseModel):
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
    content_hash: str

    @staticmethod
    def compute_content_hash(content: bytes) -> str:
        """SHA-256 of raw file bytes. Used for identical-file duplicate detection."""
        return hashlib.sha256(content).hexdigest()


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

    Both requested and resolved model identifiers are stored: `grok-4.5` is an
    alias with no dated equivalent available. The resolved id (from the API
    response) proves what actually answered on any given call.
    """
    model_config = ConfigDict(protected_namespaces=())

    requested_model: str
    resolved_model: str
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
