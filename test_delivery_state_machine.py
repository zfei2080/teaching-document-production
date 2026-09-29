"""P0-4 database-level tests for the controlled delivery state machine."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from automatic_gate import REQUIRED_VERIFICATION_TYPES, apply_automatic_admission
from input_snapshot import refresh_current_snapshot

ROOT = Path(__file__).parent
SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15")
)


class DeliveryStateMachineTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self._seed_reference_data()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _seed_reference_data(self):
        self.conn.execute("INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('s1', 'source.docx', 'source-hash', 'docx')")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('t1', '教材', 'v1')")
        self.conn.execute("INSERT INTO classes (id, name, textbook_id) VALUES ('c1', '一班', 't1')")
        self.conn.execute(
            """INSERT INTO rule_sets (id, document_type, purpose, grade_scope, rules_json, version, status)
            VALUES ('r1', 'exercise', '同步巩固', '初中', '{}', 'v1', 'active')"""
        )
        self.conn.execute(
            """INSERT INTO questions (id, stem, question_type, stage, source_document_id, content_hash, quality_status, review_status)
            VALUES ('q1', '题干', '选择题', '初中', 's1', 'content-hash', 'needs_review', 'pending')"""
        )
        self.conn.execute(
            "INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status) VALUES ('q1', 't1', NULL, 'approved')"
        )
        snapshot_hash = refresh_current_snapshot(self.conn, "q1")
        for index, verification_type in enumerate(sorted(REQUIRED_VERIFICATION_TYPES)):
            self.conn.execute(
                """INSERT INTO question_verifications
                (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash, verified_at)
                VALUES (?, 'q1', ?, 'test', 'v1', 'pass', ?, ?, ?)""",
                (
                    f"v-{verification_type}",
                    verification_type,
                    json.dumps({"type": verification_type}),
                    snapshot_hash,
                    f"2026-01-01T00:00:0{index}Z",
                ),
            )
        self.assertTrue(apply_automatic_admission(self.conn, "q1").eligible)
        self.conn.execute(
            """INSERT INTO production_requests
            (id, raw_request, request_type, parsed_spec_json, class_id, status)
            VALUES ('req1', 'request', 'exercise', '{}', 'c1', 'selected')"""
        )
        self.conn.execute(
            """INSERT INTO selection_plans
            (id, request_id, ruleset_id, filters_json, requirements_json, shortages_json, warnings_json, status)
            VALUES ('plan1', 'req1', 'r1', '{}', '{\"count\": 1}', '[]', '[]', 'ready')"""
        )
        self.conn.execute(
            """INSERT INTO selection_plan_questions
            (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
            VALUES ('plan1', 'q1', '待编排', NULL, 1, '符合规则')"""
        )

    def _create_document_with_question(self, *, document_id: str = 'doc1'):
        self.conn.execute(
            """INSERT INTO teaching_documents
            (id, request_id, selection_plan_id, ruleset_id, class_id, document_type, audience, status)
            VALUES (?, 'req1', 'plan1', 'r1', 'c1', 'exercise', 'student', 'draft')""",
            (document_id,),
        )
        self.conn.execute(
            """INSERT INTO document_questions
            (document_id, question_id, section, layer, sort_order)
            VALUES (?, 'q1', '待编排', NULL, 1)""",
            (document_id,),
        )

    def test_cannot_select_unapproved_or_invalidated_question_into_plan(self):
        self.conn.execute("UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id='q1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO selection_plan_questions
                (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
                VALUES ('plan1', 'q1', '待编排', NULL, 2, '绕过')"""
            )

    def test_document_generation_requires_selected_request_and_ready_plan(self):
        self.conn.execute("UPDATE production_requests SET status='blocked' WHERE id='req1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO teaching_documents
                (id, request_id, selection_plan_id, ruleset_id, class_id, document_type, audience, status)
                VALUES ('doc-bad', 'req1', 'plan1', 'r1', 'c1', 'exercise', 'student', 'draft')"""
            )

    def test_document_cannot_jump_or_validate_without_content_hash(self):
        self._create_document_with_question()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE teaching_documents SET status='passed' WHERE id='doc1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE teaching_documents SET status='pending_review' WHERE id='doc1'")
        self.conn.execute("UPDATE teaching_documents SET content_hash='artifact-hash', status='pending_review' WHERE id='doc1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE teaching_documents SET status='delivered' WHERE id='doc1'")

    def test_quality_report_must_match_pending_review_and_all_pass_for_pass_status(self):
        self._create_document_with_question()
        self.conn.execute("UPDATE teaching_documents SET content_hash='artifact-hash', status='pending_review' WHERE id='doc1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO quality_reports
                (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, status)
                VALUES ('qr-bad', 'doc1', 'pass', 'fail', 'pass', 'pass', 'pass')"""
            )
        self.conn.execute(
            """INSERT INTO quality_reports
            (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, findings_json, blockers_json, status)
            VALUES ('qr1', 'doc1', 'pass', 'pass', 'pass', 'pass', '[]', '[]', 'pass')"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO quality_reports
                (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, status)
                VALUES ('qr2', 'doc1', 'pass', 'pass', 'pass', 'pass', 'pass')"""
            )

    def test_delivery_requires_passed_quality_report_and_atomic_usage_rows(self):
        self._create_document_with_question()
        self.conn.execute("UPDATE production_requests SET status='generating' WHERE id='req1'")
        self.conn.execute("UPDATE teaching_documents SET content_hash='artifact-hash', status='pending_review' WHERE id='doc1'")
        self.conn.execute("UPDATE production_requests SET status='validating' WHERE id='req1'")
        self.conn.execute(
            """INSERT INTO quality_reports
            (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, findings_json, blockers_json, status)
            VALUES ('qr1', 'doc1', 'pass', 'pass', 'pass', 'pass', '[]', '[]', 'pass')"""
        )
        self.conn.execute("UPDATE teaching_documents SET status='passed' WHERE id='doc1'")
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='req1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE teaching_documents SET status='delivered' WHERE id='doc1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_usage
                (id, question_id, class_id, document_id, section, usage_type, delivered)
                VALUES ('u-bad', 'q1', 'c1', 'doc1', '待编排', 'exercise', 0)"""
            )
        with self.conn:
            self.conn.execute(
                """INSERT INTO question_usage
                (id, question_id, class_id, document_id, section, usage_type, delivered)
                VALUES ('u1', 'q1', 'c1', 'doc1', '待编排', 'exercise', 1)"""
            )
            self.conn.execute("UPDATE teaching_documents SET status='delivered' WHERE id='doc1'")
            self.conn.execute("UPDATE production_requests SET status='delivered' WHERE id='req1'")
        self.assertEqual(
            self.conn.execute("SELECT status FROM teaching_documents WHERE id='doc1'").fetchone()[0],
            'delivered',
        )
        self.assertEqual(
            self.conn.execute("SELECT delivered FROM question_usage WHERE id='u1'").fetchone()[0],
            1,
        )

    def test_failed_delivery_transaction_rolls_back_half_written_usage(self):
        self._create_document_with_question(document_id='doc2')
        self.conn.execute(
            """INSERT INTO questions (id, stem, question_type, stage, source_document_id, content_hash, quality_status, review_status)
            VALUES ('q2', '题干2', '选择题', '初中', 's1', 'content-hash-2', 'needs_review', 'pending')"""
        )
        self.conn.execute(
            "INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status) VALUES ('q2', 't1', NULL, 'approved')"
        )
        snapshot_hash = refresh_current_snapshot(self.conn, 'q2')
        for index, verification_type in enumerate(sorted(REQUIRED_VERIFICATION_TYPES)):
            self.conn.execute(
                """INSERT INTO question_verifications
                (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash, verified_at)
                VALUES (?, 'q2', ?, 'test', 'v1', 'pass', ?, ?, ?)""",
                (
                    f"q2-{verification_type}",
                    verification_type,
                    json.dumps({"type": verification_type}),
                    snapshot_hash,
                    f"2026-01-02T00:00:0{index}Z",
                ),
            )
        self.assertTrue(apply_automatic_admission(self.conn, 'q2').eligible)
        self.conn.execute(
            """INSERT INTO selection_plan_questions
            (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
            VALUES ('plan1', 'q2', '待编排', NULL, 2, '符合规则')"""
        )
        self.conn.execute(
            """INSERT INTO document_questions
            (document_id, question_id, section, layer, sort_order)
            VALUES ('doc2', 'q2', '待编排', NULL, 2)"""
        )
        self.conn.execute("UPDATE production_requests SET status='generating' WHERE id='req1'")
        self.conn.execute("UPDATE teaching_documents SET content_hash='artifact-hash', status='pending_review' WHERE id='doc2'")
        self.conn.execute("UPDATE production_requests SET status='validating' WHERE id='req1'")
        self.conn.execute(
            """INSERT INTO quality_reports
            (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, findings_json, blockers_json, status)
            VALUES ('qr2', 'doc2', 'pass', 'pass', 'pass', 'pass', '[]', '[]', 'pass')"""
        )
        self.conn.execute("UPDATE teaching_documents SET status='passed' WHERE id='doc2'")
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='req1'")
        self.conn.execute("SAVEPOINT broken_delivery")
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(
                    """INSERT INTO question_usage
                    (id, question_id, class_id, document_id, section, usage_type, delivered)
                    VALUES ('u2a', 'q1', 'c1', 'doc2', '待编排', 'exercise', 1)"""
                )
                self.conn.execute(
                    """INSERT INTO question_usage
                    (id, question_id, class_id, document_id, section, usage_type, delivered)
                    VALUES ('u2b', 'q1', 'c1', 'doc2', '待编排', 'exercise', 1)"""
                )
                self.conn.execute("UPDATE teaching_documents SET status='delivered' WHERE id='doc2'")
        finally:
            self.conn.execute("ROLLBACK TO broken_delivery")
            self.conn.execute("RELEASE broken_delivery")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_usage WHERE document_id='doc2'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM teaching_documents WHERE id='doc2'").fetchone()[0], 'passed')

    def test_duplicate_delivery_requires_explicit_reuse_and_is_immutable(self):
        self._create_document_with_question(document_id='doc3')
        self.conn.execute("UPDATE production_requests SET status='generating' WHERE id='req1'")
        self.conn.execute("UPDATE teaching_documents SET content_hash='artifact-hash', status='pending_review' WHERE id='doc3'")
        self.conn.execute("UPDATE production_requests SET status='validating' WHERE id='req1'")
        self.conn.execute(
            """INSERT INTO quality_reports
            (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, findings_json, blockers_json, status)
            VALUES ('qr3', 'doc3', 'pass', 'pass', 'pass', 'pass', '[]', '[]', 'pass')"""
        )
        self.conn.execute("UPDATE teaching_documents SET status='passed' WHERE id='doc3'")
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='req1'")
        self.conn.execute(
            """INSERT INTO question_usage
            (id, question_id, class_id, document_id, section, usage_type, delivered)
            VALUES ('u3', 'q1', 'c1', 'doc3', '待编排', 'exercise', 1)"""
        )
        self.conn.execute("UPDATE teaching_documents SET status='delivered' WHERE id='doc3'")
        self.conn.execute("UPDATE production_requests SET status='delivered' WHERE id='req1'")

        self.conn.execute(
            """INSERT INTO production_requests
            (id, raw_request, request_type, parsed_spec_json, class_id, status)
            VALUES ('req2', 'request2', 'exercise', '{}', 'c1', 'selected')"""
        )
        self.conn.execute(
            """INSERT INTO selection_plans
            (id, request_id, ruleset_id, filters_json, requirements_json, shortages_json, warnings_json, status)
            VALUES ('plan2', 'req2', 'r1', '{}', '{\"count\": 1}', '[]', '[]', 'ready')"""
        )
        self.conn.execute(
            """INSERT INTO selection_plan_questions
            (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
            VALUES ('plan2', 'q1', '待编排', NULL, 1, '复用')"""
        )
        self.conn.execute(
            """INSERT INTO teaching_documents
            (id, request_id, selection_plan_id, ruleset_id, class_id, document_type, audience, status)
            VALUES ('doc4', 'req2', 'plan2', 'r1', 'c1', 'exercise', 'student', 'draft')"""
        )
        self.conn.execute(
            """INSERT INTO document_questions
            (document_id, question_id, section, layer, sort_order)
            VALUES ('doc4', 'q1', '待编排', NULL, 1)"""
        )
        self.conn.execute("UPDATE production_requests SET status='generating' WHERE id='req2'")
        self.conn.execute("UPDATE teaching_documents SET content_hash='artifact-hash-2', status='pending_review' WHERE id='doc4'")
        self.conn.execute("UPDATE production_requests SET status='validating' WHERE id='req2'")
        self.conn.execute(
            """INSERT INTO quality_reports
            (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, findings_json, blockers_json, status)
            VALUES ('qr4', 'doc4', 'pass', 'pass', 'pass', 'pass', '[]', '[]', 'pass')"""
        )
        self.conn.execute("UPDATE teaching_documents SET status='passed' WHERE id='doc4'")
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='req2'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_usage
                (id, question_id, class_id, document_id, section, usage_type, delivered)
                VALUES ('u4-bad', 'q1', 'c1', 'doc4', '待编排', 'exercise', 1)"""
            )
        self.conn.execute(
            """INSERT INTO question_usage
            (id, question_id, class_id, document_id, section, usage_type, delivered, reuse_allowed, reuse_reason)
            VALUES ('u4', 'q1', 'c1', 'doc4', '待编排', 'exercise', 1, 1, '复习回顾')"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE question_usage SET reuse_reason='篡改' WHERE id='u4'")


if __name__ == '__main__':
    unittest.main()
