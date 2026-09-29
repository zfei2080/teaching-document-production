"""Tests for auditable shortage blocking and controlled delivery workflow."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from automatic_gate import REQUIRED_VERIFICATION_TYPES, apply_automatic_admission
from input_snapshot import refresh_current_snapshot
from artifact_validation import ArtifactEvidenceRecord
from progress_control import ProgressControlError, set_active_progress
from production_workflow import (
    bind_generated_artifact,
    create_generation_task,
    deliver_document,
    plan_selection,
    record_quality_report,
)
from selection_engine import SelectionRequest

ROOT = Path(__file__).parent
SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15", "_16", "_17", "_19")
)


class ProductionWorkflowTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self._create_progress_contract_fixture()
        self.conn.execute("INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('s1', 'source.docx', 'source-hash', 'docx')")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('t1', '教材', 'v1')")
        self.conn.execute("INSERT INTO classes (id, name, textbook_id) VALUES ('c1', '虚拟班', 't1')")
        for node_id in ("node-current", "node-prereq", "node-future"):
            self.conn.execute(
                """INSERT INTO curriculum_nodes
                (id, textbook_id, stage, grade_level, node_type, name, sequence, catalog_version, status)
                VALUES (?, 't1', '初中', '八下', 'topic', ?, 1, 'v1', 'active')""",
                (node_id, node_id),
            )
        self.conn.execute(
            """INSERT INTO rule_sets (id, document_type, purpose, grade_scope, rules_json, version, status)
            VALUES ('r1', 'exercise', '同步巩固', '初中', '{}', 'v1', 'active')"""
        )
        set_active_progress(
            self.conn,
            progress_id="progress-1",
            class_id="c1",
            current_curriculum_node_id="node-current",
            allowed_curriculum_node_ids=["node-current", "node-prereq"],
        )

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _create_progress_contract_fixture(self):
        return

    def _insert_approved_question(self, question_id: str = "q1"):
        node_id = {"q1": "node-current", "q2": "node-prereq", "q3": "node-future"}.get(question_id, "node-current")
        self.conn.execute(
            """INSERT INTO questions (id, stem, question_type, stage, source_document_id, content_hash, quality_status, review_status)
            VALUES (?, '题干', '选择题', '初中', 's1', ?, 'needs_review', 'pending')""",
            (question_id, f"hash-{question_id}"),
        )
        self.conn.execute(
            "INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status) VALUES (?, 't1', ?, 'approved')",
            (question_id, node_id),
        )
        snapshot_hash = refresh_current_snapshot(self.conn, question_id)
        for index, verification_type in enumerate(sorted(REQUIRED_VERIFICATION_TYPES)):
            self.conn.execute(
                """INSERT INTO question_verifications
                (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash, verified_at)
                VALUES (?, ?, ?, 'test', 'v1', 'pass', ?, ?, ?)""",
                (
                    f"{question_id}-{verification_type}",
                    question_id,
                    verification_type,
                    json.dumps({"type": verification_type}),
                    snapshot_hash,
                    f"2026-01-01T00:00:0{index}Z",
                ),
            )
        self.assertTrue(apply_automatic_admission(self.conn, question_id).eligible)

    def _artifact_evidence(self, *, gate: str = 'pass', artifact_role: str = 'docx_student') -> ArtifactEvidenceRecord:
        return ArtifactEvidenceRecord(
            artifact_role=artifact_role,
            gate=gate,
            manifest_hash='manifest-hash',
            artifact_hash='artifact-hash',
            checks_json='[]',
            findings_json='[]',
        )

    def _plan_request(self, *, request_id: str = "request-1", plan_id: str = "plan-1", count: int = 1):
        selection = SelectionRequest(
            class_id="c1", textbook_id="t1", stage="初中", count=count, question_types=("选择题",)
        )
        return plan_selection(
            self.conn,
            request_id=request_id,
            plan_id=plan_id,
            ruleset_id="r1",
            raw_request="给虚拟班出练习",
            request_type="exercise",
            parsed_spec={"topic": "测试"},
            selection_request=selection,
        )

    def test_shortage_blocks_request_without_document_or_usage(self):
        result = self._plan_request(count=3)
        self.assertEqual(result.result.shortage, 3)
        self.assertEqual(
            self.conn.execute("SELECT status FROM production_requests WHERE id='request-1'").fetchone()[0],
            "blocked",
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM selection_plans WHERE id='plan-1'").fetchone()[0],
            "blocked",
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM teaching_documents").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_usage").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM selection_plan_questions").fetchone()[0], 0)

    def test_generation_review_and_delivery_happy_path(self):
        self._insert_approved_question()
        self._plan_request()
        draft = create_generation_task(
            self.conn,
            document_id="doc-1",
            request_id="request-1",
            plan_id="plan-1",
            ruleset_id="r1",
            class_id="c1",
            document_type="exercise",
            audience="student",
        )
        self.assertEqual(draft.question_ids, ("q1",))
        self.assertEqual(self.conn.execute("SELECT status FROM production_requests WHERE id='request-1'").fetchone()[0], "generating")

        bound = bind_generated_artifact(
            self.conn,
            request_id="request-1",
            document_id="doc-1",
            content_hash="artifact-hash",
            output_path="out/doc-1.docx",
        )
        self.assertEqual(bound.content_hash, "artifact-hash")
        self.assertEqual(self.conn.execute("SELECT status FROM teaching_documents WHERE id='doc-1'").fetchone()[0], "pending_review")
        self.assertEqual(self.conn.execute("SELECT status FROM production_requests WHERE id='request-1'").fetchone()[0], "validating")

        report = record_quality_report(
            self.conn,
            quality_report_id="qr-1",
            document_id="doc-1",
            data_gate="pass",
            rule_gate="pass",
            fidelity_gate="pass",
            artifact_gate="pass",
            findings=[],
            blockers=[],
            artifact_evidence=self._artifact_evidence(),
        )
        self.assertTrue(report.passed)
        self.assertEqual(self.conn.execute("SELECT status FROM teaching_documents WHERE id='doc-1'").fetchone()[0], "passed")
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='request-1'")

        delivered = deliver_document(
            self.conn,
            request_id="request-1",
            document_id="doc-1",
            usage_rows=[
                {
                    "id": "usage-1",
                    "question_id": "q1",
                    "class_id": "c1",
                    "section": "待编排",
                    "usage_type": "exercise",
                }
            ],
        )
        self.assertEqual(delivered.usage_ids, ("usage-1",))
        self.assertEqual(self.conn.execute("SELECT status FROM teaching_documents WHERE id='doc-1'").fetchone()[0], "delivered")
        self.assertEqual(self.conn.execute("SELECT status FROM production_requests WHERE id='request-1'").fetchone()[0], "delivered")
        self.assertEqual(self.conn.execute("SELECT delivered FROM question_usage WHERE id='usage-1'").fetchone()[0], 1)

    def test_plan_selection_blocks_question_outside_active_progress(self):
        self._insert_approved_question("q3")
        result = self._plan_request(count=1)
        self.assertEqual(result.result.shortage, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM selection_plan_questions WHERE selection_plan_id='plan-1'").fetchone()[0], 0)

    def test_generation_rechecks_plan_against_current_progress(self):
        self._insert_approved_question()
        self._plan_request()
        set_active_progress(
            self.conn,
            progress_id="progress-2",
            class_id="c1",
            current_curriculum_node_id="node-prereq",
            allowed_curriculum_node_ids=["node-prereq"],
        )
        with self.assertRaises(ProgressControlError):
            create_generation_task(
                self.conn,
                document_id="doc-recheck",
                request_id="request-1",
                plan_id="plan-1",
                ruleset_id="r1",
                class_id="c1",
                document_type="exercise",
                audience="student",
            )

    def test_delivery_requires_per_question_reuse_authorization_rows(self):
        self._insert_approved_question()
        self._plan_request()
        create_generation_task(
            self.conn,
            document_id="doc-reuse",
            request_id="request-1",
            plan_id="plan-1",
            ruleset_id="r1",
            class_id="c1",
            document_type="exercise",
            audience="student",
        )
        bind_generated_artifact(
            self.conn,
            request_id="request-1",
            document_id="doc-reuse",
            content_hash="artifact-reuse",
        )
        record_quality_report(
            self.conn,
            quality_report_id="qr-reuse",
            document_id="doc-reuse",
            data_gate="pass",
            rule_gate="pass",
            fidelity_gate="pass",
            artifact_gate="pass",
            artifact_evidence=self._artifact_evidence(),
        )
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='request-1'")
        self.conn.execute(
            """INSERT INTO production_requests
            (id, raw_request, request_type, parsed_spec_json, class_id, status)
            VALUES ('request-old', '旧交付', 'exercise', '{}', 'c1', 'selected')"""
        )
        self.conn.execute(
            """INSERT INTO selection_plans
            (id, request_id, ruleset_id, filters_json, requirements_json, shortages_json, warnings_json, status)
            VALUES ('plan-old', 'request-old', 'r1', '{}', '{"count": 1}', '[]', '[]', 'ready')"""
        )
        self.conn.execute(
            """INSERT INTO selection_plan_questions
            (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
            VALUES ('plan-old', 'q1', '待编排', NULL, 1, '旧交付')"""
        )
        self.conn.execute(
            """INSERT INTO teaching_documents
            (id, request_id, selection_plan_id, ruleset_id, class_id, document_type, audience, status)
            VALUES ('doc-old', 'request-old', 'plan-old', 'r1', 'c1', 'exercise', 'student', 'draft')"""
        )
        self.conn.execute(
            "INSERT INTO document_questions (document_id, question_id, section, layer, sort_order) VALUES ('doc-old', 'q1', '待编排', NULL, 1)"
        )
        self.conn.execute("UPDATE production_requests SET status='generating' WHERE id='request-old'")
        self.conn.execute("UPDATE teaching_documents SET content_hash='old-hash', status='pending_review' WHERE id='doc-old'")
        self.conn.execute("UPDATE production_requests SET status='validating' WHERE id='request-old'")
        self.conn.execute(
            """INSERT INTO quality_reports
            (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, findings_json, blockers_json, status)
            VALUES ('qr-old', 'doc-old', 'pass', 'pass', 'pass', 'pass', '[]', '[]', 'pass')"""
        )
        self.conn.execute(
            """INSERT INTO artifact_validation_evidence
            (id, document_id, quality_report_id, artifact_role, content_hash, question_set_hash, snapshot_hash, manifest_hash, artifact_hash, gate, checks_json, findings_json)
            VALUES ('ev-old', 'doc-old', 'qr-old', 'docx_student', 'old-hash', ?, ?, 'mh', 'ah', 'pass', '[]', '[]')""",
            (
                self.conn.execute("SELECT group_concat(question_id, '|') FROM (SELECT question_id FROM document_questions WHERE document_id='doc-old' ORDER BY sort_order ASC, question_id ASC)").fetchone()[0],
                self.conn.execute("SELECT group_concat(question_id || ':' || input_hash, '|') FROM (SELECT dq.question_id AS question_id, qis.input_hash AS input_hash FROM document_questions dq JOIN question_input_snapshots qis ON qis.question_id=dq.question_id WHERE dq.document_id='doc-old' AND qis.input_hash IS NOT NULL AND qis.invalidated_at IS NULL ORDER BY dq.sort_order ASC, dq.question_id ASC)").fetchone()[0],
            ),
        )
        self.conn.execute("UPDATE teaching_documents SET status='passed' WHERE id='doc-old'")
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='request-old'")
        self.conn.execute(
            """INSERT INTO question_usage
            (id, question_id, class_id, document_id, section, usage_type, delivered, reuse_allowed, reuse_reason)
            VALUES ('usage-old', 'q1', 'c1', 'doc-old', '待编排', 'exercise', 1, 0, NULL)"""
        )
        self.conn.commit()
        with self.assertRaises(TypeError):
            deliver_document(
                self.conn,
                request_id="request-1",
                document_id="doc-reuse",
                usage_rows=[
                    {
                        "id": "usage-new",
                        "question_id": "q1",
                        "class_id": "c1",
                        "section": "待编排",
                        "usage_type": "exercise",
                    }
                ],
            )

    def test_cannot_create_generation_task_without_approved_current_snapshot_question(self):
        self.conn.execute(
            """INSERT INTO questions (id, stem, question_type, stage, source_document_id, content_hash, quality_status, review_status)
            VALUES ('q-bad', '题干', '选择题', '初中', 's1', 'hash-bad', 'needs_review', 'pending')"""
        )
        self.conn.execute(
            "INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status) VALUES ('q-bad', 't1', NULL, 'approved')"
        )
        self.conn.execute(
            """INSERT INTO production_requests (id, raw_request, request_type, parsed_spec_json, class_id, status)
            VALUES ('req-bad', 'request', 'exercise', '{}', 'c1', 'selecting')"""
        )
        self.conn.execute(
            """INSERT INTO selection_plans (id, request_id, ruleset_id, filters_json, requirements_json, shortages_json, warnings_json, status)
            VALUES ('plan-bad', 'req-bad', 'r1', '{}', '{\"count\": 1}', '[]', '[]', 'ready')"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO selection_plan_questions
                (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
                VALUES ('plan-bad', 'q-bad', '待编排', NULL, 1, '绕过')"""
            )

    def test_quality_failure_blocks_document_and_prevents_delivery(self):
        self._insert_approved_question()
        self._plan_request()
        create_generation_task(
            self.conn,
            document_id="doc-2",
            request_id="request-1",
            plan_id="plan-1",
            ruleset_id="r1",
            class_id="c1",
            document_type="exercise",
            audience="student",
        )
        bind_generated_artifact(
            self.conn,
            request_id="request-1",
            document_id="doc-2",
            content_hash="artifact-hash-2",
        )
        report = record_quality_report(
            self.conn,
            quality_report_id="qr-2",
            document_id="doc-2",
            data_gate="pass",
            rule_gate="fail",
            fidelity_gate="pass",
            artifact_gate="pass",
            findings=[],
            blockers=["rule_gate"],
            artifact_evidence=self._artifact_evidence(),
        )
        self.assertFalse(report.passed)
        self.assertEqual(self.conn.execute("SELECT status FROM teaching_documents WHERE id='doc-2'").fetchone()[0], "blocked")
        with self.assertRaises((sqlite3.IntegrityError, ProgressControlError)):
            deliver_document(
                self.conn,
                request_id="request-1",
                document_id="doc-2",
                usage_rows=[
                    {
                        "id": "usage-2",
                        "question_id": "q1",
                        "class_id": "c1",
                    }
                ],
            )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_usage WHERE document_id='doc-2'").fetchone()[0], 0)

    def test_delivery_transaction_rolls_back_usage_on_duplicate_insert(self):
        self._insert_approved_question()
        self._plan_request()
        create_generation_task(
            self.conn,
            document_id="doc-3",
            request_id="request-1",
            plan_id="plan-1",
            ruleset_id="r1",
            class_id="c1",
            document_type="exercise",
            audience="student",
        )
        bind_generated_artifact(
            self.conn,
            request_id="request-1",
            document_id="doc-3",
            content_hash="artifact-hash-3",
        )
        record_quality_report(
            self.conn,
            quality_report_id="qr-3",
            document_id="doc-3",
            data_gate="pass",
            rule_gate="pass",
            fidelity_gate="pass",
            artifact_gate="pass",
            artifact_evidence=self._artifact_evidence(),
        )
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='request-1'")
        with self.assertRaises(ProgressControlError):
            deliver_document(
                self.conn,
                request_id="request-1",
                document_id="doc-3",
                usage_rows=[
                    {
                        "id": "usage-3",
                        "question_id": "q1",
                        "class_id": "c1",
                    },
                    {
                        "id": "usage-3-dup",
                        "question_id": "q1",
                        "class_id": "c1",
                    },
                ],
            )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_usage WHERE document_id='doc-3'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM teaching_documents WHERE id='doc-3'").fetchone()[0], "passed")
        self.assertEqual(self.conn.execute("SELECT status FROM production_requests WHERE id='request-1'").fetchone()[0], "validating")


    def test_delivery_blocked_when_content_hash_changes_after_pass(self):
        self._insert_approved_question()
        self._plan_request()
        create_generation_task(
            self.conn,
            document_id="doc-hash",
            request_id="request-1",
            plan_id="plan-1",
            ruleset_id="r1",
            class_id="c1",
            document_type="exercise",
            audience="student",
        )
        bind_generated_artifact(self.conn, request_id="request-1", document_id="doc-hash", content_hash="hash-a")
        record_quality_report(
            self.conn,
            quality_report_id="qr-hash",
            document_id="doc-hash",
            data_gate="pass",
            rule_gate="pass",
            fidelity_gate="pass",
            artifact_gate="pass",
            artifact_evidence=self._artifact_evidence(),
        )
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='request-1'")
        self.conn.execute("UPDATE teaching_documents SET content_hash='hash-b' WHERE id='doc-hash'")
        with self.assertRaises(ProgressControlError):
            deliver_document(
                self.conn,
                request_id="request-1",
                document_id="doc-hash",
                usage_rows=[{"id": "usage-hash", "question_id": "q1", "class_id": "c1"}],
            )

    def test_delivery_blocked_when_snapshot_changes_after_pass(self):
        self._insert_approved_question()
        self._plan_request()
        create_generation_task(
            self.conn,
            document_id="doc-snapshot",
            request_id="request-1",
            plan_id="plan-1",
            ruleset_id="r1",
            class_id="c1",
            document_type="exercise",
            audience="student",
        )
        bind_generated_artifact(self.conn, request_id="request-1", document_id="doc-snapshot", content_hash="hash-s")
        record_quality_report(
            self.conn,
            quality_report_id="qr-snapshot",
            document_id="doc-snapshot",
            data_gate="pass",
            rule_gate="pass",
            fidelity_gate="pass",
            artifact_gate="pass",
            artifact_evidence=self._artifact_evidence(),
        )
        self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='request-1'")
        self.conn.execute("UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id='q1'")
        self.conn.execute("UPDATE questions SET stem='变更后题干' WHERE id='q1'")
        with self.assertRaises(ProgressControlError):
            deliver_document(
                self.conn,
                request_id="request-1",
                document_id="doc-snapshot",
                usage_rows=[{"id": "usage-snapshot", "question_id": "q1", "class_id": "c1"}],
            )

    def test_pdf_is_blocked_as_unsupported(self):
        self._insert_approved_question()
        self._plan_request()
        create_generation_task(
            self.conn,
            document_id="doc-pdf",
            request_id="request-1",
            plan_id="plan-1",
            ruleset_id="r1",
            class_id="c1",
            document_type="pdf",
            audience="student",
        )
        bind_generated_artifact(self.conn, request_id="request-1", document_id="doc-pdf", content_hash="hash-pdf")
        report = record_quality_report(
            self.conn,
            quality_report_id="qr-pdf",
            document_id="doc-pdf",
            data_gate="pass",
            rule_gate="pass",
            fidelity_gate="pass",
            artifact_gate="unsupported",
            artifact_evidence=None,
        )
        self.assertFalse(report.passed)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE production_requests SET status='passed' WHERE id='request-1'")

    def test_missing_artifact_evidence_blocks_delivery(self):
        self._insert_approved_question()
        self._plan_request()
        create_generation_task(
            self.conn,
            document_id="doc-missing",
            request_id="request-1",
            plan_id="plan-1",
            ruleset_id="r1",
            class_id="c1",
            document_type="exercise",
            audience="teacher",
        )
        bind_generated_artifact(self.conn, request_id="request-1", document_id="doc-missing", content_hash="hash-m")
        report = record_quality_report(
            self.conn,
            quality_report_id="qr-missing",
            document_id="doc-missing",
            data_gate="pass",
            rule_gate="pass",
            fidelity_gate="pass",
            artifact_gate="missing",
            artifact_evidence=None,
        )
        self.assertFalse(report.passed)
        self.assertEqual(self.conn.execute("SELECT gate FROM artifact_validation_evidence WHERE quality_report_id='qr-missing'").fetchone()[0], 'missing')

if __name__ == "__main__":
    unittest.main()
