"""Extraction findings — surface declared corrections in the audit trail.

The extractor prompt (prompts/extractor.md) instructs the LLM to declare
any repair in `Invoice.corrections`. This validator re-emits each as an
`EX-001` finding so it's visible alongside the other checks.
"""
from __future__ import annotations

from src.schema import Finding, Invoice, Severity
from src.validators.reference import Reference


def check(invoice: Invoice, reference: Reference) -> list[Finding]:  # noqa: ARG001
    return [
        Finding(
            code="EX-001",
            severity=Severity.INFO,
            message=f"Extractor repaired {c.field_path}: {c.original!r} -> {c.corrected!r}",
            evidence=c.reason,
            field_path=c.field_path,
        )
        for c in invoice.corrections
    ]
