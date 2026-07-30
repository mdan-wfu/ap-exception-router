"""Pydantic input/output models for every tool.

Kept in one file so the schemas are easy to review together and the JSON
schemas sent to xAI can be generated in one place. No `minLength`,
`maxLength`, `minItems`, `maxItems`, or `pattern` on any of these — the
xAI structured-output constraint applies to tool schemas as well.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


_M = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# get_vendor_record
# ---------------------------------------------------------------------------

class VendorRecordQuery(BaseModel):
    model_config = _M
    name: str


class VendorMasterRow(BaseModel):
    model_config = _M
    name: str
    domain: str
    status: str                # "active" | "inactive"
    contracted_terms: str
    relationship_since: str


class VendorFuzzyCandidate(BaseModel):
    model_config = _M
    name: str
    score: float               # 0.0–1.0 raw SequenceMatcher ratio
    below_threshold: bool      # True if score < VN-002 match_threshold
    status: str
    relationship_since: str


class VendorRecordResult(BaseModel):
    model_config = _M
    query: str
    exact_match: VendorMasterRow | None
    fuzzy_candidates: list[VendorFuzzyCandidate]
    # Below this score, a candidate is neighborhood noise — not a name match.
    # See src/validators/vendor.py::FUZZY_THRESHOLD. Surfaced here so a reader
    # of the tool output does not mistake ranking among noise scores for
    # meaningful similarity.
    match_threshold: float


# ---------------------------------------------------------------------------
# get_vendor_invoice_history
# ---------------------------------------------------------------------------

class VendorHistoryQuery(BaseModel):
    model_config = _M
    name: str


class VendorHistoryResult(BaseModel):
    model_config = _M
    vendor_name: str
    invoice_count: int
    total_value_usd: Decimal
    first_seen: str | None
    last_seen: str | None
    prior_outcomes: dict[str, int]   # e.g. {"APPROVE": 3, "ESCALATE": 1}


# ---------------------------------------------------------------------------
# get_item_reference
# ---------------------------------------------------------------------------

class ItemQuery(BaseModel):
    model_config = _M
    item: str


class ItemReferenceResult(BaseModel):
    model_config = _M
    query: str
    found: bool
    canonical_name: str | None
    stock: int | None
    reference_unit_price: Decimal | None
    category: str | None
    active: bool | None


# ---------------------------------------------------------------------------
# get_prior_invoice
# ---------------------------------------------------------------------------

class PriorInvoiceQuery(BaseModel):
    model_config = _M
    invoice_number: str


class PriorInvoiceResult(BaseModel):
    model_config = _M
    invoice_number: str
    found: bool
    semantic_hash: str | None
    stated_total_usd: Decimal | None
    source_file: str | None
    prior_outcome: str | None


# ---------------------------------------------------------------------------
# get_policy
# ---------------------------------------------------------------------------

class PolicyQuery(BaseModel):
    model_config = _M
    finding_code: str


class PolicyResult(BaseModel):
    model_config = _M
    code: str
    found: bool
    trigger: str | None              # what triggers the finding
    detection: str | None            # how the validator detects it
    rationale: str | None            # documented reason the code exists
    corpus_examples: list[str]       # invoice numbers/paths mentioned in the doc
