"""Tests for the review-based delivery admission gate."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from review_gate import REQUIRED_REVIEW_TYPES, evaluate_question_admission, promote_if_eligible

ROOT = Path(__file__).parent


class ReviewGateTests(unittest.TestCase):
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

    def add_review(self, review_type, decision="approved", reviewer_role="assistant_precheck"):
        self.conn.execute(
            """INSERT INTO question_reviews
            (id, question_id, review_type, decision, reviewer, rationale, reviewer_role)
            VALUES (?, 'q1', ?, ?, 'tester', 'test evidence', ?)""",
            (f"{review_type}-{decision}-{reviewer_role}", review_type, decision, reviewer_role),
        )

    def test_missing_reviews_block_promotion(self):
        self.add_review("math_correctness")
        result = promote_if_eligible(self.conn, "q1")
        self.assertFalse(result.eligible)
        self.assertIn("teaching_fit", result.missing_reviews)
        state = self.conn.execute("SELECT quality_status, review_status FROM questions WHERE id='q1'").fetchone()
        self.assertEqual(state, ("needs_review", "pending"))

    def test_all_required_automated_approvals_allow_promotion(self):
        for review_type in REQUIRED_REVIEW_TYPES:
            self.add_review(review_type)
        result = promote_if_eligible(self.conn, "q1")
        self.assertTrue(result.eligible)
        state = self.conn.execute("SELECT quality_status, review_status FROM questions WHERE id='q1'").fetchone()
        self.assertEqual(state, ("approved", "approved"))

    def test_rejection_blocks_even_when_other_reviews_exist(self):
        for review_type in REQUIRED_REVIEW_TYPES:
            self.add_review(review_type)
        self.add_review("math_correctness", "rejected")
        result = evaluate_question_admission(self.conn, "q1")
        self.assertFalse(result.eligible)
        self.assertIn("math_correctness", result.blocking_reviews)


if __name__ == "__main__":
    unittest.main()
