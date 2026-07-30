"""Shared helpers for deterministic adapters.

These helpers are intentionally boring: parse to canonical types, preserve
raw literals for anything unparseable, never compute or validate business
rules. Semantic correctness is Phase 4's job.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from src.schema import Money


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

# `26-Jan-2026` / `January 27, 2026`
_DAY_MONTHNAME_YEAR = re.compile(r"^(\d{1,2})[\s\-](\w+)[\s\-,]+(\d{4})$")
_MONTHNAME_DAY_YEAR = re.compile(r"^(\w+)\s+(\d{1,2})[\s,]+(\d{4})$")


def parse_date(raw: str | None) -> str | None:
    """Return an ISO-8601 YYYY-MM-DD string, or None if unparseable.

    Never invents a value. INV-1003's `yesterday` returns None; the caller
    is responsible for preserving the raw literal in the `_raw` field.

    Recognized shapes (extend as new corpus formats appear):
      2026-01-15
      01/28/2026        (US MM/DD/YYYY)
      26-Jan-2026
      January 27, 2026
    """
    if not raw:
        return None
    raw = raw.strip()
    if _ISO_DATE.match(raw):
        try:
            date.fromisoformat(raw)
            return raw
        except ValueError:
            return None
    m = _US_DATE.match(raw)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    m = _DAY_MONTHNAME_YEAR.match(raw)
    if m:
        day = int(m.group(1))
        month = _MONTHS.get(m.group(2).lower())
        year = int(m.group(3))
        if month:
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return None
    m = _MONTHNAME_DAY_YEAR.match(raw)
    if m:
        month = _MONTHS.get(m.group(1).lower())
        day = int(m.group(2))
        year = int(m.group(3))
        if month:
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return None
    return None


def money(amount: Any, currency: str = "USD") -> Money | None:
    """Return a Money instance, or None if the input is None/empty.

    Coerces int/float/str/Decimal to Decimal. Preserves whatever was given —
    negatives survive, zero survives.
    """
    if amount is None:
        return None
    if isinstance(amount, str):
        s = amount.strip()
        if not s:
            return None
        amount = s
    return Money(amount_native=Decimal(str(amount)), currency=currency)


