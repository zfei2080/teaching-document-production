"""Regression checks for the isolated unified development schema."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
SCHEMA_PATH = PROJECT_ROOT / "schema_v2.sql"
PROVENANCE_SCHEMA_PATH = PROJECT_ROOT / "schema_v2_1.sql"
REVIEW_SCHEMA_PATH = PROJECT_ROOT / "schema_v2_2.sql"
TEACHER_REVIEW_SCHEMA_PATH = PROJECT_ROOT / "schema_v2_3.sql"
AUTOMATIC_VERIFICATION_SCHEMA_PATH = PROJECT_ROOT / "schema_v2_4.sql"
CONTROLLED_CATALOG_SCHEMA_PATH = PROJECT_ROOT / "schema_v2_5.sql"
CONTROLLED_IMPORT_SCHEMA_PATH = PROJECT_ROOT / "schema_v2_6.sql"


class SchemaV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.executescript(PROVENANCE_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.executescript(REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.executescript(TEACHER_REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.executescript(AUTOMATIC_VERIFICATION_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.executescript(CONTROLLED_CATALOG_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.executescript(CONTROLLED_IMPORT_SCHEMA_PATH.read_text(encoding="utf-8"))

    def tearDown(self):
        self.conn.close()
        self.db_path.unlink(missing_ok=True)

    def test_required_tables_and_initial_migration_exist(self):
        expected = {
            "textbooks", "curriculum_nodes", "knowledge_points", "source_documents",
            "source_fragments", "questions", "question_assets", "classes", "rule_sets",
            "production_requests", "selection_plans", "teaching_documents", "question_usage",
            "quality_reports", "question_source_fragments", "question_reviews", "question_verifications",
            "catalog_releases", "controlled_import_runs", "catalog_release_imports", "question_textbook_imports",
        }
        actual = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue(expected.issubset(actual))
        migrations = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        self.assertIn("v2-initial-2026-07-25", migrations)
        self.assertIn("v2.1-field-provenance-2026-07-25", migrations)
        self.assertIn("v2.2-question-review-audit-2026-07-25", migrations)
        self.assertIn("v2.3-teacher-final-review-2026-07-25", migrations)
        self.assertIn("v2.4-automatic-verification-2026-07-25", migrations)
        self.assertIn("v2.5-controlled-catalog-releases-2026-07-25", migrations)
        self.assertIn("v2.6-controlled-import-provenance-2026-07-26", migrations)

    def test_question_usage_requires_existing_class_question_and_document(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO question_usage
                (id, question_id, class_id, document_id, usage_type)
                VALUES ('usage-1', 'missing-question', 'missing-class', 'missing-doc', 'lecture')""")

    def test_delivered_flag_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO question_usage
                (id, question_id, class_id, document_id, usage_type, delivered)
                VALUES ('usage-2', 'question', 'class', 'document', 'lecture', 2)""")

    def test_question_difficulty_uses_confirmed_five_level_scale(self):
        self.conn.execute("""INSERT INTO source_documents
            (id, relative_path, file_hash, file_type)
            VALUES ('source-1', 'history/demo.docx', 'hash', 'docx')""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO questions
                (id, stem, question_type, difficulty, stage, source_document_id, content_hash)
                VALUES ('question-1', 'demo', '选择题', '未知', '初中', 'source-1', 'content-hash')""")

    def test_review_requires_existing_question(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO question_reviews
                (id, question_id, review_type, decision, reviewer, rationale)
                VALUES ('review-1', 'missing-question', 'math_correctness', 'approved', 'reviewer', 'evidence')""")

    def test_field_provenance_requires_existing_question_and_fragment(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO question_source_fragments
                (question_id, source_fragment_id, field_name, source_hash)
                VALUES ('missing-question', 'missing-fragment', 'answer', 'hash')""")


if __name__ == "__main__":
    unittest.main()
