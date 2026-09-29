"""Apply complete source-inventory support for database enrichment."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

MIGRATION = "v2.35-database-enrichment-complete-source-inventory"
REQUIRED_MIGRATION = "v2.34-database-enrichment-change-ledger"


def apply(db_path: str | Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone():
            print(f"MIGRATION_ALREADY_APPLIED={MIGRATION}")
            return
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (REQUIRED_MIGRATION,)).fetchone() is None:
            raise RuntimeError("v2_34_migration_required_before_v2_35")
        connection.executescript((Path(__file__).parent / "schema_v2_35.sql").read_text(encoding="utf-8"))
        print(f"MIGRATION_APPLIED={MIGRATION}")
    finally:
        connection.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply_schema_v2_35.py <database_path>")
    apply(sys.argv[1])
