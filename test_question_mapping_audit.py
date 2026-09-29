"""Regression tests for source-bound question-mapping audit and approval."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from controlled_question_mapping_import import import_question_mappings
from question_mapping_audit import QuestionMappingAuditError, record_question_mapping_audit

ROOT = Path(__file__).parent
SCHEMAS = (
    "schema_v2.sql", "schema_v2_5.sql", "schema_v2_6.sql", "schema_v2_7.sql",
    "schema_v2_8.sql", "schema_v2_9.sql", "schema_v2_10.sql",
)


class QuestionMappingAuditTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.conn = sqlite3.connect(self.root / "dev.db")
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self._seed_approved_catalog_and_knowledge_point()
        self.source_bytes = b"mapping evidence"
        self.source_hash = hashlib.sha256(self.source_bytes).hexdigest()
        self.source_file = self.root / "mapping-source.txt"
        self.source_file.write_bytes(self.source_bytes)
        self.mapping_path = self.root / "mapping.json"
        self.mapping_path.write_text(json.dumps({
            "schema_version": "controlled-question-mapping-manifest-v1",
            "source": {"reference": "fixture://mapping-source", "file": self.source_file.name, "sha256": self.source_hash},
            "mappings": [{"question_id": "q1", "textbook_id": "book", "curriculum_node_id": "node",
                          "knowledge_points": [{"knowledge_point_id": "kp", "relation_type": "primary"}]}],
        }, ensure_ascii=False), encoding="utf-8")
        self.import_result = import_question_mappings(self.conn, self.mapping_path)
        self.audit_path = self.root / "audit.json"
        self.write_audit_manifest()

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def _seed_approved_catalog_and_knowledge_point(self):
        c = self.conn
        c.execute("INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('source', 'source.docx', 'hash', 'docx')")
        c.execute("INSERT INTO questions (id, stem, question_type, stage, source_document_id, content_hash) VALUES ('q1', '题干', '选择题', '初中', 'source', 'content')")
        c.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('book', '教材', 'v1')")
        c.execute("INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, catalog_version) VALUES ('node', 'book', '初中', '八下', 'chapter', '三角形', 'v1')")
        c.execute("INSERT INTO catalog_releases (id, textbook_id, catalog_version, source_reference, source_hash, status) VALUES ('release', 'book', 'v1', 'catalog-source', 'catalog-hash', 'draft')")
        c.execute("INSERT INTO controlled_import_runs VALUES ('catalog-import', 'catalog', 'catalog-source', 'catalog-hash', 'catalog-manifest', 'fixture', 'v1', 'validated', CURRENT_TIMESTAMP)")
        c.execute("INSERT INTO catalog_release_imports VALUES ('release', 'catalog-import')")
        c.execute("INSERT INTO catalog_audits (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id, audit_method, source_reference, source_hash, status) VALUES ('catalog-audit', 'release', 'catalog-import', 'audit-hash', 'fixture', 'fixture', 'catalog-source', 'catalog-hash', 'approved')")
        c.execute("UPDATE catalog_releases SET status='approved' WHERE id='release'")
        c.execute("INSERT INTO knowledge_points (id, canonical_name, knowledge_type, stage_scope, version, review_status) VALUES ('kp', '全等三角形', 'criterion', '初中', 'v1', 'approved')")
        c.execute("INSERT INTO curriculum_knowledge_points VALUES ('node', 'kp', 'primary')")
        c.commit()

    def write_audit_manifest(self, **overrides):
        data = {
            "schema_version": "controlled-question-mapping-audit-v1",
            "import_run_id": self.import_result["import_id"],
            "mapping_hash": self.import_result["mapping_hash"],
            "source_reference": "fixture://mapping-source",
            "source_file": self.source_file.name,
            "source_sha256": self.source_hash,
            "auditor_id": "fixture-auditor",
            "findings": [
                {"check": "source-identity", "status": "pass", "evidence": f"source file {self.source_file.name} and import hashes match"},
                {"check": "scope-and-progress", "status": "pass", "evidence": "node and approved knowledge point checked"},
            ],
        }
        data.update(overrides)
        self.audit_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_mapping_cannot_be_directly_approved_without_audit(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE question_textbooks SET fit_status='approved'")
        self.assertEqual(self.conn.execute("SELECT fit_status FROM question_textbooks").fetchone()[0], "pending")

    def test_passing_source_bound_audit_approves_exact_mapping_batch(self):
        result = record_question_mapping_audit(self.conn, self.audit_path)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["approved_mappings"], 1)
        self.assertEqual(self.conn.execute("SELECT status FROM controlled_import_runs WHERE id=?", (self.import_result["import_id"],)).fetchone()[0], "approved")
        self.assertEqual(self.conn.execute("SELECT fit_status FROM question_textbooks").fetchone()[0], "approved")

    def test_source_mismatch_writes_nothing(self):
        self.write_audit_manifest(source_sha256="not-the-source")
        with self.assertRaisesRegex(QuestionMappingAuditError, "source identity"):
            record_question_mapping_audit(self.conn, self.audit_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_mapping_audits").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT fit_status FROM question_textbooks").fetchone()[0], "pending")

    def test_failed_finding_cannot_approve(self):
        self.write_audit_manifest(findings=[{"check": "scope", "status": "fail", "evidence": "future node"}])
        with self.assertRaisesRegex(QuestionMappingAuditError, "every finding passes"):
            record_question_mapping_audit(self.conn, self.audit_path)
        self.assertEqual(self.conn.execute("SELECT fit_status FROM question_textbooks").fetchone()[0], "pending")

    def test_knowledge_point_drift_blocks_batch_and_writes_nothing(self):
        self.conn.execute("DELETE FROM curriculum_knowledge_points")
        with self.assertRaisesRegex(QuestionMappingAuditError, "incomplete, drifted"):
            record_question_mapping_audit(self.conn, self.audit_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_mapping_audits").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM controlled_import_runs WHERE id=?", (self.import_result["import_id"],)).fetchone()[0], "validated")

    def test_missing_source_file_field_blocks_audit(self):
        self.write_audit_manifest(source_file="")
        with self.assertRaisesRegex(QuestionMappingAuditError, "source_file"):
            record_question_mapping_audit(self.conn, self.audit_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_mapping_audits").fetchone()[0], 0)

    def test_source_identity_finding_must_name_source_file(self):
        self.write_audit_manifest(findings=[
            {"check": "source-identity", "status": "pass", "evidence": "source and import hashes match"},
            {"check": "scope-and-progress", "status": "pass", "evidence": "node and approved knowledge point checked"},
        ])
        with self.assertRaisesRegex(QuestionMappingAuditError, "name the audited source file"):
            record_question_mapping_audit(self.conn, self.audit_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_mapping_audits").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
