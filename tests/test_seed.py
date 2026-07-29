"""Seed script produces reference.db with the shape the validators depend on.

Verifies:
  - both tables populate
  - FastShip Ltd. is present with status 'inactive' (INV-1012 depends on this)
  - the four deliberately-absent vendors are absent (VN-001 has teeth)
  - canonical.KNOWN_ITEMS matches the inventory table exactly
"""
import sqlite3
from pathlib import Path

import pytest

from src.store.canonical import KNOWN_ITEMS
from src.store.seed import seed


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    db = tmp_path / "reference.db"
    seed(db_path=db)
    return db


def test_seed_populates_inventory(seeded_db: Path) -> None:
    conn = sqlite3.connect(str(seeded_db))
    rows = conn.execute("SELECT item FROM inventory").fetchall()
    items = {r[0] for r in rows}
    assert items == {"WidgetA", "WidgetB", "GadgetX", "FakeItem"}


def test_seed_populates_vendors(seeded_db: Path) -> None:
    conn = sqlite3.connect(str(seeded_db))
    n = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    assert n == 11


def test_fastship_present_and_inactive(seeded_db: Path) -> None:
    """FastShip Ltd. is the key to INV-1012's genuine ambiguity."""
    conn = sqlite3.connect(str(seeded_db))
    row = conn.execute(
        "SELECT status FROM vendors WHERE name = 'FastShip Ltd.'"
    ).fetchone()
    assert row is not None, "FastShip Ltd. must be in the vendor master"
    assert row[0] == "inactive"


def test_deliberately_absent_vendors(seeded_db: Path) -> None:
    """These four corpus vendors are absent so VN-001 has teeth."""
    conn = sqlite3.connect(str(seeded_db))
    for name in [
        "Fraudster LLC",
        "NoProd Industries",
        "Global Supply Chain Partners",
        "QuickShip Distributers",
    ]:
        row = conn.execute(
            "SELECT name FROM vendors WHERE name = ?", (name,)
        ).fetchone()
        assert row is None, f"{name} must NOT be in the vendor master"


def test_inventory_catalog_matches_canonical_constant(seeded_db: Path) -> None:
    """canonical.KNOWN_ITEMS must be exactly the inventory table.

    If seed.sql adds an item, KNOWN_ITEMS in canonical.py must be updated,
    or canonicalize_item will silently return None for it.
    """
    conn = sqlite3.connect(str(seeded_db))
    rows = conn.execute("SELECT item FROM inventory").fetchall()
    items = frozenset(r[0] for r in rows)
    assert items == KNOWN_ITEMS


def test_seed_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "reference.db"
    seed(db_path=db)
    counts_first = seed(db_path=db)
    counts_second = seed(db_path=db)
    assert counts_first == counts_second


def test_fakeitem_present_but_inactive(seeded_db: Path) -> None:
    """FakeItem: known item, zero stock, inactive — distinguishes IN-002 from IN-001."""
    conn = sqlite3.connect(str(seeded_db))
    row = conn.execute(
        "SELECT stock, active FROM inventory WHERE item = 'FakeItem'"
    ).fetchone()
    assert row is not None
    assert row[0] == 0
    assert row[1] == 0
