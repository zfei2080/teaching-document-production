"""Temporary-database checks for v2.22 draft mapping evidence."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apply_schema_v2_16 import apply_schema as apply_schema_v2_16
from apply_schema_v2_17 import apply_schema as apply_schema_v2_17
from apply_schema_v2_18 import apply_schema as apply_schema_v2_18
from apply_schema_v2_19 import apply_schema as apply_schema_v2_19
from apply_schema_v2_20 import apply as apply_schema_v2_20
from apply_schema_v2_21 import apply as apply_schema_v2_21
from apply_schema_v2_22 import MIGRATION, apply as apply_schema_v2_22
from input_snapshot import refresh_current_snapshot
from question_curriculum_mapping_evidence import MappingEvidence, current_evidence, record_mapping_evidence


ROOT = Path(__file__).parent
BASE_SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15")
)


class QuestionCurriculumMappingEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "mapping_evidence.db"
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            for schema in BASE_SCHEMAS:
                conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()
        for apply in (apply_schema_v2_16, apply_schema_v2_17, apply_schema_v2_18, apply_schema_v2_19, apply_schema_v2_20, apply_schema_v2_21, apply_schema_v2_22):
            apply(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("""INSERT INTO source_documents
            (id, relative_path, file_hash, file_type, original_relative_path,
             original_file_hash, trusted_source)
            VALUES ('source', 'source.docx', 'source-hash', 'docx', 'original.docx',
                    'source-hash', 1)""")
        self.conn.execute("INSERT INTO source_fragments (id, source_document_id, location_type, raw_text, raw_hash) VALUES ('fragment', 'source', 'paragraph', '原题', 'fragment-hash')")
        self.conn.execute("""INSERT INTO questions
            (id, stem, question_type, stage, source_document_id, source_fragment_id, content_hash)
            VALUES ('question', '计算 1+1', '填空题', '初中', 'source', 'fragment', 'question-hash')""")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('book', '教材', 'catalog-v1')")
        self.conn.execute("""INSERT INTO curriculum_nodes
            (id, textbook_id, stage, grade_level, node_type, name, catalog_version)
            VALUES ('node', 'book', '初中', '七年级', 'topic', '有理数', 'catalog-v1')""")
        self.conn.execute("""INSERT INTO controlled_import_runs
            (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status)
            VALUES ('catalog-import', 'catalog', 'fixture://catalog', 'catalog-hash',
                    'manifest-hash', 'fixture', '1', 'validated')""")
        self.conn.execute("""INSERT INTO catalog_releases
            (id, textbook_id, catalog_version, source_reference, source_hash, status)
            VALUES ('catalog-release', 'book', 'catalog-v1', 'fixture://catalog', 'catalog-hash', 'draft')""")
        self.conn.execute("""INSERT INTO catalog_audits
            (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id,
             audit_method, source_reference, source_hash, status)
            VALUES ('catalog-audit', 'catalog-release', 'catalog-import', 'audit-hash',
                    'fixture', 'fixture', 'fixture://catalog', 'catalog-hash', 'approved')""")
        self.conn.execute("UPDATE catalog_releases SET status='approved' WHERE id='catalog-release'")
        refresh_current_snapshot(self.conn, "question")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def _draft(self, status: str = "candidate", reason: str = "特征与节点术语一致") -> MappingEvidence:
        return MappingEvidence(
            question_id="question", textbook_id="book", curriculum_node_id="node",
            features={"tokens": ["计算", "1+1"], "stage": "初中", "decision_case": status},
            decider_id="controlled-mapping-rules", decider_version="1.0.0",
            status=status, confidence=0.82, reason=reason,
        )

    def test_draft_statuses_are_deterministic_and_never_promote_mappings(self) -> None:
        candidate = record_mapping_evidence(self.conn, self._draft())
        with self.assertRaisesRegex(ValueError, "duplicate evidence"):
            record_mapping_evidence(self.conn, self._draft())
        unsupported = record_mapping_evidence(self.conn, self._draft("unsupported", "缺少决定性特征"))
        rejected = record_mapping_evidence(self.conn, self._draft("rejected", "题目学段不匹配"))
        self.assertTrue(all(current_evidence(self.conn, item) for item in (candidate, unsupported, rejected)))
        self.assertEqual(
            {row[0] for row in self.conn.execute("SELECT status FROM question_curriculum_mapping_evidence")},
            {"candidate", "unsupported", "rejected"},
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbooks WHERE fit_status='approved'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT quality_status, review_status FROM questions WHERE id='question'").fetchone(), ("pending", "pending"))

    def test_conflicting_result_for_identical_decision_key_is_rejected(self) -> None:
        candidate = self._draft()
        record_mapping_evidence(self.conn, candidate)
        conflict = MappingEvidence(
            question_id=candidate.question_id,
            textbook_id=candidate.textbook_id,
            curriculum_node_id=candidate.curriculum_node_id,
            features=candidate.features,
            decider_id=candidate.decider_id,
            decider_version=candidate.decider_version,
            status="rejected",
            confidence=candidate.confidence,
            reason="同一输入不能产生第二个结论",
        )
        with self.assertRaisesRegex(ValueError, "conflicting evidence"):
            record_mapping_evidence(self.conn, conflict)

    def test_question_change_invalidates_evidence_and_fails_closed_until_new_snapshot(self) -> None:
        evidence_id = record_mapping_evidence(self.conn, self._draft())
        self.conn.execute("UPDATE questions SET stem='已修改题干' WHERE id='question'")
        self.assertFalse(current_evidence(self.conn, evidence_id))
        invalidated = self.conn.execute("SELECT invalidation_reason FROM question_curriculum_mapping_evidence WHERE id=?", (evidence_id,)).fetchone()[0]
        self.assertEqual(invalidated, "question_changed")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE question_curriculum_mapping_evidence SET invalidated_at=NULL, invalidation_reason=NULL WHERE id=?", (evidence_id,))
        with self.assertRaises(ValueError):
            record_mapping_evidence(self.conn, self._draft())
        refresh_current_snapshot(self.conn, "question")
        self.assertNotEqual(evidence_id, record_mapping_evidence(self.conn, self._draft()))

    def test_catalog_change_invalidates_evidence_and_rejects_stale_direct_insert(self) -> None:
        evidence_id = record_mapping_evidence(self.conn, self._draft())
        self.conn.execute("UPDATE curriculum_nodes SET name='有理数运算', catalog_version='catalog-v2' WHERE id='node'")
        self.assertFalse(current_evidence(self.conn, evidence_id))
        row = self.conn.execute("SELECT invalidation_reason FROM question_curriculum_mapping_evidence WHERE id=?", (evidence_id,)).fetchone()
        self.assertEqual(row[0], "curriculum_node_changed")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO question_curriculum_mapping_evidence
                (id, question_id, textbook_id, curriculum_node_id, question_input_hash,
                 question_snapshot_revision, textbook_catalog_version, node_catalog_version,
                 feature_hash, features_json, decider_id, decider_version, status, confidence,
                 reason, evidence_hash)
                SELECT 'forged', question_id, textbook_id, curriculum_node_id, question_input_hash,
                       question_snapshot_revision, textbook_catalog_version, node_catalog_version,
                       feature_hash, features_json, decider_id, decider_version, status, confidence,
                       reason, 'forged-hash'
                FROM question_curriculum_mapping_evidence WHERE id=?""", (evidence_id,))

    def test_untrusted_source_or_unapproved_release_fails_closed_without_writes(self) -> None:
        evidence_id = record_mapping_evidence(self.conn, self._draft())
        self.conn.execute("UPDATE source_documents SET trusted_source=0 WHERE id='source'")
        self.assertFalse(current_evidence(self.conn, evidence_id))
        with self.assertRaisesRegex(ValueError, "trusted question source"):
            record_mapping_evidence(self.conn, self._draft())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_curriculum_mapping_evidence").fetchone()[0], 1)
        self.conn.execute("UPDATE source_documents SET trusted_source=1 WHERE id='source'")
        refresh_current_snapshot(self.conn, "question")
        self.conn.execute("UPDATE catalog_releases SET status='draft' WHERE id='catalog-release'")
        self.assertFalse(current_evidence(self.conn, evidence_id))
        with self.assertRaisesRegex(ValueError, "approved catalog release"):
            record_mapping_evidence(self.conn, self._draft())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_curriculum_mapping_evidence").fetchone()[0], 1)

    def test_migration_is_idempotent(self) -> None:
        apply_schema_v2_22(self.path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
