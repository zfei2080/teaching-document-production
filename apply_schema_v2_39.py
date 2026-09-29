"""Repair the malformed v2.34 difficulty enumeration without guessing old values."""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

MIGRATION = "v2.39-repair-content-item-difficulty-enum"
REQUIRED_MIGRATION = "v2.38-content-item-mathematical-validation-contract"


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def apply(db_path: str | Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone():
            print(f"MIGRATION_ALREADY_APPLIED={MIGRATION}")
            return
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (REQUIRED_MIGRATION,)).fetchone() is None:
            raise RuntimeError("v2_38_migration_required_before_v2_39")
        existing_count = connection.execute("SELECT COUNT(*) FROM content_item_difficulty_evidence").fetchone()[0]
        if existing_count:
            raise RuntimeError("v2_39_refuses_ambiguous_legacy_difficulty_values")
        trigger_rows = connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='content_item_difficulty_evidence' ORDER BY name"
        ).fetchall()
        view_rows = connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='view' "
            "AND sql LIKE '%content_item_difficulty_evidence%' ORDER BY name"
        ).fetchall()
        for name, _ in view_rows:
            connection.execute("DROP VIEW " + _quoted(name))
        for name, _ in trigger_rows:
            connection.execute("DROP TRIGGER " + _quoted(name))
        connection.executescript((Path(__file__).parent / "schema_v2_39.sql").read_text(encoding="utf-8"))
        for _name, statement in trigger_rows:
            connection.execute(statement)
        for _name, statement in view_rows:
            connection.execute(statement)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"v2_39_foreign_key_check_failed:{violations!r}")
        print(f"MIGRATION_APPLIED={MIGRATION}")
    finally:
        connection.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply_schema_v2_39.py <database_path>")
    apply(sys.argv[1])
