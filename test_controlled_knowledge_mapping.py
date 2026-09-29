"""Regression tests for controlled knowledge mapping import and audit gate."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from controlled_knowledge_mapping_import import KnowledgeManifestError, import_knowledge_mapping
from knowledge_mapping_audit import KnowledgeMappingAuditError, record_knowledge_mapping_audit

ROOT = Path(__file__).parent
SCHEMAS = ("schema_v2.sql", "schema_v2_5.sql", "schema_v2_6.sql", "schema_v2_7.sql", "schema_v2_8.sql", "schema_v2_11.sql")


class ControlledKnowledgeMappingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.conn = sqlite3.connect(self.root / "dev.db")
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self._seed_approved_catalog()
        self.source_bytes = b"knowledge evidence"
        self.source_hash = hashlib.sha256(self.source_bytes).hexdigest()
        self.source_path = self.root / "knowledge-source.txt"
        self.source_path.write_bytes(self.source_bytes)
        self.manifest_path = self.root / "knowledge.json"
        self._write_manifest()
        self.result = import_knowledge_mapping(self.conn, self.manifest_path)
        self.audit_path = self.root / "audit.json"
        self._write_audit()

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def _seed_approved_catalog(self):
        c = self.conn
        c.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('book', '教材', 'v1')")
        c.execute("INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, catalog_version) VALUES ('node', 'book', '初中', '八下', 'chapter', '三角形证明', 'v1')")
        c.execute("INSERT INTO catalog_releases VALUES ('release', 'book', 'v1', 'catalog-source', 'catalog-hash', 'draft', CURRENT_TIMESTAMP)")
        c.execute("INSERT INTO controlled_import_runs VALUES ('catalog-import', 'catalog', 'catalog-source', 'catalog-hash', 'manifest', 'fixture', 'v1', 'validated', CURRENT_TIMESTAMP)")
        c.execute("INSERT INTO catalog_release_imports VALUES ('release', 'catalog-import')")
        c.execute("INSERT INTO catalog_audits (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id, audit_method, source_reference, source_hash, status) VALUES ('catalog-audit', 'release', 'catalog-import', 'audit-hash', 'fixture', 'fixture', 'catalog-source', 'catalog-hash', 'approved')")
        c.execute("UPDATE catalog_releases SET status='approved' WHERE id='release'")
        c.commit()

    def _write_manifest(self, **overrides):
        data = {"schema_version": "controlled-knowledge-mapping-manifest-v1", "catalog_release_id": "release", "source": {"reference": "fixture://knowledge-source", "file": "knowledge-source.txt", "sha256": self.source_hash}, "knowledge_points": [{"id": "kp", "canonical_name": "全等三角形", "knowledge_type": "criterion", "stage_scope": "初中", "version": "v1", "definition_text": "判定条件"}], "curriculum_knowledge_points": [{"curriculum_node_id": "node", "knowledge_point_id": "kp", "relation_type": "primary"}]}
        data.update(overrides)
        self.manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _write_audit(self, **overrides):
        data = {"schema_version": "controlled-knowledge-mapping-audit-v1", "import_run_id": self.result["import_id"], "source_reference": "fixture://knowledge-source", "source_sha256": self.source_hash, "auditor_id": "fixture-auditor", "findings": [{"check": "source-identity", "status": "pass", "evidence": "source matches import"}, {"check": "semantic-and-node-scope", "status": "pass", "evidence": "point and active node checked"}]}
        data.update(overrides)
        self.audit_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_import_stays_validated_and_pending(self):
        self.assertEqual(self.result["status"], "validated")
        self.assertEqual(self.conn.execute("SELECT status FROM controlled_knowledge_import_runs").fetchone()[0], "validated")
        self.assertEqual(self.conn.execute("SELECT review_status FROM knowledge_points").fetchone()[0], "pending")

    def test_direct_approval_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE knowledge_points SET review_status='approved'")

    def test_passing_audit_approves_exact_batch(self):
        result = record_knowledge_mapping_audit(self.conn, self.audit_path)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(self.conn.execute("SELECT status FROM controlled_knowledge_import_runs").fetchone()[0], "approved")
        self.assertEqual(self.conn.execute("SELECT review_status FROM knowledge_points").fetchone()[0], "approved")

    def test_source_mismatch_writes_nothing(self):
        self._write_audit(source_sha256="different")
        with self.assertRaisesRegex(KnowledgeMappingAuditError, "source identity"):
            record_knowledge_mapping_audit(self.conn, self.audit_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM knowledge_mapping_audits").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT review_status FROM knowledge_points").fetchone()[0], "pending")

    def test_failed_finding_cannot_approve(self):
        self._write_audit(findings=[{"check": "semantic", "status": "fail", "evidence": "ambiguous source"}])
        with self.assertRaisesRegex(KnowledgeMappingAuditError, "every finding passes"):
            record_knowledge_mapping_audit(self.conn, self.audit_path)
        self.assertEqual(self.conn.execute("SELECT review_status FROM knowledge_points").fetchone()[0], "pending")

    def test_drifted_node_relation_blocks_batch(self):
        self.conn.execute("DELETE FROM curriculum_knowledge_points")
        with self.assertRaisesRegex(KnowledgeMappingAuditError, "incomplete, drifted"):
            record_knowledge_mapping_audit(self.conn, self.audit_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM knowledge_mapping_audits").fetchone()[0], 0)

    def test_rejects_unapproved_catalog_release(self):
        self.conn.execute("UPDATE catalog_releases SET status='draft' WHERE id='release'")
        self.conn.commit()
        other = self.root / "other.json"
        other.write_text(self.manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeManifestError, "approved catalog release"):
            import_knowledge_mapping(self.conn, other)

    def test_missing_source_file_writes_nothing(self):
        self.source_path.unlink()
        other = self.root / "missing-source.json"
        other.write_text(self.manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeManifestError, "source file does not exist"):
            import_knowledge_mapping(self.conn, other)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM controlled_knowledge_import_runs").fetchone()[0], 1)

    def test_tampered_source_file_writes_nothing(self):
        self.source_path.write_bytes(b"tampered")
        other = self.root / "tampered-source.json"
        other.write_text(self.manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeManifestError, "SHA-256"):
            import_knowledge_mapping(self.conn, other)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM controlled_knowledge_import_runs").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
