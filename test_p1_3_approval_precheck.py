from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from p1_3_approval_precheck import (
    PILOT_CURRENT_NODE_ID,
    PILOT_TEXTBOOK_ID,
    build_p1_3_precheck,
    write_precheck_report,
)


class P13ApprovalPrecheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "development.db"
        self.audit = self.root / "p1-2b.json"
        self._create_database()
        self.audit.write_text(json.dumps({"records": [{"status": "candidate_content_only"}]}), encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _create_database(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript(
                """
                CREATE TABLE questions (
                    id TEXT PRIMARY KEY, stem TEXT, answer TEXT, analysis TEXT,
                    question_type TEXT, source_document_id TEXT, content_hash TEXT,
                    quality_status TEXT, review_status TEXT
                );
                CREATE TABLE source_documents (
                    id TEXT PRIMARY KEY, trusted_source INTEGER, intake_manifest_json TEXT
                );
                CREATE TABLE question_verifications (
                    id TEXT PRIMARY KEY, question_id TEXT, verification_type TEXT,
                    status TEXT, verified_at TEXT
                );
                CREATE TABLE question_textbooks (
                    question_id TEXT, textbook_id TEXT, curriculum_node_id TEXT, fit_status TEXT
                );
                CREATE TABLE question_knowledge_points (question_id TEXT, knowledge_point_id TEXT);
                CREATE TABLE question_curriculum_mapping_evidence (
                    question_id TEXT, textbook_id TEXT, curriculum_node_id TEXT,
                    question_input_hash TEXT, invalidated_at TEXT, status TEXT
                );
                CREATE TABLE question_auto_mapping_audit_logs (
                    question_id TEXT, textbook_id TEXT, curriculum_node_id TEXT,
                    question_input_hash TEXT, audit_status TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO source_documents VALUES (?, ?, ?)",
                ("source-1", 1, json.dumps({"hashes_match": True})),
            )
            connection.execute(
                "INSERT INTO questions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("q1", "等腰三角形的性质", "A", "analysis", "选择题", "source-1", "H" * 64, "blocked", "pending"),
            )
            for position, verification_type in enumerate(("source_fidelity", "structural_consistency", "mathematical_independent", "asset_semantics")):
                connection.execute(
                    "INSERT INTO question_verifications VALUES (?, ?, ?, ?, ?)",
                    (str(position), "q1", verification_type, "pass", "2026-07-30"),
                )
            connection.commit()
        finally:
            connection.close()

    def test_incomplete_evidence_produces_zero_approval_without_database_change(self) -> None:
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        report = build_p1_3_precheck(self.database, p12b_audit_path=self.audit)
        after = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertTrue(report["database"]["unchanged"])
        self.assertEqual(report["decision"], "blocked_zero_approval_gap_report")
        self.assertEqual(report["approved_question_count"], 0)
        blockers = report["questions"][0]["blockers"]
        self.assertIn("current_target_draft_mapping_missing", blockers)
        self.assertIn("controlled_content_not_formally_imported_or_current", blockers)
        output = write_precheck_report(report, self.root / "audits" / "precheck.json")
        self.assertTrue(output.is_file())

    def test_formal_mapping_rows_do_not_authorize_without_formal_content_import(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "INSERT INTO question_textbooks VALUES (?, ?, ?, ?)",
                ("q1", PILOT_TEXTBOOK_ID, PILOT_CURRENT_NODE_ID, "approved"),
            )
            connection.execute("INSERT INTO question_knowledge_points VALUES (?, ?)", ("q1", "kp-1"))
            connection.execute(
                "INSERT INTO question_curriculum_mapping_evidence VALUES (?, ?, ?, ?, ?, ?)",
                ("q1", PILOT_TEXTBOOK_ID, PILOT_CURRENT_NODE_ID, "H" * 64, None, "candidate"),
            )
            connection.execute(
                "INSERT INTO question_auto_mapping_audit_logs VALUES (?, ?, ?, ?, ?)",
                ("q1", PILOT_TEXTBOOK_ID, PILOT_CURRENT_NODE_ID, "H" * 64, "pass"),
            )
            connection.commit()
        finally:
            connection.close()
        report = build_p1_3_precheck(self.database, p12b_audit_path=self.audit)
        blockers = report["questions"][0]["blockers"]
        self.assertIn("controlled_content_not_formally_imported_or_current", blockers)
        self.assertIn("current_input_snapshot_missing_or_stale", blockers)
        self.assertEqual(report["eligible_question_count"], 0)
        self.assertFalse(report["approval_run_authorized"])


if __name__ == "__main__":
    unittest.main()
