"""Apply the controlled-catalog schema migration to the isolated dev database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"
SCHEMA_PATH = ROOT / "schema_v2_5.sql"
MIGRATION = "v2.5-controlled-catalog-releases-2026-07-25"


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Development database is missing: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        row = conn.execute(
            "SELECT version FROM schema_migrations WHERE version=?", (MIGRATION,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Migration was not recorded: {MIGRATION}")
        print(f"APPLIED={row[0]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
