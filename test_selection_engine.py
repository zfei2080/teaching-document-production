"""Regression tests for class-scoped question selection."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automatic_gate import AutomaticAdmissionResult
from progress_control import ProgressControlError, set_active_progress
from selection_engine import SelectionRequest, select_approved_questions

ROOT = Path(__file__).parent


class SelectionEngineTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in (
            "schema_v2.sql",
            "schema_v2_1.sql",
            "schema_v2_2.sql",
            "schema_v2_3.sql",
            "schema_v2_4.sql",
        ):
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self._create_progress_contract_fixture()
        self._seed()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _create_progress_contract_fixture(self):
        self.conn.executescript(
            """
            CREATE TABLE class_progress_controls (
                id TEXT PRIMARY KEY,
                class_id TEXT NOT NULL,
                textbook_id TEXT NOT NULL,
                current_curriculum_node_id TEXT NOT NULL,
                allowed_nodes_json TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE class_progress_allowed_nodes (
                progress_id TEXT NOT NULL,
                curriculum_node_id TEXT NOT NULL
            );
            CREATE TABLE question_reuse_authorizations (
                id TEXT PRIMARY KEY,
                class_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                question_id TEXT NOT NULL,
                progress_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                invalidated_at TEXT,
                invalidation_reason TEXT,
                used_at TEXT,
                used_usage_id TEXT,
                UNIQUE (class_id, request_id, question_id)
            );

            CREATE TRIGGER trg_reuse_authorization_insert_guard
            BEFORE INSERT ON question_reuse_authorizations
            FOR EACH ROW
            BEGIN
                SELECT CASE WHEN NEW.status <> 'active'
                    THEN RAISE(ABORT, 'reuse authorization must be inserted as active') END;
                SELECT CASE WHEN NOT EXISTS (
                    SELECT 1 FROM class_progress_controls cpc
                    WHERE cpc.id = NEW.progress_id AND cpc.class_id = NEW.class_id AND cpc.status = 'active'
                ) THEN RAISE(ABORT, 'reuse authorization requires current active class progress') END;
                SELECT CASE WHEN NOT EXISTS (
                    SELECT 1 FROM production_requests pr
                    WHERE pr.id = NEW.request_id AND pr.class_id = NEW.class_id AND pr.status IN ('selected', 'generating', 'validating', 'passed')
                ) THEN RAISE(ABORT, 'reuse authorization requires same-class live production request') END;
            END;

            CREATE TRIGGER trg_reuse_authorization_update_guard
            BEFORE UPDATE ON question_reuse_authorizations
            FOR EACH ROW
            BEGIN
                SELECT CASE WHEN NEW.class_id <> OLD.class_id OR NEW.request_id <> OLD.request_id OR NEW.question_id <> OLD.question_id OR NEW.progress_id <> OLD.progress_id
                    THEN RAISE(ABORT, 'reuse authorization identity is immutable') END;
            END;

            CREATE TRIGGER trg_reuse_authorization_delete_guard
            BEFORE DELETE ON question_reuse_authorizations
            FOR EACH ROW
            BEGIN
                SELECT RAISE(ABORT, 'reuse authorization cannot be deleted');
            END;

            CREATE TRIGGER trg_progress_change_invalidates_prior_state
            AFTER INSERT ON class_progress_controls
            FOR EACH ROW
            WHEN EXISTS (
                SELECT 1 FROM class_progress_controls prior
                WHERE prior.class_id = NEW.class_id
                  AND prior.id <> NEW.id
                  AND prior.status IN ('active', 'replaced')
            )
            BEGIN
                UPDATE question_reuse_authorizations
                   SET status = 'invalidated',
                       invalidated_at = CURRENT_TIMESTAMP,
                       invalidation_reason = 'progress_changed'
                 WHERE class_id = NEW.class_id
                   AND status = 'active'
                   AND progress_id <> NEW.id;
            END;
            """
        )

    def _seed(self):
        self.conn.execute(
            "INSERT INTO textbooks (id, name, catalog_version) VALUES ('textbook-a', '测试教材', 'v1')"
        )
        for node_id in ("node-current", "node-prereq", "node-future"):
            self.conn.execute(
                """INSERT INTO curriculum_nodes
                (id, textbook_id, stage, grade_level, node_type, name, sequence, catalog_version, status)
                VALUES (?, 'textbook-a', '初中', '八下', 'topic', ?, 1, 'v1', 'active')""",
                (node_id, node_id),
            )
        self.conn.execute(
            "INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('source', 'source.docx', 'hash', 'docx')"
        )
        for class_id in ("class-a", "class-b"):
            self.conn.execute(
                "INSERT INTO classes (id, name, textbook_id, grade_level) VALUES (?, ?, 'textbook-a', '八下')",
                (class_id, class_id),
            )
        self.conn.execute(
            """INSERT INTO rule_sets (id, document_type, purpose, grade_scope, rules_json, version, status)
            VALUES ('rules', 'exercise', '同步巩固', '初中', '{}', 'v1', 'active')"""
        )
        for question_id in ("q1", "q2", "q3"):
            node_id = {"q1": "node-current", "q2": "node-prereq", "q3": "node-future"}[question_id]
            self.conn.execute(
                """INSERT INTO questions
                (id, stem, question_type, difficulty, stage, source_document_id, content_hash, quality_status, review_status)
                VALUES (?, ?, '选择题', '基础', '初中', 'source', ?, 'approved', 'approved')""",
                (question_id, f"题目 {question_id}", f"hash-{question_id}"),
            )
            self.conn.execute(
                "INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status) VALUES (?, 'textbook-a', ?, 'approved')",
                (question_id, node_id),
            )
            for verification_type in (
                'source_fidelity', 'structural_consistency', 'mathematical_independent',
                'textbook_scope', 'asset_semantics',
            ):
                self.conn.execute(
                    """INSERT INTO question_verifications
                    (id, question_id, verification_type, validator_id, validator_version,
                     status, evidence_json, input_hash)
                    VALUES (?, ?, ?, 'test-validator', 'v1', 'pass', ?, ?)""",
                    (
                        f"{question_id}-{verification_type}", question_id, verification_type,
                        json.dumps({"test": True}), f"{question_id}-{verification_type}-hash",
                    ),
                )
        set_active_progress(
            self.conn,
            progress_id="progress-class-a",
            class_id="class-a",
            current_curriculum_node_id="node-current",
            allowed_curriculum_node_ids=["node-current", "node-prereq"],
        )
        set_active_progress(
            self.conn,
            progress_id="progress-class-b",
            class_id="class-b",
            current_curriculum_node_id="node-current",
            allowed_curriculum_node_ids=["node-current", "node-prereq", "node-future"],
        )
        self.conn.commit()

    def _record_delivered_usage(self, question_id: str, class_id: str):
        request_id = f"request-{class_id}-{question_id}"
        document_id = f"document-{class_id}-{question_id}"
        self.conn.execute(
            "INSERT INTO production_requests (id, raw_request, request_type, class_id, status) VALUES (?, 'test', 'exercise', ?, 'delivered')",
            (request_id, class_id),
        )
        self.conn.execute(
            """INSERT INTO teaching_documents
            (id, request_id, ruleset_id, class_id, document_type, audience, status)
            VALUES (?, ?, 'rules', ?, 'exercise', 'student', 'delivered')""",
            (document_id, request_id, class_id),
        )
        self.conn.execute(
            """INSERT INTO question_usage
            (id, question_id, class_id, document_id, usage_type, delivered)
            VALUES (?, ?, ?, ?, 'exercise', 1)""",
            (f"usage-{class_id}-{question_id}", question_id, class_id, document_id),
        )
        self.conn.commit()

    def request(self, class_id="class-a", count=2):
        return SelectionRequest(
            class_id=class_id,
            textbook_id="textbook-a",
            stage="初中",
            count=count,
            question_types=("选择题",),
        )

    def test_same_class_delivered_question_is_excluded(self):
        self._record_delivered_usage("q1", "class-a")
        result = select_approved_questions(self.conn, self.request(count=3))
        self.assertEqual(result.question_ids, ("q2",))
        self.assertEqual(result.shortage, 2)
        self.assertTrue(result.blockers)

    def test_different_class_can_reuse_question(self):
        self._record_delivered_usage("q1", "class-a")
        result = select_approved_questions(self.conn, self.request(class_id="class-b", count=3))
        self.assertEqual(result.question_ids, ("q1", "q2", "q3"))
        self.assertEqual(result.shortage, 0)

    def test_same_class_reuse_requires_per_question_authorization(self):
        self._record_delivered_usage("q1", "class-a")
        result = select_approved_questions(self.conn, self.request(count=2))
        self.assertEqual(result.question_ids, ("q2",))
        self.assertEqual(result.shortage, 1)
        self.conn.execute(
            "INSERT INTO production_requests (id, raw_request, request_type, class_id, status) VALUES ('request-reuse-class-a', 'test', 'exercise', 'class-a', 'selected')"
        )
        self.conn.execute(
            """INSERT INTO question_reuse_authorizations
            (id, class_id, request_id, question_id, progress_id, reason, status)
            VALUES ('auth-1', 'class-a', 'request-reuse-class-a', 'q1', 'progress-class-a', '错题订正', 'active')"""
        )
        self.conn.commit()
        with self.assertRaises(TypeError):
            select_approved_questions(self.conn, self.request(count=2))

    def test_selection_is_filtered_by_active_progress_allowed_nodes(self):
        result = select_approved_questions(self.conn, self.request(count=3))
        self.assertEqual(result.question_ids, ("q1", "q2"))
        self.assertEqual(result.shortage, 1)

    @patch("selection_engine.evaluate_automatic_admission")
    @patch("selection_engine.requires_live_p1_3c_scope_recheck")
    def test_selection_skips_live_p1_3c_candidate_that_no_longer_passes(
        self, requires_recheck, evaluate
    ):
        requires_recheck.side_effect = lambda _connection, question_id: question_id == "q1"
        evaluate.return_value = AutomaticAdmissionResult(
            eligible=False,
            missing_or_nonpassing=("textbook_scope",),
            blockers=("textbook_scope_live:unsupported",),
        )

        result = select_approved_questions(self.conn, self.request(count=2))

        self.assertEqual(result.question_ids, ("q2",))
        self.assertEqual(result.shortage, 1)
        evaluate.assert_called_once_with(self.conn, "q1")

    def test_selection_blocks_when_progress_contract_tables_are_missing(self):
        broken = sqlite3.connect(":memory:")
        broken.execute("PRAGMA foreign_keys=ON")
        for schema in (
            "schema_v2.sql",
            "schema_v2_1.sql",
            "schema_v2_2.sql",
            "schema_v2_3.sql",
            "schema_v2_4.sql",
        ):
            broken.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self._seed_into(broken)
        with self.assertRaisesRegex(Exception, "class_progress_controls"):
            select_approved_questions(broken, self.request(count=1))
        broken.close()

    def _seed_into(self, conn: sqlite3.Connection):
        conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('textbook-a', '测试教材', 'v1')")
        conn.execute(
            """INSERT INTO curriculum_nodes
            (id, textbook_id, stage, grade_level, node_type, name, sequence, catalog_version, status)
            VALUES ('node-current', 'textbook-a', '初中', '八下', 'topic', 'node-current', 1, 'v1', 'active')"""
        )
        conn.execute("INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('source', 'source.docx', 'hash', 'docx')")
        conn.execute("INSERT INTO classes (id, name, textbook_id, grade_level) VALUES ('class-a', 'class-a', 'textbook-a', '八下')")
        conn.execute(
            """INSERT INTO questions
            (id, stem, question_type, difficulty, stage, source_document_id, content_hash, quality_status, review_status)
            VALUES ('q1', '题目 q1', '选择题', '基础', '初中', 'source', 'hash-q1', 'approved', 'approved')"""
        )
        conn.execute(
            "INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status) VALUES ('q1', 'textbook-a', 'node-current', 'approved')"
        )
        for verification_type in (
            'source_fidelity', 'structural_consistency', 'mathematical_independent',
            'textbook_scope', 'asset_semantics',
        ):
            conn.execute(
                """INSERT INTO question_verifications
                (id, question_id, verification_type, validator_id, validator_version,
                 status, evidence_json, input_hash)
                VALUES (?, 'q1', ?, 'test-validator', 'v1', 'pass', ?, ?)""",
                (
                    f"q1-{verification_type}", verification_type,
                    json.dumps({"test": True}), f"q1-{verification_type}-hash",
                ),
            )
        conn.commit()

    def test_missing_or_unsupported_automatic_evidence_is_never_selected(self):
        self.conn.execute("DELETE FROM question_verifications WHERE question_id='q2' AND verification_type='mathematical_independent'")
        self.conn.execute("UPDATE question_verifications SET status='unsupported' WHERE question_id='q3' AND verification_type='asset_semantics'")
        self.conn.commit()
        result = select_approved_questions(self.conn, self.request(count=3))
        self.assertEqual(result.question_ids, ("q1",))
        self.assertEqual(result.shortage, 2)

    def test_pending_or_wrong_textbook_questions_are_never_selected(self):
        self.conn.execute("UPDATE questions SET quality_status='needs_review' WHERE id='q3'")
        self.conn.execute("UPDATE question_textbooks SET fit_status='pending' WHERE question_id='q2'")
        self.conn.commit()
        result = select_approved_questions(self.conn, self.request(count=2))
        self.assertEqual(result.question_ids, ("q1",))
        self.assertEqual(result.shortage, 1)


if __name__ == "__main__":
    unittest.main()
