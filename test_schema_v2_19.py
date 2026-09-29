from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from apply_schema_v2_16 import apply_schema as apply_schema_v2_16
from apply_schema_v2_17 import apply_schema as apply_schema_v2_17
from apply_schema_v2_19 import apply_schema as apply_schema_v2_19
from automatic_gate import REQUIRED_VERIFICATION_TYPES, apply_automatic_admission
from input_snapshot import refresh_current_snapshot

ROOT = Path(__file__).parent
BASE_SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15")
)
MIGRATION = 'v2.19-delivery-quality-gate-binding-2026-07-26'


class SchemaV219MigrationTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.path = Path(handle.name)
        conn = sqlite3.connect(self.path)
        conn.execute('PRAGMA foreign_keys=ON')
        for schema in BASE_SCHEMAS:
            conn.executescript((ROOT / schema).read_text(encoding='utf-8'))
        conn.close()
        apply_schema_v2_16(self.path)
        apply_schema_v2_17(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute('PRAGMA foreign_keys=ON')
        self._seed_base()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _seed_base(self):
        self.conn.execute("INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('s1', 'source.docx', 'hash-s1', 'docx')")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('tb1', '教材', 'v1')")
        self.conn.execute(
            """INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, sequence, catalog_version, status)
            VALUES ('n1', 'tb1', '初中', '七上', 'topic', '当前节点', 1, 'v1', 'active')"""
        )
        self.conn.execute("INSERT INTO classes (id, name, textbook_id, current_curriculum_node_id) VALUES ('c1', '一班', 'tb1', 'n1')")
        self.conn.execute(
            """INSERT INTO rule_sets (id, document_type, purpose, grade_scope, rules_json, version, status)
            VALUES ('r1', 'exercise', '同步巩固', '初中', '{}', 'v1', 'active')"""
        )
        self.conn.execute(
            """INSERT INTO questions (id, stem, question_type, stage, source_document_id, content_hash, quality_status, review_status)
            VALUES ('q1', '题干', '选择题', '初中', 's1', 'content-hash', 'needs_review', 'pending')"""
        )
        self.conn.execute(
            "INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status) VALUES ('q1', 'tb1', 'n1', 'approved')"
        )
        snapshot_hash = refresh_current_snapshot(self.conn, 'q1')
        for index, verification_type in enumerate(sorted(REQUIRED_VERIFICATION_TYPES)):
            self.conn.execute(
                """INSERT INTO question_verifications
                (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash, verified_at)
                VALUES (?, 'q1', ?, 'test', 'v1', 'pass', '{\"ok\": true}', ?, ?)""",
                (
                    f"q1-{verification_type}",
                    verification_type,
                    snapshot_hash,
                    f"2026-01-01T00:00:{index:02d}Z",
                ),
            )
        self.assertTrue(apply_automatic_admission(self.conn, 'q1').eligible)
        self.conn.execute(
            """INSERT INTO class_progress_controls
            (id, class_id, textbook_id, current_curriculum_node_id, allowed_nodes_json, status)
            VALUES ('progress-1', 'c1', 'tb1', 'n1', '[\"n1\"]', 'active')"""
        )
        self.conn.execute("INSERT INTO class_progress_allowed_nodes (progress_id, curriculum_node_id) VALUES ('progress-1', 'n1')")
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
        self.conn.execute(
            """INSERT INTO teaching_documents
            (id, request_id, selection_plan_id, ruleset_id, class_id, document_type, audience, status)
            VALUES ('doc1', 'req1', 'plan1', 'r1', 'c1', 'exercise', 'student', 'draft')"""
        )
        self.conn.execute(
            """INSERT INTO document_questions
            (document_id, question_id, section, layer, sort_order)
            VALUES ('doc1', 'q1', '待编排', NULL, 1)"""
        )
        self.conn.execute("UPDATE production_requests SET status='generating' WHERE id='req1'")
        self.conn.execute("UPDATE teaching_documents SET content_hash='artifact-hash', status='pending_review' WHERE id='doc1'")
        self.conn.execute("UPDATE production_requests SET status='validating' WHERE id='req1'")
        self.conn.commit()

    def _insert_pass_report_with_evidence(self, *, artifact_role='docx_student', content_hash='artifact-hash', question_set_hash='qs', snapshot_hash='ss'):
        self.conn.execute(
            """INSERT INTO quality_reports
            (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, findings_json, blockers_json, status)
            VALUES ('qr1', 'doc1', 'pass', 'pass', 'pass', 'pass', '[]', '[]', 'pass')"""
        )
        self.conn.execute(
            """INSERT INTO artifact_validation_evidence
            (id, document_id, quality_report_id, artifact_role, content_hash, question_set_hash, snapshot_hash, manifest_hash, artifact_hash, gate, checks_json, findings_json)
            VALUES ('ev1', 'doc1', 'qr1', ?, ?, ?, ?, 'mh', 'ah', 'pass', '[]', '[]')""",
            (artifact_role, content_hash, question_set_hash, snapshot_hash),
        )

    def test_apply_schema_on_v217_is_successful_and_idempotent(self):
        apply_schema_v2_19(self.path)
        apply_schema_v2_19(self.path)
        count = self.conn.execute(
            'SELECT COUNT(*) FROM schema_migrations WHERE version=?',
            (MIGRATION,),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_raw_sql_delivery_requires_matching_pass_evidence(self):
        apply_schema_v2_19(self.path)
        self._insert_pass_report_with_evidence(content_hash='artifact-hash', question_set_hash='bad', snapshot_hash='bad')
        self.conn.execute("UPDATE teaching_documents SET status='passed' WHERE id='doc1'")
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='req1'")
        self.conn.execute(
            """INSERT INTO question_usage
            (id, question_id, class_id, document_id, section, usage_type, delivered, reuse_allowed, reuse_reason)
            VALUES ('usage1', 'q1', 'c1', 'doc1', '待编排', 'exercise', 1, 0, NULL)"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE teaching_documents SET status='delivered' WHERE id='doc1'")

    def test_raw_sql_delivery_allows_exact_match(self):
        apply_schema_v2_19(self.path)
        question_set_hash = self.conn.execute(
            "SELECT group_concat(question_id, '|') FROM (SELECT question_id FROM document_questions WHERE document_id='doc1' ORDER BY sort_order ASC, question_id ASC)"
        ).fetchone()[0]
        snapshot_hash = self.conn.execute(
            "SELECT group_concat(question_id || ':' || input_hash, '|') FROM (SELECT dq.question_id AS question_id, qis.input_hash AS input_hash FROM document_questions dq JOIN question_input_snapshots qis ON qis.question_id=dq.question_id WHERE dq.document_id='doc1' AND qis.input_hash IS NOT NULL AND qis.invalidated_at IS NULL ORDER BY dq.sort_order ASC, dq.question_id ASC)"
        ).fetchone()[0]
        self._insert_pass_report_with_evidence(content_hash='artifact-hash', question_set_hash=question_set_hash, snapshot_hash=snapshot_hash)
        self.conn.execute("UPDATE teaching_documents SET status='passed' WHERE id='doc1'")
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='req1'")
        self.conn.execute(
            """INSERT INTO question_usage
            (id, question_id, class_id, document_id, section, usage_type, delivered, reuse_allowed, reuse_reason)
            VALUES ('usage1', 'q1', 'c1', 'doc1', '待编排', 'exercise', 1, 0, NULL)"""
        )
        self.conn.execute("UPDATE teaching_documents SET status='delivered' WHERE id='doc1'")
        self.assertEqual(self.conn.execute("SELECT status FROM teaching_documents WHERE id='doc1'").fetchone()[0], 'delivered')


if __name__ == '__main__':
    unittest.main()
