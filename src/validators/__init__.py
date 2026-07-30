"""Deterministic validators.

Each per-invoice validator obeys:

    def check_x(invoice: Invoice, reference: Reference) -> list[Finding]:

Pure, no I/O beyond the injected reference, no mutation. Returns empty list
when nothing is wrong.

Validators NEVER decide. They report evidence with a severity; whether that
kills the invoice is the Adjudicator's call in Phase 5.
"""
from src.validators.registry import (
    find_duplicates,
    has_critical,
    run_validators,
)
from src.validators.reference import InventoryItem, Reference, VendorRecord

__all__ = [
    "InventoryItem",
    "Reference",
    "VendorRecord",
    "find_duplicates",
    "has_critical",
    "run_validators",
]
