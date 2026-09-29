from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apply_schema_v2_16 import apply_schema
from automatic_gate import REQUIRED_VERIFICATION_TYPES, apply_automatic_admission
from input_snapshot import refresh_current_snapshot

ROOT = Path(__file__).parent
BASE_SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15")
)


class ClassProgressStateMachineTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in BASE_SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self.conn.close()
        apply_schema(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._seed_base()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _seed_base(self):
        self.conn.execute("INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('s1', 'source.docx', 'hash-s1', 'docx')")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('tb1', '教材一', 'v1')")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('tb2', '教材二', 'v1')")
        self.conn.execute(
            """INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, sequence, catalog_version, status)
            VALUES ('n-current', 'tb1', '初中', '七上', 'topic', '当前节点', 1, 'v1', 'active')"""
        )
        self.conn.execute(
            """INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, sequence, catalog_version, status)
            VALUES ('n-prev', 'tb1', '初中', '七上', 'topic', '前置节点', 2, 'v1', 'active')"""
        )
        self.conn.execute(
            """INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, sequence, catalog_version, status)
            VALUES ('n-future', 'tb1', '初中', '七上', 'topic', '后续节点', 3, 'v1', 'active')"""
        )
        self.conn.execute(
            """INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, sequence, catalog_version, status)
            VALUES ('n-cross', 'tb2', '初中', '七上', 'topic', '跨教材节点', 1, 'v1', 'active')"""
        )
        self.conn.execute(
            "INSERT INTO classes (id, name, textbook_id, current_curriculum_node_id) VALUES ('c1', '一班', 'tb1', 'n-current')"
        )
        self.conn.execute(
            """INSERT INTO rule_sets (id, document_type, purpose, grade_scope, rules_json, version, status)
            VALUES ('r1', 'exercise', '同步巩固', '初中', '{}', 'v1', 'active')"""
        )
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
        for question_id, stem, content_hash in (
            ('q-current', '题1', 'hash-q1'),
            ('q-prev', '题2', 'hash-q2'),
            ('q-future', '题3', 'hash-q3'),
        ):
            self.conn.execute(
                """INSERT INTO questions
                (id, stem, question_type, stage, source_document_id, content_hash, quality_status, review_status)
                VALUES (?, ?, '选择题', '初中', 's1', ?, 'needs_review', 'pending')""",
                (question_id, stem, content_hash),
            )
        self.conn.execute(
            """INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status)
            VALUES ('q-current', 'tb1', 'n-current', 'approved')"""
        )
        self.conn.execute(
            """INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status)
            VALUES ('q-prev', 'tb1', 'n-prev', 'approved')"""
        )
        self.conn.execute(
            """INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status)
            VALUES ('q-future', 'tb1', 'n-future', 'approved')"""
        )
        for question_id in ('q-current', 'q-prev', 'q-future'):
            snapshot_hash = refresh_current_snapshot(self.conn, question_id)
            for index, verification_type in enumerate(sorted(REQUIRED_VERIFICATION_TYPES)):
                self.conn.execute(
                    """INSERT INTO question_verifications
                    (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash, verified_at)
                    VALUES (?, ?, ?, 'test', 'v1', 'pass', '{\"ok\": true}', ?, ?)""",
                    (
                        f"{question_id}-{verification_type}",
                        question_id,
                        verification_type,
                        snapshot_hash,
                        f"2026-01-01T00:00:{index:02d}Z",
                    ),
                )
            self.assertTrue(apply_automatic_admission(self.conn, question_id).eligible)
        self.conn.commit()

    def _insert_progress(self, progress_id: str = 'progress-1', current: str = 'n-current', allowed_json: str = '["n-current","n-prev"]'):
        self.conn.execute(
            """INSERT INTO class_progress_controls
            (id, class_id, textbook_id, current_curriculum_node_id, allowed_nodes_json, status)
            VALUES (?, 'c1', 'tb1', ?, ?, 'active')""",
            (progress_id, current, allowed_json),
        )
        allowed_nodes = []
        if allowed_json == '["n-current","n-prev"]':
            allowed_nodes = ['n-current', 'n-prev']
        elif allowed_json == '["n-prev"]':
            allowed_nodes = ['n-prev']
        for node_id in allowed_nodes:
            self.conn.execute(
                "INSERT INTO class_progress_allowed_nodes (progress_id, curriculum_node_id) VALUES (?, ?)",
                (progress_id, node_id),
            )
        self.conn.commit()

    def _create_request_bundle(self, request_id: str, plan_id: str, question_id: str):
        self.conn.execute(
            """INSERT INTO production_requests
            (id, raw_request, request_type, parsed_spec_json, class_id, status)
            VALUES (?, ?, 'exercise', '{}', 'c1', 'selected')""",
            (request_id, request_id),
        )
        self.conn.execute(
            """INSERT INTO selection_plans
            (id, request_id, ruleset_id, filters_json, requirements_json, shortages_json, warnings_json, status)
            VALUES (?, ?, 'r1', '{}', '{\"count\": 1}', '[]', '[]', 'ready')""",
            (plan_id, request_id),
        )
        self.conn.execute(
            """INSERT INTO selection_plan_questions
            (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
            VALUES (?, ?, '待编排', NULL, 1, '测试计划')""",
            (plan_id, question_id),
        )

    def _record_delivered_usage(self, request_id: str, plan_id: str, document_id: str, question_id: str, usage_id: str):
        self._create_request_bundle(request_id, plan_id, question_id)
        self.conn.execute(
            """INSERT INTO teaching_documents
            (id, request_id, selection_plan_id, ruleset_id, class_id, document_type, audience, status)
            VALUES (?, ?, ?, 'r1', 'c1', 'exercise', 'student', 'draft')""",
            (document_id, request_id, plan_id),
        )
        self.conn.execute(
            "INSERT INTO document_questions (document_id, question_id, section, layer, sort_order) VALUES (?, ?, '待编排', NULL, 1)",
            (document_id, question_id),
        )
        self.conn.execute("UPDATE production_requests SET status='generating' WHERE id=?", (request_id,))
        self.conn.execute("UPDATE teaching_documents SET content_hash=?, status='pending_review' WHERE id=?", (f"artifact-{document_id}", document_id))
        self.conn.execute("UPDATE production_requests SET status='validating' WHERE id=?", (request_id,))
        self.conn.execute(
            """INSERT INTO quality_reports
            (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, findings_json, blockers_json, status)
            VALUES (?, ?, 'pass', 'pass', 'pass', 'pass', '[]', '[]', 'pass')""",
            (f"qr-{document_id}", document_id),
        )
        self.conn.execute("UPDATE teaching_documents SET status='passed' WHERE id=?", (document_id,))
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id=?", (request_id,))
        self.conn.execute(
            """INSERT INTO question_usage
            (id, question_id, class_id, document_id, section, usage_type, delivered)
            VALUES (?, ?, 'c1', ?, '待编排', 'exercise', 1)""",
            (usage_id, question_id, document_id),
        )
        self.conn.commit()

    def _create_reuse_document(self, request_id: str, plan_id: str, document_id: str, question_id: str):
        self._create_request_bundle(request_id, plan_id, question_id)
        self.conn.execute(
            """INSERT INTO teaching_documents
            (id, request_id, selection_plan_id, ruleset_id, class_id, document_type, audience, status)
            VALUES (?, ?, ?, 'r1', 'c1', 'exercise', 'student', 'draft')""",
            (document_id, request_id, plan_id),
        )
        self.conn.execute(
            "INSERT INTO document_questions (document_id, question_id, section, layer, sort_order) VALUES (?, ?, '待编排', NULL, 1)",
            (document_id, question_id),
        )
        self.conn.execute("UPDATE production_requests SET status='generating' WHERE id=?", (request_id,))
        self.conn.execute("UPDATE teaching_documents SET content_hash=?, status='pending_review' WHERE id=?", (f"artifact-{document_id}", document_id))
        self.conn.execute("UPDATE production_requests SET status='validating' WHERE id=?", (request_id,))
        self.conn.execute(
            """INSERT INTO quality_reports
            (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, findings_json, blockers_json, status)
            VALUES (?, ?, 'pass', 'pass', 'pass', 'pass', '[]', '[]', 'pass')""",
            (f"qr-{document_id}", document_id),
        )
        self.conn.execute("UPDATE teaching_documents SET status='passed' WHERE id=?", (document_id,))
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id=?", (request_id,))
        self.conn.commit()

    def test_progress_requires_current_inside_allowed_and_matching_textbook(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO class_progress_controls
                (id, class_id, textbook_id, current_curriculum_node_id, allowed_nodes_json, status)
                VALUES ('bad1', 'c1', 'tb1', 'n-current', '[\"n-prev\"]', 'active')"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO class_progress_controls
                (id, class_id, textbook_id, current_curriculum_node_id, allowed_nodes_json, status)
                VALUES ('bad2', 'c1', 'tb1', 'n-current', '[\"n-current\",\"n-cross\"]', 'active')"""
            )

    def test_only_one_active_progress_per_class(self):
        self._insert_progress()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO class_progress_controls
                (id, class_id, textbook_id, current_curriculum_node_id, allowed_nodes_json, status)
                VALUES ('progress-2', 'c1', 'tb1', 'n-prev', '[\"n-prev\"]', 'active')"""
            )

    def test_selection_plan_question_must_match_active_progress(self):
        self._insert_progress()
        self.conn.execute(
            """INSERT INTO selection_plan_questions
            (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
            VALUES ('plan1', 'q-current', '待编排', NULL, 1, '当前进度')"""
        )
        self.conn.execute(
            """INSERT INTO selection_plan_questions
            (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
            VALUES ('plan1', 'q-prev', '待编排', NULL, 2, '已学前置')"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO selection_plan_questions
                (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
                VALUES ('plan1', 'q-future', '待编排', NULL, 3, '猜测前置')"""
            )

    def test_reuse_authorization_requires_same_class_request_question_and_progress(self):
        self._insert_progress()
        self._record_delivered_usage('req-legacy', 'plan-legacy', 'legacy-doc', 'q-current', 'usage-1')
        self._create_reuse_document('req2', 'plan2', 'doc-reuse', 'q-current')
        self.conn.execute(
            """INSERT INTO question_reuse_authorizations
            (id, class_id, request_id, question_id, progress_id, reason, status)
            VALUES ('auth-mismatch', 'c1', 'req1', 'q-current', 'progress-1', '错题回练', 'active')"""
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_usage
                (id, question_id, class_id, document_id, section, usage_type, delivered)
                VALUES ('usage-2', 'q-current', 'c1', 'doc-reuse', '待编排', 'exercise', 1)"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_usage
                (id, question_id, class_id, document_id, section, usage_type, delivered, reuse_allowed, reuse_reason)
                VALUES ('usage-2', 'q-current', 'c1', 'doc-reuse', '待编排', 'exercise', 1, 1, '错题回练')"""
            )

        self.conn.execute(
            """INSERT INTO question_reuse_authorizations
            (id, class_id, request_id, question_id, progress_id, reason, status)
            VALUES ('auth-2', 'c1', 'req2', 'q-current', 'progress-1', '错题回练', 'active')"""
        )
        self.conn.execute(
            """INSERT INTO question_usage
            (id, question_id, class_id, document_id, section, usage_type, delivered, reuse_allowed, reuse_reason)
            VALUES ('usage-2', 'q-current', 'c1', 'doc-reuse', '待编排', 'exercise', 1, 1, '错题回练')"""
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM question_reuse_authorizations WHERE id='auth-mismatch'").fetchone()[0],
            'active',
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM question_reuse_authorizations WHERE id='auth-2'").fetchone()[0],
            'used',
        )

    def test_duplicate_same_class_usage_without_matching_authorization_is_blocked(self):
        self._insert_progress()
        self._record_delivered_usage('req-legacy', 'plan-legacy', 'legacy-doc', 'q-current', 'usage-1')
        self.conn.execute(
            """INSERT INTO question_reuse_authorizations
            (id, class_id, request_id, question_id, progress_id, reason, status)
            VALUES ('auth-1', 'c1', 'req1', 'q-current', 'progress-1', '错题回练', 'active')"""
        )
        self.conn.commit()
        self._create_reuse_document('req2', 'plan2', 'doc-reuse', 'q-current')
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_usage
                (id, question_id, class_id, document_id, section, usage_type, delivered, reuse_allowed, reuse_reason)
                VALUES ('usage-2', 'q-current', 'c1', 'doc-reuse', '待编排', 'exercise', 1, 1, '原因不匹配')"""
            )

    def test_progress_change_invalidates_old_authorizations_and_voids_ready_plans(self):
        self._insert_progress()
        self._record_delivered_usage('req-legacy', 'plan-legacy', 'legacy-doc', 'q-current', 'usage-1')
        self.conn.execute(
            """INSERT INTO question_reuse_authorizations
            (id, class_id, request_id, question_id, progress_id, reason, status)
            VALUES ('auth-1', 'c1', 'req1', 'q-current', 'progress-1', '错题回练', 'active')"""
        )
        self.conn.commit()
        self.conn.execute("UPDATE class_progress_controls SET status='replaced', replaced_at=CURRENT_TIMESTAMP WHERE id='progress-1'")
        self.conn.execute(
            """INSERT INTO class_progress_controls
            (id, class_id, textbook_id, current_curriculum_node_id, allowed_nodes_json, status)
            VALUES ('progress-2', 'c1', 'tb1', 'n-prev', '[\"n-prev\"]', 'active')"""
        )
        self.conn.execute("INSERT INTO class_progress_allowed_nodes (progress_id, curriculum_node_id) VALUES ('progress-2', 'n-prev')")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT status, invalidation_reason FROM question_reuse_authorizations WHERE id='auth-1'"
        ).fetchone()
        self.assertEqual(row, ('invalidated', 'progress_changed'))
        plan_row = self.conn.execute("SELECT status FROM selection_plans WHERE id='plan1'").fetchone()[0]
        self.assertEqual(plan_row, 'voided')

    def test_apply_schema_is_idempotent(self):
        apply_schema(self.path)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version='v2.16-class-progress-reuse-authorization-2026-07-26'"
        ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()
