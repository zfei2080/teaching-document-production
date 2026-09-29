"""Apply the P1-3c approval-boundary and mapping-revision migration."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


MIGRATION = "v2.28-p13c-approval-boundary-and-mapping-revisions"


def apply(db_path: str | Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        if connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)
        ).fetchone():
            print(f"MIGRATION_ALREADY_APPLIED={MIGRATION}")
            return
        script = (Path(__file__).parent / "schema_v2_28.sql").read_text(encoding="utf-8")
        connection.executescript(script)
        connection.commit()
        print(f"MIGRATION_APPLIED={MIGRATION}")
    finally:
        connection.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply_schema_v2_28.py <database_path>")
    apply(sys.argv[1])
