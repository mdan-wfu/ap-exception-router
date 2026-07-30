"""Read-only lookup tools for the Adjudicator (CLAUDE.md §2.1a).

Every tool returns FACTS. No tool returns a judgment, a recommendation,
a score, a severity, or a decision. If a tool's return value contains
a judgment term ("suspicious", "approve", "risky", …), the boundary
has been crossed and the taxonomy in §2.1 has been violated.

The `TOOLS` list below is the single source of truth Phase 5c consumes
to hand tool schemas to the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from src.tools.audit_store import AuditStore, RunHistoryRow
from src.tools.invoice_tools import get_prior_invoice
from src.tools.item_tools import get_item_reference
from src.tools.models import (
    ItemQuery,
    ItemReferenceResult,
    PolicyQuery,
    PolicyResult,
    PriorInvoiceQuery,
    PriorInvoiceResult,
    VendorFuzzyCandidate,
    VendorHistoryQuery,
    VendorHistoryResult,
    VendorMasterRow,
    VendorRecordQuery,
    VendorRecordResult,
)
from src.tools.policy_tool import get_policy
from src.tools.vendor_tools import get_vendor_invoice_history, get_vendor_record


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    fn: Callable[..., BaseModel]
    input_model: type[BaseModel]
    output_model: type[BaseModel]

    def openai_schema(self) -> dict[str, Any]:
        """The tool schema in the OpenAI/xAI tool-calling shape.

        Respects the Phase 0 constraint: no minLength/maxLength/minItems/
        maxItems/pattern anywhere in the schema.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


TOOLS: list[Tool] = [
    Tool(
        name="get_vendor_record",
        description=(
            "Look up a vendor in the master. Returns the exact record if one "
            "exists, plus any fuzzy candidates with match scores and each "
            "candidate's status (active/inactive) and relationship_since date. "
            "The Adjudicator interprets these facts; the tool does not."
        ),
        fn=get_vendor_record,
        input_model=VendorRecordQuery,
        output_model=VendorRecordResult,
    ),
    Tool(
        name="get_vendor_invoice_history",
        description=(
            "Prior invoices for this vendor from the audit store: count, total "
            "value in USD, first and last seen dates, and a count of prior "
            "outcomes by type. Zero on an empty store — a first-time payee is "
            "a valid answer, not an error."
        ),
        fn=get_vendor_invoice_history,
        input_model=VendorHistoryQuery,
        output_model=VendorHistoryResult,
    ),
    Tool(
        name="get_item_reference",
        description=(
            "Look up an inventory item by name (canonicalized first). Returns "
            "stock, reference_unit_price, category, and active flag. Never "
            "fuzzy-matches items — an unknown item returns found=False."
        ),
        fn=get_item_reference,
        input_model=ItemQuery,
        output_model=ItemReferenceResult,
    ),
    Tool(
        name="get_prior_invoice",
        description=(
            "Look up any prior submission of this invoice number in the audit "
            "store. Returns semantic_hash, stated total, source file, and "
            "prior outcome. Empty result on first submission — that is the "
            "meaningful answer for a new invoice."
        ),
        fn=get_prior_invoice,
        input_model=PriorInvoiceQuery,
        output_model=PriorInvoiceResult,
    ),
    Tool(
        name="get_policy",
        description=(
            "Look up the documented trigger, detection method, and business "
            "rationale for a finding code, from the exception taxonomy. Does "
            "NOT return a recommended action or a severity assessment."
        ),
        fn=get_policy,
        input_model=PolicyQuery,
        output_model=PolicyResult,
    ),
]


TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


__all__ = [
    "AuditStore",
    "RunHistoryRow",
    "TOOLS",
    "TOOLS_BY_NAME",
    "Tool",
    "get_item_reference",
    "get_policy",
    "get_prior_invoice",
    "get_vendor_invoice_history",
    "get_vendor_record",
]
