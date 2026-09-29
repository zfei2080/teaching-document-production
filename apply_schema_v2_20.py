"""Apply schema v2.20: classifier-runs-and-classification-provenance.

Usage: python apply_schema_v2_20.py <db_path>
"""
import sqlite3
import sys
from pathlib import Path


def apply(db_path: str) -> None:
    sql_path = Path(__file__).parent / "schema_v2_20.sql"
    conn = sqlite3.connect(db_path)
    try:
        already = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=?", ("v2.20",)
        ).fetchone()[0]
        if already:
            print("MIGRATION_ALREADY_APPLIED=v2.20")
            return
        conn.executescript(sql_path.read_text(encoding="utf-8"))
        conn.commit()
        print("MIGRATION_APPLIED=v2.20")
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python apply_schema_v2_20.py <db_path>", file=sys.stderr)
        sys.exit(1)
    apply(sys.argv[1])
