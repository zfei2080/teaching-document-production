"""Apply the P1-4b current pedagogical-role evidence migration."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


MIGRATION = "v2.32-p14b-current-pedagogical-role-evidence"
REQUIRED_MIGRATIONS = (
    "v2.30-p14-current-p13c-question-admission-path",
    "v2.31-p14b-controlled-question-source-derivations",
)


def apply(db_path: str | Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone():
            print(f"MIGRATION_ALREADY_APPLIED={MIGRATION}")
            return
        present = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations WHERE version IN (?, ?)", REQUIRED_MIGRATIONS
            )
        }
        if present != set(REQUIRED_MIGRATIONS):
            raise RuntimeError("v2_30_and_v2_31_migrations_required_before_v2_32")
        connection.executescript((Path(__file__).parent / "schema_v2_32.sql").read_text(encoding="utf-8"))
        connection.commit()
        print(f"MIGRATION_APPLIED={MIGRATION}")
    finally:
        connection.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply_schema_v2_32.py <database_path>")
    apply(sys.argv[1])
