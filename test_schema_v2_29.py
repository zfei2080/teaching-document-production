"""Migration coverage for append-only controlled-content source revisions."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from apply_schema_v2_28 import apply as apply_v28
from apply_schema_v2_29 import MIGRATION, apply as apply_v29


ROOT = Path(__file__).parent
# This fixture is the preserved v2.28 state from immediately before v2.29.
# Migration tests must not derive their initial state from a live database that
# has already advanced beyond the migration being tested.
P1_2F_PRE_V29_DATABASE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f-20260730"
    / "teaching_docs_dev_before_18A8B0805DCFC4A3.db"
)


class SchemaV229Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "db.sqlite"
        shutil.copy2(P1_2F_PRE_V29_DATABASE, self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_migration_seeds_current_revisions_and_is_idempotent(self) -> None:
        apply_v28(self.database)
        apply_v29(self.database)
        apply_v29(self.database)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM controlled_content_source_revisions").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM controlled_content_source_revision_heads").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM current_controlled_content_segments").fetchone()[0],
                2,
            )
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()

    def test_current_content_source_rows_are_immutable_after_migration(self) -> None:
        apply_v28(self.database)
        apply_v29(self.database)
        connection = sqlite3.connect(self.database)
        try:
            source_id = connection.execute(
                "SELECT id FROM controlled_content_sources ORDER BY id LIMIT 1"
            ).fetchone()[0]
            with self.assertRaisesRegex(sqlite3.IntegrityError, "sources are append-only"):
                connection.execute(
                    "UPDATE controlled_content_sources SET converted_path='forged' WHERE id=?", (source_id,)
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "sources are append-only"):
                connection.execute("DELETE FROM controlled_content_sources WHERE id=?", (source_id,))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
