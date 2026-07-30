"""Per-validator tests. Each validator gets a positive case (trigger fires)
and a negative case (validator does NOT fire on a legitimate example).

The negative tests matter more than the positive ones — a validator that
flags everything is worse than useless."""
from decimal import Decimal
from pathlib import Path

import pytest

from src.adapters.router import extract
from src.schema import Money, Severity
from src.validators import Reference, has_critical, run_validators
from src.validators.duplicates import find

INVOICES = Path("data/invoices")


@pytest.fixture(scope="module")
def reference() -> Reference:
    from src.store.seed import seed
    seed()  # idempotent
    return Reference()


def _findings_for(name: str, reference: Reference):
    inv = extract(INVOICES / name).invoice
    return inv, run_validators(inv, reference)


def _codes(findings) -> list[str]:
    return [f.code for f in findings]


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

def test_ar_004_fires_on_inv_1013_grand_total_off_by_50(reference):
    _, findings = _findings_for("invoice_1013.json", reference)
    ar_004 = [f for f in findings if f.code == "AR-004"]
    assert len(ar_004) == 1
    assert "50" in ar_004[0].evidence or "50" in ar_004[0].message


def test_ar_002_fires_on_inv_1009_subtotal_mismatch(reference):
    _, findings = _findings_for("invoice_1009.json", reference)
    assert "AR-002" in _codes(findings)


def test_ar_005_and_ar_006_fire_on_inv_1009_negatives(reference):
    _, findings = _findings_for("invoice_1009.json", reference)
    assert "AR-005" in _codes(findings)
    assert "AR-006" in _codes(findings)


def test_ar_004_does_not_fire_on_clean_inv_1001(reference):
    _, findings = _findings_for("invoice_1001.txt", reference)
    assert "AR-004" not in _codes(findings)


def test_ar_004_does_not_fire_on_inv_1010_because_shipping_is_included(reference):
    """INV-1010 has a $150 shipping line outside the line items. AR-004
    MUST include additional_charges or it produces a false finding here."""
    _, findings = _findings_for("invoice_1010.txt", reference)
    assert "AR-004" not in _codes(findings), (
        "AR-004 fired on INV-1010 — additional_charges must be included in "
        f"the grand-total sum. Findings: {_codes(findings)}"
    )


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def test_in_003_aggregates_per_canonical_item_on_inv_1013(reference):
    """The proof case. Every per-line quantity passes; the sum does not."""
    _, findings = _findings_for("invoice_1013.json", reference)
    in_003 = [f for f in findings if f.code == "IN-003"]
    items_flagged = {f.evidence for f in in_003}
    # Three canonical items exceeded: WidgetA 22/15, WidgetB 18/10, GadgetX 9/5
    assert len(in_003) == 3
    assert any("22" in e and "15" in e for e in items_flagged)
    assert any("18" in e and "10" in e for e in items_flagged)
    assert any("9" in e and "5" in e for e in items_flagged)


def test_in_003_does_not_fire_on_inv_1010_which_splits_widget_a(reference):
    """INV-1010: WidgetA at 8 + 4 = 12, within stock=15."""
    _, findings = _findings_for("invoice_1010.txt", reference)
    assert "IN-003" not in _codes(findings)


def test_in_001_fires_on_unknown_item_inv_1016(reference):
    _, findings = _findings_for("invoice_1016.json", reference)
    assert "IN-001" in _codes(findings)


def test_in_002_fires_on_fakeitem_inv_1003(reference):
    _, findings = _findings_for("invoice_1003.txt", reference)
    assert "IN-002" in _codes(findings)


# ---------------------------------------------------------------------------
# Pricing — the sign-flip test on INV-1014
# ---------------------------------------------------------------------------

def test_pr_001_fires_on_inv_1014_widget_b_post_fx(reference):
    """WidgetB €475 -> $541.50 is 8.3% OVER $500 contract. Compared native
    it looks like a discount. Getting the sign wrong here is THE failure mode."""
    _, findings = _findings_for("invoice_1014.xml", reference)
    pr_001 = [f for f in findings if f.code == "PR-001"]
    assert len(pr_001) >= 1
    # Should mention WidgetB and being over
    assert any("WidgetB" in f.message and "exceeds" in f.message for f in pr_001)
    # PR-002 (below reference) must NOT fire — that would mean we compared native
    assert "PR-002" not in _codes(findings), (
        "PR-002 fired on INV-1014 — comparison was done in native EUR "
        "instead of USD, and the sign of the finding is inverted"
    )


