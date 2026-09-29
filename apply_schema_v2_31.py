"""Apply the P1-4b controlled question-source derivation migration."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


MIGRATION = "v2.31-p14b-controlled-question-source-derivations"
REQUIRED_MIGRATION = "v2.29-p12f-append-only-controlled-content-source-revisions"


def apply(db_path: str | Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone():
            print(f"MIGRATION_ALREADY_APPLIED={MIGRATION}")
            return
        if connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (REQUIRED_MIGRATION,)
        ).fetchone() is None:
            raise RuntimeError("v2_29_migration_required_before_v2_31")
        connection.executescript((Path(__file__).parent / "schema_v2_31.sql").read_text(encoding="utf-8"))
        connection.commit()
        print(f"MIGRATION_APPLIED={MIGRATION}")
    finally:
        connection.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply_schema_v2_31.py <database_path>")
    apply(sys.argv[1])
