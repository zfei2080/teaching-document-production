"""Apply the P1-2 textbook body snapshot chain migration to a target SQLite DB.

This helper is intentionally generic so tests can apply the migration to an
isolated temporary database. Callers must opt in with an explicit path.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SCHEMA_PATH = ROOT / "schema_v2_18.sql"
MIGRATION = "v2.18-textbook-body-snapshot-chain-2026-07-26"


def apply_schema(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database is missing: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone() is None:
            raise RuntimeError(f"Migration was not recorded: {MIGRATION}")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("USAGE=python apply_schema_v2_18.py <sqlite_db_path>", file=sys.stderr)
        return 2
    db_path = Path(args[0]).expanduser().resolve()
    apply_schema(db_path)
    print(f"APPLIED={MIGRATION}")
    print(f"DATABASE={db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
