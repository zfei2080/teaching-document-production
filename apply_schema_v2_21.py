"""Apply schema v2.21: controlled original/archive source binding.

Usage: python apply_schema_v2_21.py <db_path>
"""
import sqlite3
import sys
from pathlib import Path


MIGRATION = "v2.21-controlled-source-intake"


def apply(db_path: str | Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone():
            print(f"MIGRATION_ALREADY_APPLIED={MIGRATION}")
            return
        conn.executescript((Path(__file__).parent / "schema_v2_21.sql").read_text(encoding="utf-8"))
        conn.commit()
        print(f"MIGRATION_APPLIED={MIGRATION}")
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply_schema_v2_21.py <db_path>")
    apply(sys.argv[1])
