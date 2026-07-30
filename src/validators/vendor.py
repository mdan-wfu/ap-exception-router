"""Vendor validator — the only validator where fuzzy matching is permitted.

Fuzzy threshold tuned against the deliberate false-positive in the corpus:
`Acme Industrial Supplies` (INV-1006) shares the token `Acme` with the
buyer `Acme Corp`. Since INV-1006 has an exact master match, VN-002 doesn't
fire for it — but the threshold must also ensure a synthetic near-match
(e.g. a typo of a real vendor) trips while unrelated token overlap does not.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from src.schema import Finding, Invoice, Severity
from src.validators.reference import Reference, VendorRecord


# Tuned so `Acme Industrial Supplies` vs `Acme Corp` (0.42) does NOT trip,
# but `QuickShip Distributers` vs `QuickShip Distributors` (0.98) does. See
# DECISIONS.md for the false-positive calibration.
FUZZY_THRESHOLD = 0.70


def check(invoice: Invoice, reference: Reference) -> list[Finding]:
    findings: list[Finding] = []

    if not invoice.vendor_name:
        findings.append(Finding(
            code="VN-005",
            severity=Severity.CRITICAL,
            message="Vendor name is empty",
            evidence="vendor_name == ''",
            field_path="vendor_name",
        ))
        return findings  # nothing else to check without a name

    master_match = reference.find_vendor(invoice.vendor_name)

    # VN-001 / VN-002: not in master, plus fuzzy or claim-based candidate
    if master_match is None:
        findings.append(Finding(
            code="VN-001",
            severity=Severity.HIGH,
            message=f"Vendor {invoice.vendor_name!r} is not in the master",
            evidence=f"vendor_name={invoice.vendor_name!r}",
            field_path="vendor_name",
        ))
        # VN-003 (unknown vendor variant): if the invoice carries a vendor
        # email, the domain can't be verified — flag it. This is the signal
        # for INV-1008's `billing@noproduct.biz`, where the domain looks
        # unrelated to the vendor name but the vendor isn't in the master
        # to compare against.
        if invoice.vendor_email:
            findings.append(Finding(
                code="VN-003",
                severity=Severity.MEDIUM,
                message=(
                    f"Cannot verify email domain for unknown vendor: "
                    f"{invoice.vendor_email!r} claims {invoice.vendor_name!r}"
                ),
                evidence=(
                    f"vendor_email={invoice.vendor_email!r}, "
                    f"vendor_name={invoice.vendor_name!r}, in_master=False"
                ),
                field_path="vendor_email",
            ))
        candidate = _fuzzy_candidate(invoice, reference)
        if candidate is not None:
            reason, cand = candidate
            findings.append(Finding(
                code="VN-002",
                severity=Severity.MEDIUM,
                message=f"Fuzzy candidate in master: {cand.name!r} ({reason})",
                evidence=f"candidate={cand.name!r}, {reason}",
                field_path="vendor_name",
            ))
            # VN-004: if the candidate is inactive, that's the flagship signal
            if not cand.is_active:
                findings.append(Finding(
                    code="VN-004",
                    severity=Severity.HIGH,
                    message=(
                        f"Fuzzy candidate {cand.name!r} is an INACTIVE vendor — "
                        f"the vendor claims (or resembles) a dormant relationship"
                    ),
                    evidence=(
                        f"candidate={cand.name!r}, status={cand.status}, {reason}"
                    ),
                    field_path="vendor_claims",
                ))
    else:
        # VN-003 / VN-004 only meaningful when vendor IS in master
        findings.extend(_domain_mismatch(invoice, master_match))
        if not master_match.is_active:
            findings.append(Finding(
                code="VN-004",
                severity=Severity.HIGH,
                message=f"Vendor {master_match.name!r} is inactive in the master",
                evidence=f"status={master_match.status}",
                field_path="vendor_name",
            ))

    return findings


def _fuzzy_candidate(
    invoice: Invoice, reference: Reference
) -> tuple[str, VendorRecord] | None:
    """Return (reason, VendorRecord) for the best candidate, or None.

    Preference order:
      1. Any `vendor_claims` string that exactly names a master vendor
         (deterministic — no fuzz).
      2. Highest SequenceMatcher ratio on vendor_name above FUZZY_THRESHOLD.
    """
    # 1. Claim-based exact match (INV-1012's "(formerly FastShip Ltd.)")
    for claim in invoice.vendor_claims:
        for record in reference.vendors.values():
            if record.name.lower() in claim.lower():
                return (
                    f"vendor claim {claim!r} names master vendor {record.name!r}",
                    record,
                )

    # 2. Fuzzy match on vendor_name
    target = invoice.vendor_name.lower()
    best: tuple[float, VendorRecord] | None = None
    for record in reference.vendors.values():
        ratio = SequenceMatcher(None, target, record.name.lower()).ratio()
        if ratio >= FUZZY_THRESHOLD and (best is None or ratio > best[0]):
            best = (ratio, record)
    if best is not None:
        return (f"SequenceMatcher ratio {best[0]:.2f}", best[1])
    return None


def _domain_mismatch(invoice: Invoice, master: VendorRecord) -> list[Finding]:
    if not invoice.vendor_email or not master.domain:
        return []
    email_domain = invoice.vendor_email.split("@")[-1].strip().lower()
    if email_domain and email_domain != master.domain.lower():
        return [Finding(
            code="VN-003",
            severity=Severity.HIGH,
            message=(
                f"Vendor email domain {email_domain!r} does not match master "
                f"domain {master.domain!r} for {master.name!r}"
            ),
            evidence=f"email={invoice.vendor_email!r}, master_domain={master.domain!r}",
            field_path="vendor_email",
        )]
    return []
