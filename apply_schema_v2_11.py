"""Apply controlled knowledge-mapping audit migration to the isolated development DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"
SCHEMA_PATH = ROOT / "schema_v2_11.sql"
MIGRATION = "v2.11-controlled-knowledge-mapping-audit-2026-07-26"


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Development database is missing: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone() is None:
            raise RuntimeError(f"Migration was not recorded: {MIGRATION}")
        print(f"APPLIED={MIGRATION}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
