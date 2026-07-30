"""Direct-invocation tests for the five Adjudicator tools.

Zero LLM calls. Each tool gets a hit, a miss, and one edge case, per the
Phase 5b brief. One additional test scans tool outputs for judgment
words to lock the fact/judgment boundary against future drift.
"""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.tools import (
    TOOLS,
    TOOLS_BY_NAME,
    AuditStore,
    RunHistoryRow,
    get_item_reference,
    get_policy,
    get_prior_invoice,
    get_vendor_invoice_history,
    get_vendor_record,
)
from src.tools.models import (
    ItemQuery,
    PolicyQuery,
    PriorInvoiceQuery,
    VendorHistoryQuery,
    VendorRecordQuery,
)


@pytest.fixture
def audit(tmp_path: Path) -> AuditStore:
    return AuditStore(path=tmp_path / "audit.sqlite")


# ---------------------------------------------------------------------------
# get_vendor_record
# ---------------------------------------------------------------------------

def test_get_vendor_record_quickship_surfaces_fastship_candidate() -> None:
    r = get_vendor_record(VendorRecordQuery(name="QuickShip Distributers"))
    assert r.exact_match is None
    fastship = next(
        (c for c in r.fuzzy_candidates if c.name == "FastShip Ltd."), None,
    )
    assert fastship is not None, (
        f"FastShip Ltd. must appear among candidates for QuickShip; got: "
        f"{[c.name for c in r.fuzzy_candidates]}"
    )
    assert fastship.status == "inactive"
    assert fastship.relationship_since == "2016-10-01"


def test_get_vendor_record_marks_noise_scores_below_threshold() -> None:
    """Fuzzy name similarity is NOT the signal that resolves INV-1012.
    Every QuickShip-vs-master score is below the VN-002 threshold; the
    tool must make that visible so the reader does not mistake ranking
    among noise scores (0.348 vs 0.343) for meaningful similarity."""
    r = get_vendor_record(VendorRecordQuery(name="QuickShip Distributers"))
    assert r.match_threshold == 0.70
    assert all(c.below_threshold for c in r.fuzzy_candidates), (
        f"expected every candidate below 0.70; got: "
        f"{[(c.name, c.score, c.below_threshold) for c in r.fuzzy_candidates]}"
    )


def test_get_vendor_record_surfaces_threshold_on_every_result() -> None:
    """The threshold field is set even when candidates exist above it."""
    r = get_vendor_record(VendorRecordQuery(name="Acme Industrial Supplies"))
    assert r.match_threshold == 0.70
    # Above-threshold candidates (if any) are flagged False; below True.
    for c in r.fuzzy_candidates:
        assert c.below_threshold == (c.score < r.match_threshold)


def test_get_vendor_record_exact_match_on_acme_industrial() -> None:
    """The false-positive vendor from Phase 4. Exact-matches cleanly."""
    r = get_vendor_record(VendorRecordQuery(name="Acme Industrial Supplies"))
    assert r.exact_match is not None
    assert r.exact_match.name == "Acme Industrial Supplies"
    assert r.exact_match.status == "active"


def test_get_vendor_record_empty_name_returns_no_result() -> None:
    r = get_vendor_record(VendorRecordQuery(name=""))
    assert r.exact_match is None
    assert r.fuzzy_candidates == []


def test_get_vendor_record_garbage_input_does_not_raise() -> None:
    r = get_vendor_record(VendorRecordQuery(name="!!!!!"))
    assert r.exact_match is None


# ---------------------------------------------------------------------------
# get_vendor_invoice_history
# ---------------------------------------------------------------------------

def test_get_vendor_history_on_empty_store_returns_zeros(audit) -> None:
    r = get_vendor_invoice_history(
        VendorHistoryQuery(name="Widgets Inc."), audit_store=audit,
    )
    assert r.invoice_count == 0
    assert r.total_value_usd == Decimal("0")
    assert r.first_seen is None
    assert r.last_seen is None
    assert r.prior_outcomes == {}


