"""Reference data loader — inventory and vendor master from reference.db.

Loaded once per run, passed into every per-invoice validator. Never mutated.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from src.store.seed import DEFAULT_DB_PATH


@dataclass(frozen=True)
class InventoryItem:
    item: str
    stock: int
    reference_unit_price: Decimal | None
    category: str
    active: bool


@dataclass(frozen=True)
class VendorRecord:
    name: str
    aliases: tuple[str, ...]
    domain: str
    status: str                    # "active" | "inactive"
    contracted_terms: str          # e.g. "Net 30"
    relationship_since: str        # ISO date

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class Reference:
    """Snapshot of the reference DB. Read-only, hashable by db path."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        conn = sqlite3.connect(str(db_path))
        try:
            inv_rows = conn.execute(
                "SELECT item, stock, reference_unit_price, category, active FROM inventory"
            ).fetchall()
            self.inventory: dict[str, InventoryItem] = {
                r[0]: InventoryItem(
                    item=r[0],
                    stock=r[1],
                    reference_unit_price=Decimal(str(r[2])) if r[2] is not None else None,
                    category=r[3],
                    active=bool(r[4]),
                )
                for r in inv_rows
            }
            vnd_rows = conn.execute(
                "SELECT name, aliases, domain, status, contracted_terms, "
                "relationship_since FROM vendors"
            ).fetchall()
            self.vendors: dict[str, VendorRecord] = {
                r[0]: VendorRecord(
                    name=r[0],
                    aliases=tuple(json.loads(r[1])) if r[1] else (),
                    domain=r[2],
                    status=r[3],
                    contracted_terms=r[4],
                    relationship_since=r[5],
                )
                for r in vnd_rows
            }
        finally:
            conn.close()

    # -- Convenience lookups ------------------------------------------------

    def find_vendor(self, name: str) -> VendorRecord | None:
        """Case-insensitive exact match on vendor name."""
        if not name:
            return None
        target = name.strip().lower()
        for record in self.vendors.values():
            if record.name.lower() == target:
                return record
        return None

    def find_inventory(self, canonical: str | None) -> InventoryItem | None:
        if canonical is None:
            return None
        return self.inventory.get(canonical)
