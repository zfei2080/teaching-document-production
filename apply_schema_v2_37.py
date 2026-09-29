"""Make legacy question stage metadata optional for new content-library records."""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

MIGRATION = "v2.37-question-stage-optional-for-content-library"
REQUIRED_MIGRATION = "v2.36-question-source-asset-evidence"


def apply(db_path: str | Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone():
            print(f"MIGRATION_ALREADY_APPLIED={MIGRATION}")
            return
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (REQUIRED_MIGRATION,)).fetchone() is None:
            raise RuntimeError("v2_36_migration_required_before_v2_37")
        question_indexes = [
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND tbl_name='questions' AND sql IS NOT NULL ORDER BY name"
            )
        ]
        # SQLite validates triggers on other tables that reference questions
        # while replacing the table.  Temporarily remove every such trigger,
        # then recreate its original SQL after the replacement.
        trigger_rows = connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
        ).fetchall()
        question_triggers = [row[1] for row in trigger_rows]
        # SQLite recompiles any existing view during a table rebuild. Preserve
        # all views, not merely direct references, because view dependencies can
        # be indirect.
        view_rows = connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='view' ORDER BY name"
        ).fetchall()
        question_views = [row[1] for row in view_rows]
        connection.execute("PRAGMA foreign_keys=OFF")
        for name, _statement in trigger_rows:
            connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        for name, _statement in view_rows:
            connection.execute('DROP VIEW "' + name.replace('"', '""') + '"')
        connection.executescript((Path(__file__).parent / "schema_v2_37.sql").read_text(encoding="utf-8"))
        for statement in question_indexes:
            connection.execute(statement)
        for statement in question_views:
            connection.execute(statement)
        for statement in question_triggers:
            connection.execute(statement)
        connection.execute("PRAGMA foreign_keys=ON")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"v2_37_foreign_key_check_failed:{violations!r}")
        print(f"MIGRATION_APPLIED={MIGRATION}")
    finally:
        connection.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply_schema_v2_37.py <database_path>")
    apply(sys.argv[1])
