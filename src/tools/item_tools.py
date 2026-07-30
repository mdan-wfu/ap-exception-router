"""Item reference lookup.

Canonicalizes the input first (`Widget A` → `WidgetA`), then hits the
inventory table. Items do NOT fuzzy match — a Phase 1 decision. If the
canonical name is not in the catalog, returns an explicit not-found
result; the tool never guesses.
"""
from __future__ import annotations

from src.store.canonical import canonicalize_item
from src.tools.models import ItemQuery, ItemReferenceResult
from src.validators.reference import Reference


_reference: Reference | None = None


def _ref() -> Reference:
    global _reference
    if _reference is None:
        _reference = Reference()
    return _reference


def get_item_reference(query: ItemQuery) -> ItemReferenceResult:
    canonical, _ops = canonicalize_item(query.item)

    if canonical is None:
        return ItemReferenceResult(
            query=query.item,
            found=False,
            canonical_name=None,
            stock=None,
            reference_unit_price=None,
            category=None,
            active=None,
        )

    record = _ref().find_inventory(canonical)
    if record is None:
        # Canonicalized to a known name but the inventory row is absent.
        # Treat as not found; the tool's contract is "reference row present".
        return ItemReferenceResult(
            query=query.item,
            found=False,
            canonical_name=canonical,
            stock=None,
            reference_unit_price=None,
            category=None,
            active=None,
        )

    return ItemReferenceResult(
        query=query.item,
        found=True,
        canonical_name=canonical,
        stock=record.stock,
        reference_unit_price=record.reference_unit_price,
        category=record.category,
        active=record.active,
    )
