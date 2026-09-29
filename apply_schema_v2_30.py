"""Apply the P1-4 current-P1-3c question-admission migration."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


MIGRATION = "v2.30-p14-current-p13c-question-admission-path"
REQUIRED_MIGRATIONS = (
    "v2.28-p13c-approval-boundary-and-mapping-revisions",
    "v2.29-p12f-append-only-controlled-content-source-revisions",
)


def apply(db_path: str | Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        if connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)
        ).fetchone():
            print(f"MIGRATION_ALREADY_APPLIED={MIGRATION}")
            return
        present = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations WHERE version IN (?, ?)",
                REQUIRED_MIGRATIONS,
            )
        }
        if present != set(REQUIRED_MIGRATIONS):
            raise RuntimeError("v2_28_and_v2_29_migrations_required_before_v2_30")
        connection.executescript(
            (Path(__file__).parent / "schema_v2_30.sql").read_text(encoding="utf-8")
        )
        connection.commit()
        print(f"MIGRATION_APPLIED={MIGRATION}")
    finally:
        connection.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply_schema_v2_30.py <database_path>")
    apply(sys.argv[1])
