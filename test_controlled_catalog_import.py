"""Regression tests for source-hashed controlled curriculum catalog imports."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from controlled_catalog_import import ManifestError, import_catalog

ROOT = Path(__file__).parent
SCHEMAS = ("schema_v2.sql", "schema_v2_5.sql", "schema_v2_6.sql", "schema_v2_7.sql")


class ControlledCatalogImportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "dev.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self.source = self.root / "catalog-source.md"
        self.source.write_text("# 北师大版八年级下册\n\n三角形的证明\n", encoding="utf-8")
        self.manifest_path = self.root / "catalog-manifest.json"
        self.write_manifest()

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def write_manifest(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        manifest = {
            "schema_version": "controlled-catalog-manifest-v1",
            "catalog": {
                "textbook_id": "fixture-nbsd-8-lower",
                "name": "北师大版八年级下册",
                "subject": "数学",
                "publisher": "北京师范大学出版社",
                "catalog_version": "fixture-2026.1",
                "source_reference": "fixture://catalog-source",
                "source_file": self.source.name,
                "source_sha256": digest,
            },
            "nodes": [
                {"id": "fixture-term", "stage": "初中", "grade_level": "八下", "node_type": "term", "name": "八年级下册", "sequence": 1},
                {"id": "fixture-triangle-proof", "parent_id": "fixture-term", "stage": "初中", "grade_level": "八下", "node_type": "chapter", "name": "三角形的证明", "sequence": 2},
            ],
            "knowledge_points": [
                {"id": "fixture-kp-congruence", "canonical_name": "全等三角形判定", "knowledge_type": "criterion", "stage_scope": "初中", "version": "fixture-2026.1"},
            ],
            "curriculum_knowledge_points": [
                {"curriculum_node_id": "fixture-triangle-proof", "knowledge_point_id": "fixture-kp-congruence", "relation_type": "primary"},
            ],
        }
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def test_valid_import_is_traceable_but_not_approved(self):
        result = import_catalog(self.conn, self.manifest_path)
        self.assertEqual(result["nodes"], 2)
        self.assertEqual(result["knowledge_points"], 1)
        self.assertEqual(self.conn.execute("SELECT status FROM catalog_releases").fetchone()[0], "draft")
        self.assertEqual(self.conn.execute("SELECT status FROM controlled_import_runs").fetchone()[0], "validated")
        self.assertEqual(self.conn.execute("SELECT import_kind FROM controlled_import_runs").fetchone()[0], "catalog")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM catalog_release_imports").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT review_status FROM knowledge_points").fetchone()[0], "pending")

    def test_source_hash_mismatch_blocks_all_writes(self):
        self.source.write_text("来源已被替换", encoding="utf-8")
        with self.assertRaisesRegex(ManifestError, "SHA-256"):
            import_catalog(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM textbooks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM controlled_import_runs").fetchone()[0], 0)

    def test_unknown_parent_blocks_all_writes(self):
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["nodes"][1]["parent_id"] = "outside-manifest"
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ManifestError, "父节点"):
            import_catalog(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM textbooks").fetchone()[0], 0)

    def test_duplicate_release_is_not_silently_overwritten(self):
        import_catalog(self.conn, self.manifest_path)
        with self.assertRaisesRegex(ManifestError, "已存在"):
            import_catalog(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM catalog_releases").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
