"""Regression tests for source-bound catalog audit and approval."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from catalog_audit import CatalogAuditError, record_catalog_audit
from controlled_catalog_import import import_catalog

ROOT = Path(__file__).parent
SCHEMAS = ("schema_v2.sql", "schema_v2_5.sql", "schema_v2_6.sql", "schema_v2_7.sql", "schema_v2_8.sql")


class CatalogAuditTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.conn = sqlite3.connect(self.root / "dev.db")
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self.source = self.root / "catalog-source.md"
        self.source.write_text("# 受控教材目录\n\n八年级下册：三角形证明\n", encoding="utf-8")
        self.catalog_manifest = self.root / "catalog.json"
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.catalog_manifest.write_text(json.dumps({
            "schema_version": "controlled-catalog-manifest-v1",
            "catalog": {"textbook_id": "fixture-book", "name": "受控教材", "subject": "数学", "catalog_version": "fixture-v1", "source_reference": "fixture://source", "source_file": self.source.name, "source_sha256": source_hash},
            "nodes": [{"id": "fixture-node", "stage": "初中", "grade_level": "八下", "node_type": "chapter", "name": "三角形证明", "sequence": 1}],
            "knowledge_points": [], "curriculum_knowledge_points": []
        }, ensure_ascii=False), encoding="utf-8")
        self.import_result = import_catalog(self.conn, self.catalog_manifest)
        self.audit_manifest = self.root / "audit.json"
        self.write_audit_manifest()

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def write_audit_manifest(self, **overrides):
        data = {
            "schema_version": "controlled-catalog-audit-v1",
            "catalog_release_id": self.import_result["release_id"],
            "import_run_id": self.import_result["import_id"],
            "source_reference": "fixture://source",
            "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "auditor_id": "fixture-auditor",
            "findings": [
                {"check": "source-identity", "status": "pass", "evidence": "source hash matches import"},
                {"check": "scope", "status": "pass", "evidence": "fixture scope checked"},
            ],
        }
        data.update(overrides)
        self.audit_manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_release_cannot_be_approved_without_audit(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE catalog_releases SET status='approved' WHERE id=?", (self.import_result["release_id"],))

    def test_passing_source_bound_audit_approves_release(self):
        result = record_catalog_audit(self.conn, self.audit_manifest)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(self.conn.execute("SELECT status FROM catalog_releases").fetchone()[0], "approved")
        self.assertEqual(self.conn.execute("SELECT status FROM catalog_audits").fetchone()[0], "approved")

    def test_source_identity_mismatch_does_not_write_audit_or_approve(self):
        self.write_audit_manifest(source_sha256="not-the-source-hash")
        with self.assertRaisesRegex(CatalogAuditError, "source identity"):
            record_catalog_audit(self.conn, self.audit_manifest)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM catalog_audits").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM catalog_releases").fetchone()[0], "draft")

    def test_failed_finding_cannot_approve_release(self):
        self.write_audit_manifest(findings=[{"check": "scope", "status": "fail", "evidence": "cross-grade content found"}])
        with self.assertRaisesRegex(CatalogAuditError, "every finding passes"):
            record_catalog_audit(self.conn, self.audit_manifest)
        self.assertEqual(self.conn.execute("SELECT status FROM catalog_releases").fetchone()[0], "draft")


if __name__ == "__main__":
    unittest.main()
