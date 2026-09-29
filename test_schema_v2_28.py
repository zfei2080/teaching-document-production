"""Schema-level safeguards for the P1-3c approval-boundary migration."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from apply_schema_v2_28 import MIGRATION, apply


ROOT = Path(__file__).parent
LIVE_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"


class SchemaV228Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "p1-3c.db"
        shutil.copy2(LIVE_DATABASE, self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_migration_is_idempotent_and_preserves_legacy_question_state(self) -> None:
        before = sqlite3.connect(self.database)
        try:
            legacy = before.execute(
                """SELECT fit_status FROM question_textbooks
                     WHERE question_id='golden-q013'"""
            ).fetchone()[0]
        finally:
            before.close()
        apply(self.database)
        apply(self.database)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT fit_status FROM question_textbooks WHERE question_id='golden-q013'"
                ).fetchone()[0],
                legacy,
            )
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()

    def test_append_only_and_content_contract_guards_are_installed(self) -> None:
        apply(self.database)
        connection = sqlite3.connect(self.database)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "mapping revision heads are created"):
                connection.execute(
                    """INSERT INTO question_mapping_source_revision_heads
                       (question_id, textbook_id, curriculum_node_id, current_revision_id, head_revision)
                       VALUES ('golden-q013', 'bsd-math-grade8-lower-2026-spring-extsrc',
                               'bsd-math-8x-2026-node-01-section-02', 'forged', 1)"""
                )
            source_id = connection.execute(
                "SELECT id FROM controlled_content_sources ORDER BY id LIMIT 1"
            ).fetchone()[0]
            with self.assertRaisesRegex(sqlite3.IntegrityError, "controlled content sources are append-only"):
                connection.execute(
                    "UPDATE controlled_content_sources SET converted_sha256='x' WHERE id=?", (source_id,)
                )
            segment_id = connection.execute(
                "SELECT id FROM controlled_content_segments ORDER BY id LIMIT 1"
            ).fetchone()[0]
            with self.assertRaisesRegex(sqlite3.IntegrityError, "segment contracts are immutable"):
                connection.execute(
                    "UPDATE controlled_content_segments SET layer='challenge_extension' WHERE id=?", (segment_id,)
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
