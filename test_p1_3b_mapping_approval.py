"""Regression coverage for the fail-closed P1-3b q013 mapping exercise."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from input_snapshot import refresh_current_snapshot
from p1_3b_mapping_approval import (
    NODE_ID,
    QUESTION_ID,
    TEXTBOOK_ID,
    _current_bundle,
    run_p1_3b,
)
from question_auto_mapping_audit import AutoMappingAuditRequest, record_auto_mapping_audit
from question_curriculum_mapping_evidence import MappingEvidence, record_mapping_evidence

ROOT = Path(__file__).parent
LIVE_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"


class P13BMappingApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "exercise.db"
        self.mapping_source = self.root / "q013_mapping_source.json"
        self.report = self.root / "p1-3b-report.json"
        shutil.copy2(LIVE_DATABASE, self.database)
        self._prepare_current_pending_mapping()

    def _reset_prepared_database(self) -> None:
        shutil.copy2(LIVE_DATABASE, self.database)
        self._prepare_current_pending_mapping()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _prepare_current_pending_mapping(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE question_textbooks SET fit_status='pending' WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?",
                (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
            )
            import_row = connection.execute(
                """SELECT import_run_id FROM question_textbook_imports
                     WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
                (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
            ).fetchone()
            self.assertIsNotNone(import_row)
            connection.execute("DELETE FROM question_mapping_audits WHERE import_run_id=?", (import_row["import_run_id"],))
            connection.execute("UPDATE controlled_import_runs SET status='validated' WHERE id=?", (import_row["import_run_id"],))
            input_hash = refresh_current_snapshot(connection, QUESTION_ID)
            self.mapping_source.write_text(
                json.dumps({"current_input_hash": input_hash}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            connection.execute(
                """UPDATE controlled_import_runs SET source_hash=?
                     WHERE id=(SELECT import_run_id FROM question_textbook_imports
                               WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?)""",
                (hashlib.sha256(self.mapping_source.read_bytes()).hexdigest(), QUESTION_ID, TEXTBOOK_ID, NODE_ID),
            )
            prior = connection.execute(
                """SELECT features_json, reason FROM question_curriculum_mapping_evidence
                     WHERE question_id=? AND textbook_id=? AND curriculum_node_id=? AND status='candidate'
                     ORDER BY created_at DESC LIMIT 1""",
                (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
            ).fetchone()
            self.assertIsNotNone(prior)
            evidence_id = record_mapping_evidence(
                connection,
                MappingEvidence(
                    question_id=QUESTION_ID,
                    textbook_id=TEXTBOOK_ID,
                    curriculum_node_id=NODE_ID,
                    features=json.loads(prior["features_json"]),
                    decider_id="p1-3b-test-current-evidence",
                    decider_version="1.0.0",
                    status="candidate",
                    confidence=1.0,
                    reason=prior["reason"],
                ),
            )
            audit = record_auto_mapping_audit(
                connection, AutoMappingAuditRequest(evidence_id=evidence_id, reuse_existing=True)
            )
            self.assertEqual(audit.audit_status, "pass")
            connection.commit()
            _, blockers = _current_bundle(connection, self.mapping_source)
            self.assertEqual(blockers, [])
        finally:
            connection.close()

    def test_transition_that_invalidates_current_evidence_is_revoked(self) -> None:
        report = run_p1_3b(
            self.database, mapping_source=self.mapping_source, report_path=self.report
        )
        self.assertEqual(report["action"], "approval_transition_invalidated_evidence_revoked")
        self.assertEqual(report["approved_mapping_count"], 0)
        self.assertEqual(report["fit_status"], "pending")
        self.assertEqual(report["question_status"], ["blocked", "pending"])
        self.assertFalse(report["question_approved"])
        self.assertTrue(report["delivery_tables_empty"])
        self.assertTrue(any(item.startswith("post_approval:current_input_snapshot") for item in report["blockers_before_action"]))
        self.assertTrue(self.report.is_file())

    def test_stale_mapping_source_blocks_without_database_mutation(self) -> None:
        self.mapping_source.write_text("tampered\n", encoding="utf-8")
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        report = run_p1_3b(
            self.database, mapping_source=self.mapping_source, report_path=self.report
        )
        after = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.assertEqual(report["action"], "blocked_zero_mapping_approval")
        self.assertEqual(report["approved_mapping_count"], 0)
        self.assertIn("mapping_source_hash_mismatch", report["blockers_before_action"])
        self.assertEqual(before, after)

    def test_tampering_is_detected_for_all_required_evidence_categories(self) -> None:
        cases = {
            "source": lambda c: c.execute(
                "UPDATE source_documents SET trusted_source=0 WHERE id=(SELECT source_document_id FROM questions WHERE id=?)",
                (QUESTION_ID,),
            ),
            "answer": lambda c: c.execute("UPDATE questions SET answer='tampered' WHERE id=?", (QUESTION_ID,)),
            "asset": lambda c: c.execute(
                """INSERT INTO question_assets (id, question_id, asset_type, relative_path, position, checksum, status)
                     VALUES ('p1-3b-test-asset', ?, 'image', 'test.png', 1, 'x', 'verified')""",
                (QUESTION_ID,),
            ),
            "knowledge": lambda c: c.execute(
                "UPDATE knowledge_points SET review_status='pending' WHERE id='kp-triangle-side-inequality-v1'"
            ),
            "catalog": lambda c: c.execute("UPDATE curriculum_nodes SET status='archived' WHERE id=?", (NODE_ID,)),
        }
        expected = {
            "source": "question_source_not_trusted",
            "answer": "current_input_snapshot_missing_or_invalidated",
            "asset": "current_input_snapshot_missing_or_invalidated",
            "knowledge": "required_knowledge_not_approved",
            "catalog": "current_approved_catalog_or_node_missing",
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                self._reset_prepared_database()
                connection = self._connect()
                try:
                    mutate(connection)
                    connection.commit()
                    _, blockers = _current_bundle(connection, self.mapping_source)
                finally:
                    connection.close()
                self.assertIn(expected[name], blockers)

    def test_content_contract_tampering_is_rejected_before_it_can_affect_scope(self) -> None:
        self._reset_prepared_database()
        connection = self._connect()
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "segment contracts are immutable"):
                connection.execute(
                    """UPDATE controlled_content_segments SET allowed_node_ids_json='[]'
                         WHERE textbook_id=? AND curriculum_node_id=?""",
                    (TEXTBOOK_ID, NODE_ID),
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
