"""P0-2a database-level tests for the question approval state machine."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from automatic_gate import REQUIRED_VERIFICATION_TYPES, apply_automatic_admission
from input_snapshot import refresh_current_snapshot

ROOT = Path(__file__).parent
SCHEMAS = tuple(f"schema_v2{suffix}.sql" for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14"))


class QuestionApprovalStateMachineTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self.conn.execute(
            "INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('s1', 's.docx', 'hash', 'docx')"
        )
        self.insert_candidate("q1")

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def insert_candidate(self, question_id: str):
        self.conn.execute(
            """INSERT INTO questions
            (id, stem, question_type, stage, source_document_id, content_hash, quality_status, review_status)
            VALUES (?, '题干', '选择题', '初中', 's1', ?, 'needs_review', 'pending')""",
            (question_id, f"content-{question_id}"),
        )

    def add_all_passes(self, question_id="q1"):
        input_hash = refresh_current_snapshot(self.conn, question_id)
        for index, verification_type in enumerate(sorted(REQUIRED_VERIFICATION_TYPES)):
            self.conn.execute(
                """INSERT INTO question_verifications
                (id, question_id, verification_type, validator_id, validator_version,
                 status, evidence_json, input_hash, verified_at)
                VALUES (?, ?, ?, 'test', 'v1', 'pass', ?, ?, ?)""",
                (
                    f"{question_id}-{verification_type}", question_id, verification_type,
                    json.dumps({"verification_type": verification_type}),
                    input_hash, f"2026-01-01T00:00:0{index}Z",
                ),
            )

    def test_raw_sql_cannot_insert_approved_question_without_evidence(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO questions
                (id, stem, question_type, stage, source_document_id, content_hash, quality_status, review_status)
                VALUES ('raw-approved', '题干', '选择题', '初中', 's1', 'raw-hash', 'approved', 'approved')"""
            )

    def test_raw_sql_cannot_promote_without_all_current_passes(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE questions SET quality_status='approved', review_status='approved' WHERE id='q1'")
        self.assertEqual(
            self.conn.execute("SELECT quality_status, review_status FROM questions WHERE id='q1'").fetchone(),
            ("needs_review", "pending"),
        )

    def test_mismatched_approved_statuses_are_rejected(self):
        self.add_all_passes()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE questions SET quality_status='approved' WHERE id='q1'")

    def test_controlled_gate_can_promote_with_all_five_current_passes(self):
        self.add_all_passes()
        result = apply_automatic_admission(self.conn, "q1")
        self.assertTrue(result.eligible)
        self.assertEqual(
            self.conn.execute("SELECT quality_status, review_status FROM questions WHERE id='q1'").fetchone(),
            ("approved", "approved"),
        )

    def test_approved_question_cannot_be_tampered_with_or_have_evidence_changed(self):
        self.add_all_passes()
        apply_automatic_admission(self.conn, "q1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE questions SET answer='篡改' WHERE id='q1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE question_verifications SET status='fail' WHERE id='q1-asset_semantics'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM question_verifications WHERE id='q1-asset_semantics'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_verifications
                (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash)
                VALUES ('new-fail', 'q1', 'asset_semantics', 'test', 'v2', 'fail', '{}', 'new-hash')"""
            )

    def test_isolation_then_evidence_or_content_change_is_allowed_but_not_approved(self):
        self.add_all_passes()
        apply_automatic_admission(self.conn, "q1")
        self.conn.execute("UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id='q1'")
        self.conn.execute("UPDATE questions SET answer='重新处理' WHERE id='q1'")
        self.conn.execute("UPDATE question_verifications SET status='fail' WHERE id='q1-asset_semantics'")
        self.assertEqual(
            self.conn.execute("SELECT quality_status, review_status FROM questions WHERE id='q1'").fetchone(),
            ("blocked", "pending"),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE questions SET quality_status='approved', review_status='approved' WHERE id='q1'")

    def test_reapplying_migration_is_idempotent(self):
        self.conn.executescript((ROOT / "schema_v2_13.sql").read_text(encoding="utf-8"))
        migrations = self.conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version='v2.13-question-approval-state-machine-2026-07-26'"
        ).fetchone()[0]
        self.assertEqual(migrations, 1)


if __name__ == "__main__":
    unittest.main()
