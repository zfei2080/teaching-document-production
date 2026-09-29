"""Tests for append-only review recording and strict promotion."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from review_gate import REQUIRED_REVIEW_TYPES
from review_workflow import record_review

ROOT = Path(__file__).parent


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in ("schema_v2.sql", "schema_v2_1.sql", "schema_v2_2.sql", "schema_v2_3.sql"):
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self.conn.execute(
            "INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('s1', 's.docx', 'hash', 'docx')"
        )
        self.conn.execute(
            """INSERT INTO questions
            (id, stem, question_type, stage, source_document_id, content_hash, quality_status, review_status)
            VALUES ('q1', '题干', '选择题', '初中', 's1', 'content', 'needs_review', 'pending')"""
        )

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_each_review_is_recorded_and_only_complete_set_promotes(self):
        for index, review_type in enumerate(sorted(REQUIRED_REVIEW_TYPES)):
            result = record_review(
                self.conn,
                question_id="q1",
                review_type=review_type,
                decision="approved",
                reviewer="reviewer-a",
                rationale=f"evidence {index}",
            )
        self.assertTrue(result.eligible)
        self.assertEqual(
            self.conn.execute("SELECT quality_status, review_status FROM questions WHERE id='q1'").fetchone(),
            ("approved", "approved"),
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_reviews WHERE question_id='q1'").fetchone()[0], 5)

    def test_invalid_reviewer_role_is_rejected(self):
        with self.assertRaises(ValueError):
            record_review(
                self.conn,
                question_id="q1",
                review_type="math_correctness",
                decision="approved",
                reviewer="reviewer-a",
                rationale="evidence",
                reviewer_role="unknown",
            )

    def test_empty_reviewer_or_rationale_is_rejected(self):
        with self.assertRaises(ValueError):
            record_review(
                self.conn,
                question_id="q1",
                review_type="math_correctness",
                decision="approved",
                reviewer="",
                rationale="evidence",
            )
        with self.assertRaises(ValueError):
            record_review(
                self.conn,
                question_id="q1",
                review_type="math_correctness",
                decision="approved",
                reviewer="reviewer-a",
                rationale="",
            )


if __name__ == "__main__":
    unittest.main()