def test_pr_003_intra_invoice_inconsistency_on_inv_1010(reference):
    """WidgetA at $250 AND $300 (rush-order line)."""
    _, findings = _findings_for("invoice_1010.txt", reference)
    assert "PR-003" in _codes(findings)


def test_pricing_clean_invoice_no_findings(reference):
    _, findings = _findings_for("invoice_1001.txt", reference)
    pricing_codes = [c for c in _codes(findings) if c.startswith("PR-")]
    assert pricing_codes == []


# ---------------------------------------------------------------------------
# Vendor
# ---------------------------------------------------------------------------

def test_vn_002_and_vn_004_fire_on_inv_1012_flagship(reference):
    _, findings = _findings_for("invoice_1012.txt", reference)
    codes = _codes(findings)
    assert "VN-001" in codes
    assert "VN-002" in codes
    assert "VN-004" in codes
    vn_002 = next(f for f in findings if f.code == "VN-002")
    assert "FastShip" in vn_002.message


def test_vn_002_does_not_fire_on_acme_industrial_inv_1006(reference):
    """The false-positive calibration test. Acme Industrial Supplies is a
    legitimate vendor sharing a token with the buyer (Acme Corp); it must
    not trip VN-002."""
    _, findings = _findings_for("invoice_1006.csv", reference)
    assert "VN-002" not in _codes(findings), (
        f"VN-002 fired on INV-1006 — fuzzy threshold is too loose. "
        f"Findings: {_codes(findings)}"
    )
    assert "VN-001" not in _codes(findings)


def test_vn_005_fires_on_inv_1009_empty_vendor(reference):
    _, findings = _findings_for("invoice_1009.json", reference)
    assert "VN-005" in _codes(findings)


def test_vn_003_fires_on_inv_1008_unknown_vendor_with_email(reference):
    _, findings = _findings_for("invoice_1008.txt", reference)
    assert "VN-003" in _codes(findings)


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------

def test_tm_003_fires_on_inv_1003_yesterday_due_date(reference):
    _, findings = _findings_for("invoice_1003.txt", reference)
    assert "TM-003" in _codes(findings)


def test_tm_003_fires_on_inv_1002_due_date_on_invoice_date(reference):
    _, findings = _findings_for("invoice_1002.txt", reference)
    assert "TM-003" in _codes(findings)


def test_tm_001_tolerance_allows_inv_1001_net_15_with_17_day_gap(reference):
    """Net 15 with a 17-day gap is 2 days off — exactly at tolerance."""
    _, findings = _findings_for("invoice_1001.txt", reference)
    assert "TM-001" not in _codes(findings)
    assert "TM-003" not in _codes(findings)


# ---------------------------------------------------------------------------
# Duplicates — batch-scoped
# ---------------------------------------------------------------------------

def test_duplicates_dp_001_on_inv_1011_txt_pdf_pair(reference):
    inv_txt = extract(INVOICES / "invoice_1011.txt").invoice
    inv_pdf = extract(INVOICES / "invoice_1011.pdf").invoice
    pairs = find([inv_txt, inv_pdf])
    codes = [f.code for _, f in pairs]
    assert codes.count("DP-001") == 2, (
        f"Expected DP-001 on both files; got: {codes}"
    )
    assert "DP-002" not in codes
    # All DP-001 findings are INFO — the deduplicator working, not an exception
    assert all(f.severity == Severity.INFO for _, f in pairs if f.code == "DP-001")


def test_dp_001_message_records_retained_file(reference):
    """The audit trail must show which file was kept and why."""
    inv_txt = extract(INVOICES / "invoice_1011.txt").invoice
    inv_pdf = extract(INVOICES / "invoice_1011.pdf").invoice
    pairs = find([inv_txt, inv_pdf])
    kept_msgs = [f.message for _, f in pairs if "RETAINED" in f.message]
    dropped_msgs = [f.message for _, f in pairs if "dropped" in f.message]
    assert len(kept_msgs) == 1, "exactly one file must be RETAINED"
    assert len(dropped_msgs) == 1, "exactly one file must be dropped"


