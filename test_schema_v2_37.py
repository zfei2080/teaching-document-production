"""Schema checks for optional source-question stage metadata."""
from __future__ import annotations

import sqlite3
import uuid

from apply_schema_v2_35 import apply as apply_v35
from apply_schema_v2_36 import apply as apply_v36
from apply_schema_v2_37 import MIGRATION, apply as apply_v37
import test_schema_v2_34 as base


class SchemaV237Tests(base.SchemaV234Tests):
    test_dependency_requires_exactly_one_target = None
    test_migration_is_idempotent_and_records_its_schema_event = None
    test_new_source_derived_records_require_change_ledger_links_and_stay_append_only = None

    def setUp(self) -> None:
        super().setUp()
        apply_v35(self.database)
        apply_v36(self.database)
        apply_v37(self.database)

    def test_stage_is_optional_and_existing_foreign_keys_and_triggers_survive(self) -> None:
        connection = self.connection()
        try:
            self.assertEqual(
                next(row[3] for row in connection.execute("PRAGMA table_info(questions)") if row[1] == "stage"),
                0,
            )
            ids = self.build_minimal_lineage(connection)
            question_id = f"no-stage-question:{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO questions(
                    id,stem,options_json,answer,analysis,question_type,difficulty,stage,grade_level,
                    source_document_id,source_fragment_id,source_question_no,source_page,content_hash,
                    extraction_status,quality_status,review_status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (question_id, "source question without static stage", "[]", None, None, "fill", None, None, None,
                 ids["source_document"], None, "2", None, base.digest(question_id), "structured", "pending", "pending"),
            )
            self.assertIsNone(connection.execute("SELECT stage FROM questions WHERE id=?", (question_id,)).fetchone()[0])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertGreater(
                connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='questions'").fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_migration_is_idempotent_and_records_schema_event(self) -> None:
        connection = self.connection()
        try:
            self.assertTrue(connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone())
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM content_change_ledger WHERE id=?", (f"schema:{MIGRATION}",)).fetchone()[0],
                1,
            )
        finally:
            connection.close()
        apply_v37(self.database)


if __name__ == "__main__":
    import unittest
    unittest.main()
