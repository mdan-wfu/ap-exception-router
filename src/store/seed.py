"""Rebuild `reference.db` from `reference_seed.sql`.

`make seed` calls this. Idempotent: drops the DB file if it exists and
recreates from scratch. Preserves the SQL file as the single source of
truth for reference data — every comment in that file is provenance and
must not be lost.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQL_FILE = REPO_ROOT / "reference_seed.sql"
DEFAULT_DB_PATH = REPO_ROOT / "reference.db"


def seed(
    db_path: Path = DEFAULT_DB_PATH,
    sql_path: Path = DEFAULT_SQL_FILE,
) -> dict[str, int]:
    """Drop and rebuild the reference DB. Returns row counts per table."""
    if db_path.exists():
        db_path.unlink()

    sql = sql_path.read_text()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(sql)
        conn.commit()
        counts = {
            "inventory": conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0],
            "vendors": conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0],
        }
    finally:
        conn.close()
    return counts


if __name__ == "__main__":
    counts = seed()
    print(f"Seeded {DEFAULT_DB_PATH.relative_to(REPO_ROOT)}:")
    for table, n in counts.items():
        print(f"  {table}: {n} rows")