def test_get_vendor_history_aggregates_seeded_rows(audit) -> None:
    audit.record(RunHistoryRow(
        invoice_number="INV-0500", vendor_name="Widgets Inc.",
        stated_total_usd=Decimal("100.00"), semantic_hash="a",
        source_file="x.json", outcome="APPROVE",
        finished_at="2026-06-01T00:00:00Z",
    ))
    audit.record(RunHistoryRow(
        invoice_number="INV-0501", vendor_name="Widgets Inc.",
        stated_total_usd=Decimal("200.50"), semantic_hash="b",
        source_file="y.json", outcome="ESCALATE",
        finished_at="2026-07-01T00:00:00Z",
    ))
    r = get_vendor_invoice_history(
        VendorHistoryQuery(name="Widgets Inc."), audit_store=audit,
    )
    assert r.invoice_count == 2
    assert r.total_value_usd == Decimal("300.50")
    assert r.first_seen == "2026-06-01T00:00:00Z"
    assert r.last_seen == "2026-07-01T00:00:00Z"
    assert r.prior_outcomes == {"APPROVE": 1, "ESCALATE": 1}


# ---------------------------------------------------------------------------
# get_item_reference
# ---------------------------------------------------------------------------

def test_get_item_reference_canonicalizes_widget_a() -> None:
    r = get_item_reference(ItemQuery(item="Widget A"))
    assert r.found is True
    assert r.canonical_name == "WidgetA"
    assert r.stock == 15


def test_get_item_reference_widget_c_explicit_not_found() -> None:
    """WidgetC must NOT fuzzy-match to WidgetA — items never fuzzy match."""
    r = get_item_reference(ItemQuery(item="WidgetC"))
    assert r.found is False
    assert r.canonical_name is None
    assert r.stock is None
    assert r.reference_unit_price is None


def test_get_item_reference_fakeitem_visible_state() -> None:
    r = get_item_reference(ItemQuery(item="FakeItem"))
    assert r.found is True
    assert r.canonical_name == "FakeItem"
    assert r.stock == 0
    assert r.active is False
    assert r.reference_unit_price is None


def test_get_item_reference_garbage_input_does_not_raise() -> None:
    r = get_item_reference(ItemQuery(item="🌮"))
    assert r.found is False


# ---------------------------------------------------------------------------
# get_prior_invoice
# ---------------------------------------------------------------------------

def test_get_prior_invoice_empty_store_returns_not_found(audit) -> None:
    r = get_prior_invoice(
        PriorInvoiceQuery(invoice_number="INV-1004"), audit_store=audit,
    )
    assert r.found is False
    assert r.invoice_number == "INV-1004"
    assert r.prior_outcome is None


def test_get_prior_invoice_returns_seeded_row(audit) -> None:
    audit.record(RunHistoryRow(
        invoice_number="INV-1004", vendor_name="Precision Parts Ltd.",
        stated_total_usd=Decimal("1890.00"), semantic_hash="hash-of-original",
        source_file="data/invoices/invoice_1004.json", outcome="APPROVE",
        finished_at="2026-06-15T12:00:00Z",
    ))
    r = get_prior_invoice(
        PriorInvoiceQuery(invoice_number="INV-1004"), audit_store=audit,
    )
    assert r.found is True
    assert r.semantic_hash == "hash-of-original"
    assert r.stated_total_usd == Decimal("1890.00")
    assert r.source_file == "data/invoices/invoice_1004.json"
    assert r.prior_outcome == "APPROVE"


def test_get_prior_invoice_normalizes_bare_number(audit) -> None:
    """Bare `1004` normalizes to INV-1004 — same dedupe key logic."""
    r = get_prior_invoice(
        PriorInvoiceQuery(invoice_number="1004"), audit_store=audit,
    )
    assert r.invoice_number == "INV-1004"


def test_get_prior_invoice_no_digits_does_not_raise(audit) -> None:
    r = get_prior_invoice(
        PriorInvoiceQuery(invoice_number="no-digits-here"), audit_store=audit,
    )
    assert r.found is False


# ---------------------------------------------------------------------------
# get_policy
# ---------------------------------------------------------------------------

def test_get_policy_vn_004_returns_documented_rationale() -> None:
    r = get_policy(PolicyQuery(finding_code="VN-004"))
    assert r.found is True
    assert r.code == "VN-004"
    # Rationale describes the inactive-rename mechanism
    assert r.rationale and "inactive" in r.rationale.lower()
    # Detection column references vendor_claims — the mechanism the tool documents
    assert r.detection and "vendor_claims" in r.detection
    # INV-1012 is the flagship corpus example listed in the taxonomy
    assert "INV-1012" in r.corpus_examples


