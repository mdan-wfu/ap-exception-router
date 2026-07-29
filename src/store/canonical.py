"""Deterministic normalization for items, invoice numbers, and vendor strings.

None of these use the LLM. All are pure functions.
"""
from __future__ import annotations

import re

# Canonical item catalog. Must stay in sync with reference_seed.sql —
# enforced by tests/test_seed.py::test_inventory_catalog_matches_canonical_constant.
# `FakeItem` is intentionally present: it is a known item with stock 0 and
# active=0, which lets validators distinguish IN-002 (zero-stock) from IN-001
# (unknown item entirely).
KNOWN_ITEMS: frozenset[str] = frozenset({"WidgetA", "WidgetB", "GadgetX", "FakeItem"})

_KNOWN_ITEMS_LOOKUP: dict[str, str] = {item.lower(): item for item in KNOWN_ITEMS}

_PARENTHETICAL_TAIL = re.compile(r"\s*\([^)]*\)\s*$")
_ALL_PARENTHETICALS = re.compile(r"\s*\([^)]*\)\s*")
_WHITESPACE = re.compile(r"\s+")
_FORMERLY_CLAIM = re.compile(r"\(\s*(formerly\s+[^)]+)\)", re.IGNORECASE)
_TRAILING_DIGITS = re.compile(r"(\d+)\s*$")
_ANY_DIGITS = re.compile(r"(\d+)")


def canonicalize_item(raw: str) -> tuple[str | None, str]:
    """Return (canonical_name_or_None, normalization_applied).

    Rules, in order:
      1. Strip trailing parenthetical suffix — `WidgetA (rush order)` -> `WidgetA`
      2. Collapse all whitespace — `Widget A` -> `WidgetA`
      3. Case-fold for lookup only
      4. Exact match against KNOWN_ITEMS
      5. Return None if no exact match

    Fuzzy item matching is deliberately absent. Silent misroutes to the wrong
    SKU produce silently wrong stock checks. Unknown items surface as IN-001.
    """
    if raw is None or not str(raw).strip():
        return None, "empty input"

    ops: list[str] = []
    working = raw

    stripped = _PARENTHETICAL_TAIL.sub("", working)
    if stripped != working:
        ops.append("stripped parenthetical")
        working = stripped

    collapsed = _WHITESPACE.sub("", working)
    if collapsed != working:
        ops.append("collapsed whitespace")
        working = collapsed

    lookup_key = working.lower()
    canonical = _KNOWN_ITEMS_LOOKUP.get(lookup_key)
    if canonical is None:
        return None, "; ".join(ops + ["no match in catalog"])

    if canonical != working:
        ops.append("case-folded")

    return canonical, "; ".join(ops) if ops else "exact match"


def normalize_invoice_number(raw: str) -> str:
    """Normalize any invoice-number format to `INV-{digits}`.

    Examples:
        INV-1013   -> INV-1013
        INV 1012   -> INV-1012
        1002       -> INV-1002
        Inv #: 1002 -> INV-1002

    The `INV-1004` collision between `invoice_1004.json` and
    `invoice_1004_revised.json` is intended — it is the dedupe key for the
    flagship escalation case.

    Raises ValueError if no digit run is found.
    """
    if raw is None or not str(raw).strip():
        raise ValueError("Cannot normalize an empty invoice number")

    match = _TRAILING_DIGITS.search(raw) or _ANY_DIGITS.search(raw)
    if match is None:
        raise ValueError(f"No digit run found in invoice number: {raw!r}")

    return f"INV-{match.group(1)}"


def parse_vendor(raw: str) -> tuple[str, list[str]]:
    """Split a vendor string into a primary name and secondary claims.

    Example:
        'QuickShip Distributers (formerly FastShip Ltd.)'
        -> ('QuickShip Distributers', ['formerly FastShip Ltd.'])

    Claim text is preserved verbatim. Interpretation is the Adjudicator's job.
    """
    if not raw:
        return "", []

    claims: list[str] = [
        match.group(1).strip() for match in _FORMERLY_CLAIM.finditer(raw)
    ]

    primary = _ALL_PARENTHETICALS.sub(" ", raw).strip()
    primary = _WHITESPACE.sub(" ", primary)

    return primary, claims
