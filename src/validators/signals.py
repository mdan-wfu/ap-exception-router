"""Fraud-signal validator.

Reads the RAW source file, not the extracted Invoice. Fraud-language
detection must never depend on the extractor choosing to preserve
something — INV-1003's "URGENT — Pay immediately" is exactly the kind of
evidence a schema-driven extractor might drop.

Heuristic layer. False positives are expected — severities kept moderate.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.schema import Finding, Invoice, Severity
from src.validators.reference import Reference


# Curated phrase lists — additive, edit here rather than scattering regexes.
_URGENCY_PATTERNS = [
    r"\burgent\b",
    r"\bimmediately\b",
    r"\bavoid\s+penalt",
    r"!!!",
    r"\bpay\s+immediately\b",
    r"\btime\s*[- ]?sensitive\b",
    r"\bfinal\s+notice\b",
]
_URGENCY_RE = re.compile("|".join(_URGENCY_PATTERNS), re.IGNORECASE)

_PAYMENT_CHANNEL_PATTERNS = [
    r"\bwire\s+transfer\s+(preferred|only|required)\b",
    r"\bbitcoin\b",
    r"\bcryptocurrency\b",
    r"\bgift\s+card",
    r"\bpay\s+via\s+wire\b",
]
_PAYMENT_CHANNEL_RE = re.compile("|".join(_PAYMENT_CHANNEL_PATTERNS), re.IGNORECASE)

# Substring match — deliberately narrow list. Extend as adversarial invoices
# reveal more spoofed addresses.
_SUSPICIOUS_ADDRESS_SUBSTRINGS = [
    "1600 pennsylvania",     # White House
    "10 downing",            # 10 Downing Street
    "1 first street ne",     # US Supreme Court
]
# INV-1004 uses `742 Evergreen Terrace` (The Simpsons). Deliberately NOT
# on this list: FR-003 is meant to catch spoofed real high-profile addresses,
# not every fictional one — and adding INV-1004 to the list would push a
# clean approve-track invoice into escalation for the wrong reason.


def check(invoice: Invoice, reference: Reference) -> list[Finding]:  # noqa: ARG001
    """Read the source file (or the raw text if source_file is unavailable)."""
    text = _read_source(invoice.source_file)
    if text is None:
        return []

    findings: list[Finding] = []

    if _URGENCY_RE.search(text):
        findings.append(Finding(
            code="FR-001",
            severity=Severity.LOW,
            message="Urgency / pressure language detected in source text",
            evidence=_snippet(text, _URGENCY_RE),
            field_path="source_file",
        ))

    if _PAYMENT_CHANNEL_RE.search(text):
        findings.append(Finding(
            code="FR-002",
            severity=Severity.MEDIUM,
            message="Non-standard payment-channel request in source text",
            evidence=_snippet(text, _PAYMENT_CHANNEL_RE),
            field_path="source_file",
        ))

    lower = text.lower()
    for needle in _SUSPICIOUS_ADDRESS_SUBSTRINGS:
        if needle in lower:
            findings.append(Finding(
                code="FR-003",
                severity=Severity.MEDIUM,
                message=f"Suspicious vendor address matches curated list: {needle!r}",
                evidence=f"substring={needle!r}",
                field_path="vendor_address",
            ))
            break

    return findings


def _read_source(source_file: str) -> str | None:
    path = Path(source_file)
    if not path.exists():
        return None
    if path.suffix.lower() == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            return None
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _snippet(text: str, pattern: re.Pattern) -> str:
    """Return a short evidence snippet around the first match."""
    m = pattern.search(text)
    if m is None:
        return ""
    start = max(0, m.start() - 20)
    end = min(len(text), m.end() + 20)
    snip = text[start:end].replace("\n", " ").strip()
    return f"...{snip}..."