def test_inv_1011_has_no_finding_above_info(reference):
    """INV-1011 is the clean duplicate-pair case. Its ONLY finding is DP-001,
    which is INFO. The invoice must not surface as an exception."""
    all_invoices = [
        extract(INVOICES / "invoice_1011.txt").invoice,
        extract(INVOICES / "invoice_1011.pdf").invoice,
    ]
    # Per-invoice validators + duplicate pass
    all_findings = []
    for inv in all_invoices:
        all_findings.extend(run_validators(inv, reference))
    for _inv, f in find(all_invoices):
        all_findings.append(f)

    above_info = [
        f for f in all_findings
        if f.severity not in (Severity.INFO,)
    ]
    assert above_info == [], (
        f"INV-1011 must produce no finding above INFO. Got: "
        f"{[(f.code, f.severity.value, f.message) for f in above_info]}"
    )


def test_duplicates_dp_002_and_dp_003_on_inv_1004_pair(reference):
    inv_orig = extract(INVOICES / "invoice_1004.json").invoice
    inv_rev = extract(INVOICES / "invoice_1004_revised.json").invoice
    pairs = find([inv_orig, inv_rev])
    codes = [f.code for _, f in pairs]
    assert codes.count("DP-002") == 2
    # DP-003 fires only on the file carrying the revision marker
    assert codes.count("DP-003") == 1


def test_duplicates_do_not_fire_on_singleton(reference):
    inv = extract(INVOICES / "invoice_1001.txt").invoice
    assert find([inv]) == []


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def test_po_001_fires_over_threshold(reference):
    _, findings = _findings_for("invoice_1003.txt", reference)   # $100k
    assert "PO-001" in _codes(findings)


def test_po_002_fires_near_threshold_inv_1012_and_inv_1008(reference):
    _, f_1012 = _findings_for("invoice_1012.txt", reference)
    _, f_1008 = _findings_for("invoice_1008.txt", reference)
    assert "PO-002" in _codes(f_1012)
    assert "PO-002" in _codes(f_1008)


def test_po_003_fires_on_inv_1014_eur(reference):
    _, findings = _findings_for("invoice_1014.xml", reference)
    assert "PO-003" in _codes(findings)


def test_policy_no_findings_on_low_total_invoice(reference):
    """INV-1004 ($1,890) is well below threshold and below the band."""
    _, findings = _findings_for("invoice_1004.json", reference)
    po_codes = [c for c in _codes(findings) if c.startswith("PO-")]
    assert po_codes == []


# ---------------------------------------------------------------------------
# Signals — reads raw source file
# ---------------------------------------------------------------------------

def test_fr_001_and_fr_002_fire_on_inv_1003(reference):
    _, findings = _findings_for("invoice_1003.txt", reference)
    assert "FR-001" in _codes(findings)
    assert "FR-002" in _codes(findings)


def test_fr_003_fires_on_inv_1005_white_house(reference):
    _, findings = _findings_for("invoice_1005.json", reference)
    assert "FR-003" in _codes(findings)


def test_signals_do_not_fire_on_inv_1004_evergreen_terrace(reference):
    """742 Evergreen Terrace (Simpsons) is NOT on the suspicious-address
    list, deliberately. FR-003 must not fire here."""
    _, findings = _findings_for("invoice_1004.json", reference)
    assert "FR-003" not in _codes(findings)


# ---------------------------------------------------------------------------
# The §2.2 hard-guardrail predicate
# ---------------------------------------------------------------------------

def test_has_critical_on_inv_1009(reference):
    _, findings = _findings_for("invoice_1009.json", reference)
    assert has_critical(findings)


def test_has_critical_false_on_clean_invoice(reference):
    _, findings = _findings_for("invoice_1001.txt", reference)
    assert not has_critical(findings)


# ---------------------------------------------------------------------------
# Extraction — EX-001 re-emitted from Invoice.corrections
# ---------------------------------------------------------------------------

def test_ex_001_fires_for_each_declared_correction_on_inv_1012(reference):
    _, findings = _findings_for("invoice_1012.txt", reference)
    ex = [f for f in findings if f.code == "EX-001"]
    assert len(ex) == 2  # both OCR fixes
    assert all(f.severity == Severity.INFO for f in ex)
