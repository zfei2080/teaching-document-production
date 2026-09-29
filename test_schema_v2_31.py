"""P1-4b isolated controlled-question-source derivation coverage."""
from __future__ import annotations

import copy
import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from apply_schema_v2_31 import MIGRATION, apply
from controlled_question_source_derivation import (
    ControlledQuestionSourceDerivationError,
    import_discovery,
)
from p1_4b_controlled_source_discovery import build_p1_4b_source_discovery


ROOT = Path(__file__).parent
P1_2F_BASELINE_DATABASE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)


class SchemaV231Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "development-copy.db"
        shutil.copy2(P1_2F_BASELINE_DATABASE, self.database)
        apply(self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_migration_is_idempotent(self) -> None:
        apply(self.database)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone()[0],
                1,
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='view' AND name='current_controlled_question_source_derivations'"
                ).fetchone()
            )
        finally:
            connection.close()

    def test_discovery_import_creates_internal_evidence_without_creating_a_question(self) -> None:
        discovery = build_p1_4b_source_discovery(self.database)
        connection = self._connection()
        try:
            before_questions = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            before_delivery = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("teaching_documents", "document_questions", "quality_reports", "question_usage")
            }
            result = import_discovery(connection, discovery)
            self.assertEqual(result["status"], "validated_internal_evidence_only")
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM current_controlled_question_answer_evidence").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM current_controlled_question_source_derivations").fetchone()[0],
                1,
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0], before_questions)
            self.assertEqual(
                {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in before_delivery
                },
                before_delivery,
            )
            identities = {
                tuple(row)
                for row in connection.execute(
                    "SELECT content_type, layer FROM current_controlled_content_segments"
                )
            }
            self.assertEqual(
                identities,
                {("knowledge_explanation", "public_core"), ("consolidation_practice", "basic_reinforcement")},
            )
            replay = import_discovery(connection, discovery)
            self.assertEqual(replay["status"], "reused")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "UPDATE controlled_question_answer_evidence SET field_name='analysis' WHERE field_name='answer'"
                )
        finally:
            connection.close()

    def test_invalid_math_evidence_blocks_before_any_write(self) -> None:
        discovery = build_p1_4b_source_discovery(self.database)
        invalid = copy.deepcopy(discovery)
        invalid["candidate"]["math"]["computed_answer"] = "A"
        before = self._hash(self.database)
        connection = self._connection()
        try:
            with self.assertRaisesRegex(ControlledQuestionSourceDerivationError, "math"):
                import_discovery(connection, invalid)
        finally:
            connection.close()
        self.assertEqual(before, self._hash(self.database))

    def test_tampered_candidate_fields_block_before_any_write(self) -> None:
        discovery = build_p1_4b_source_discovery(self.database)
        cases = (
            ("stem", "题干已经被替换", "candidate_prompt_not_bound_to_source_profile"),
            ("analysis", "解析已经被替换", "candidate_analysis_not_bound_to_source_profile"),
        )
        for field, value, error in cases:
            with self.subTest(field=field):
                invalid = copy.deepcopy(discovery)
                invalid["candidate"][field] = value
                before = self._hash(self.database)
                connection = self._connection()
                try:
                    with self.assertRaisesRegex(ControlledQuestionSourceDerivationError, error):
                        import_discovery(connection, invalid)
                finally:
                    connection.close()
                self.assertEqual(before, self._hash(self.database))

    def test_tampered_answer_range_blocks_before_any_write(self) -> None:
        discovery = build_p1_4b_source_discovery(self.database)
        invalid = copy.deepcopy(discovery)
        invalid["controlled_source"]["profile_ranges"]["answer"] = [0, 1]
        before = self._hash(self.database)
        connection = self._connection()
        try:
            with self.assertRaisesRegex(ControlledQuestionSourceDerivationError, "answer_range_overlaps_student_segment"):
                import_discovery(connection, invalid)
        finally:
            connection.close()
        self.assertEqual(before, self._hash(self.database))


if __name__ == "__main__":
    unittest.main()
