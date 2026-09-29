"""Tests for schema v2.20: classifier_runs table and provenance columns."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
PY = sys.executable

BASE_SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in (
        "", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9",
        "_10", "_11", "_12", "_13", "_14", "_15",
    )
)

from apply_schema_v2_16 import apply_schema as apply_schema_v2_16
from apply_schema_v2_17 import apply_schema as apply_schema_v2_17
from apply_schema_v2_18 import apply_schema as apply_schema_v2_18
from apply_schema_v2_19 import apply_schema as apply_schema_v2_19
from apply_schema_v2_20 import apply as apply_schema_v2_20


def build_temp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    for schema in BASE_SCHEMAS:
        conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    for fn in (
        apply_schema_v2_16,
        apply_schema_v2_17,
        apply_schema_v2_18,
        apply_schema_v2_19,
    ):
        fn(Path(path))
    apply_schema_v2_20(path)
    return path


class TestSchemaV220(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db_path = build_temp_db()

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.db_path)
        except Exception:
            pass

    def _columns(self, table: str) -> list:
        with sqlite3.connect(self.db_path) as c:
            return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]

    def test_1_classifier_runs_table_exists(self):
        """classifier_runs table exists with all required columns."""
        cols = self._columns("classifier_runs")
        required = [
            "id", "run_kind", "model_id", "model_version",
            "confidence_threshold", "input_hash", "output_hash",
            "status", "importer_id", "importer_version", "created_at",
        ]
        for col in required:
            self.assertIn(col, cols, f"Missing column: {col}")

    def test_2_question_textbooks_provenance_columns(self):
        """question_textbooks has classifier_run_id, confidence, classification_method."""
        cols = self._columns("question_textbooks")
        for col in ("classifier_run_id", "confidence", "classification_method"):
            self.assertIn(col, cols, f"Missing column in question_textbooks: {col}")

    def test_3_question_knowledge_points_provenance_columns(self):
        """question_knowledge_points has classifier_run_id, confidence, classification_method."""
        cols = self._columns("question_knowledge_points")
        for col in ("classifier_run_id", "confidence", "classification_method"):
            self.assertIn(col, cols, f"Missing column in question_knowledge_points: {col}")

    def test_4_insert_classifier_run(self):
        """Can insert a valid classifier_run record."""
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                """INSERT INTO classifier_runs
                   (id, run_kind, model_id, model_version, input_hash,
                    output_hash, status, importer_version)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    "test-run-001", "textbook_mapping",
                    "gpt-5.6-luna", "v1",
                    "abc123def456", "def456abc123",
                    "pending", "v1",
                ),
            )
            row = c.execute(
                "SELECT id, run_kind, status FROM classifier_runs WHERE id=?",
                ("test-run-001",)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "test-run-001")
        self.assertEqual(row[1], "textbook_mapping")
        self.assertEqual(row[2], "pending")

    def test_5_apply_script_idempotent(self):
        """apply_schema_v2_20.py is idempotent — second run prints MIGRATION_ALREADY_APPLIED."""
        apply_script = str(ROOT / "apply_schema_v2_20.py")
        # Migration already applied by setUpClass; this run should say already applied
        r1 = subprocess.run(
            [PY, apply_script, self.db_path],
            capture_output=True, text=True,
        )
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertIn("MIGRATION_ALREADY_APPLIED=v2.20", r1.stdout)
        # Second run — still idempotent
        r2 = subprocess.run(
            [PY, apply_script, self.db_path],
            capture_output=True, text=True,
        )
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("MIGRATION_ALREADY_APPLIED=v2.20", r2.stdout)

    def test_6_insert_or_replace_cannot_bypass_question_mapping_audit(self):
        """The approval guard covers INSERT and SQLite's REPLACE insert path."""
        with sqlite3.connect(self.db_path) as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute(
                    "INSERT OR REPLACE INTO question_textbooks "
                    "(question_id, textbook_id, curriculum_node_id, fit_status) "
                    "VALUES ('missing-question', 'missing-book', 'missing-node', 'approved')"
                )

    def test_7_insert_cannot_approve_unattested_knowledge_point(self):
        with sqlite3.connect(self.db_path) as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute(
                    "INSERT INTO knowledge_points "
                    "(id, canonical_name, knowledge_type, stage_scope, version, review_status) "
                    "VALUES ('unattested-kp', '未审计知识点', 'concept', '初中', 'v1', 'approved')"
                )


if __name__ == "__main__":
    unittest.main()