def test_get_policy_unknown_code_not_found() -> None:
    r = get_policy(PolicyQuery(finding_code="XX-999"))
    assert r.found is False
    assert r.trigger is None
    assert r.rationale is None
    assert r.corpus_examples == []


def test_get_policy_garbage_input_does_not_raise() -> None:
    r = get_policy(PolicyQuery(finding_code=""))
    assert r.found is False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_all_five_tools_registered() -> None:
    names = {t.name for t in TOOLS}
    assert names == {
        "get_vendor_record",
        "get_vendor_invoice_history",
        "get_item_reference",
        "get_prior_invoice",
        "get_policy",
    }


def test_tool_schemas_have_no_forbidden_constraints() -> None:
    """Phase 0 constraint: xAI structured outputs reject minLength/maxLength/
    minItems/maxItems/pattern anywhere in the schema."""
    import json
    forbidden = ("minLength", "maxLength", "minItems", "maxItems", "pattern")
    for tool in TOOLS:
        blob = json.dumps(tool.openai_schema())
        for keyword in forbidden:
            assert f'"{keyword}"' not in blob, (
                f"tool {tool.name!r} schema contains forbidden constraint "
                f"{keyword!r}"
            )


# ---------------------------------------------------------------------------
# The fact/judgment boundary — crude but locks against drift
# ---------------------------------------------------------------------------

# Words that would indicate the tool is asserting a verdict about the
# specific invoice under review. Whole-word match, case-insensitive.
# Deliberately narrow: descriptive uses (documentation excerpts containing
# these words) are handled by exempting get_policy from the scan since its
# whole purpose is to return doc text.
_FORBIDDEN_JUDGMENT_TERMS = [
    "suspicious",
    "risky",
    "should approve",
    "should reject",
    "should escalate",
    "recommend",
]


def _scan_for_judgment(payload: dict, tool_name: str) -> list[str]:
    import re
    offenders: list[str] = []
    def scan(obj) -> None:
        if isinstance(obj, str):
            for term in _FORBIDDEN_JUDGMENT_TERMS:
                if re.search(rf"\b{re.escape(term)}\b", obj, re.IGNORECASE):
                    offenders.append(f"{tool_name}: {term!r} in {obj[:80]!r}")
        elif isinstance(obj, dict):
            for v in obj.values():
                scan(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                scan(v)
    scan(payload)
    return offenders


def test_tool_outputs_contain_no_judgment_terms(audit) -> None:
    """Scans representative tool outputs. Exempts get_policy — its explicit
    contract is to return documented text and the taxonomy itself may
    reference judgment terms descriptively (e.g. 'approval process').
    """
    # Seed one history row for a realistic vendor history call
    audit.record(RunHistoryRow(
        invoice_number="INV-0500", vendor_name="Widgets Inc.",
        stated_total_usd=Decimal("100"), semantic_hash="a",
        source_file="x", outcome="APPROVE",
        finished_at="2026-01-01T00:00:00Z",
    ))
    outputs = [
        ("get_vendor_record",
         get_vendor_record(VendorRecordQuery(name="QuickShip Distributers"))),
        ("get_vendor_record",
         get_vendor_record(VendorRecordQuery(name="Acme Industrial Supplies"))),
        ("get_vendor_invoice_history",
         get_vendor_invoice_history(VendorHistoryQuery(name="Widgets Inc."), audit_store=audit)),
        ("get_item_reference", get_item_reference(ItemQuery(item="Widget A"))),
        ("get_item_reference", get_item_reference(ItemQuery(item="FakeItem"))),
        ("get_item_reference", get_item_reference(ItemQuery(item="WidgetC"))),
        ("get_prior_invoice",
         get_prior_invoice(PriorInvoiceQuery(invoice_number="INV-9999"), audit_store=audit)),
    ]
    all_offenders: list[str] = []
    for name, result in outputs:
        all_offenders.extend(_scan_for_judgment(result.model_dump(mode="json"), name))
    assert all_offenders == [], (
        "Tool outputs contained judgment terms:\n" + "\n".join(all_offenders)
    )
