"""Tests for automatic-only question admission."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from automatic_gate import REQUIRED_VERIFICATION_TYPES, evaluate_automatic_admission, promote_if_automatically_verified
from input_snapshot import refresh_current_snapshot

ROOT = Path(__file__).parent


class AutomaticGateTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in ("schema_v2.sql", "schema_v2_1.sql", "schema_v2_2.sql", "schema_v2_3.sql", "schema_v2_4.sql", "schema_v2_13.sql", "schema_v2_14.sql"):
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

    def add_verification(self, verification_type, status="pass"):
        input_hash = refresh_current_snapshot(self.conn, "q1")
        self.conn.execute(
            """INSERT INTO question_verifications
            (id, question_id, verification_type, validator_id, validator_version,
             status, computed_answer, evidence_json, input_hash)
            VALUES (?, 'q1', ?, 'test-validator', 'v1', ?, 'answer', ?, ?)""",
            (f"{verification_type}-{status}", verification_type, status, json.dumps({"test": True}), input_hash),
        )

    def test_missing_automatic_evidence_blocks_promotion(self):
        self.add_verification("source_fidelity")
        result = promote_if_automatically_verified(self.conn, "q1")
        self.assertFalse(result.eligible)
        self.assertIn("mathematical_independent", result.missing_or_nonpassing)
        state = self.conn.execute("SELECT quality_status FROM questions WHERE id='q1'").fetchone()[0]
        self.assertEqual(state, "blocked")

    def test_all_automatic_evidence_allows_promotion(self):
        for verification_type in REQUIRED_VERIFICATION_TYPES:
            self.add_verification(verification_type)
        result = promote_if_automatically_verified(self.conn, "q1")
        self.assertTrue(result.eligible)
        state = self.conn.execute("SELECT quality_status, review_status FROM questions WHERE id='q1'").fetchone()
        self.assertEqual(state, ("approved", "approved"))

    def test_unsupported_or_failed_verifier_blocks_promotion(self):
        for verification_type in REQUIRED_VERIFICATION_TYPES:
            self.add_verification(verification_type)
        self.add_verification("asset_semantics", "unsupported")
        result = evaluate_automatic_admission(self.conn, "q1")
        self.assertFalse(result.eligible)
        self.assertIn("asset_semantics", result.blockers)

    def test_nonpassing_evidence_automatically_isolates_previously_approved_question(self):
        for verification_type in REQUIRED_VERIFICATION_TYPES:
            self.add_verification(verification_type)
        self.conn.execute(
            "UPDATE questions SET quality_status='approved', review_status='approved' WHERE id='q1'"
        )
        # Approval inputs/evidence are write-locked until the candidate is
        # explicitly isolated; the later nonpassing run then keeps it blocked.
        self.conn.execute("UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id='q1'")
        self.add_verification("asset_semantics", "unsupported")

        result = promote_if_automatically_verified(self.conn, "q1")

        self.assertFalse(result.eligible)
        self.assertEqual(
            self.conn.execute(
                "SELECT quality_status, review_status FROM questions WHERE id='q1'"
            ).fetchone(),
            ("blocked", "pending"),
        )


if __name__ == "__main__":
    unittest.main()
