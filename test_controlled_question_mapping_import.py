"""Regression tests for source-bound pending question-mapping imports."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from controlled_question_mapping_import import MappingManifestError, import_question_mappings

ROOT = Path(__file__).parent
SCHEMAS = ("schema_v2.sql", "schema_v2_5.sql", "schema_v2_6.sql", "schema_v2_7.sql", "schema_v2_8.sql", "schema_v2_9.sql")


class ControlledQuestionMappingImportTests(unittest.TestCase):
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
        self.source_path = self.root / "mapping-source.txt"
        self.source_path.write_bytes(self.source_bytes)
        self.manifest_path = self.root / "mapping-manifest.json"
        self.write_manifest()

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def _seed_approved_catalog_and_knowledge_point(self):
        self.conn.execute("INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('source', 'source.docx', 'hash', 'docx')")
        self.conn.execute("INSERT INTO questions (id, stem, question_type, stage, source_document_id, content_hash) VALUES ('q1', '题干', '选择题', '初中', 'source', 'content')")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('nbsd-8-lower', '北师大版八年级下册', '2026.1')")
        self.conn.execute("INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, catalog_version) VALUES ('triangle-proof', 'nbsd-8-lower', '初中', '八下', 'chapter', '三角形的证明', '2026.1')")
        self.conn.execute("INSERT INTO catalog_releases (id, textbook_id, catalog_version, source_reference, source_hash, status) VALUES ('catalog', 'nbsd-8-lower', '2026.1', 'catalog-source', 'catalog-hash', 'draft')")
        self.conn.execute("INSERT INTO controlled_import_runs (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status) VALUES ('catalog-import', 'catalog', 'catalog-source', 'catalog-hash', 'catalog-manifest', 'fixture', 'v1', 'validated')")
        self.conn.execute("INSERT INTO catalog_release_imports VALUES ('catalog', 'catalog-import')")
        self.conn.execute("INSERT INTO catalog_audits (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id, audit_method, source_reference, source_hash, status, findings_json) VALUES ('catalog-audit', 'catalog', 'catalog-import', 'audit-manifest', 'fixture', 'fixture', 'catalog-source', 'catalog-hash', 'approved', '[]')")
        self.conn.execute("UPDATE catalog_releases SET status='approved' WHERE id='catalog'")
        self.conn.execute("INSERT INTO knowledge_points (id, canonical_name, knowledge_type, stage_scope, version, review_status) VALUES ('kp-congruence', '全等三角形判定', 'criterion', '初中', 'v1', 'approved')")
        self.conn.execute("INSERT INTO curriculum_knowledge_points VALUES ('triangle-proof', 'kp-congruence', 'primary')")
        self.conn.commit()

    def write_manifest(self, **overrides):
        manifest = {
            "schema_version": "controlled-question-mapping-manifest-v1",
            "source": {"reference": "fixture://mapping-source", "file": self.source_path.name, "sha256": self.source_hash},
            "mappings": [{
                "question_id": "q1",
                "textbook_id": "nbsd-8-lower",
                "curriculum_node_id": "triangle-proof",
                "knowledge_points": [{"knowledge_point_id": "kp-congruence", "relation_type": "primary"}],
            }],
        }
        manifest.update(overrides)
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def test_valid_import_is_traceable_but_pending(self):
        result = import_question_mappings(self.conn, self.manifest_path)
        self.assertEqual(result["mappings"], 1)
        self.assertEqual(result["status"], "validated")
        self.assertEqual(self.conn.execute("SELECT fit_status FROM question_textbooks").fetchone()[0], "pending")
        self.assertEqual(self.conn.execute("SELECT status FROM controlled_import_runs WHERE id=?", (result["import_id"],)).fetchone()[0], "validated")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbook_imports").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_knowledge_point_imports").fetchone()[0], 1)

    def test_unapproved_catalog_blocks_all_writes(self):
        self.conn.execute("UPDATE catalog_releases SET status='archived' WHERE id='catalog'")
        with self.assertRaisesRegex(MappingManifestError, "approved catalog release"):
            import_question_mappings(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM controlled_import_runs WHERE import_kind='question_mapping'").fetchone()[0], 0)

    def test_node_knowledge_point_relation_is_required(self):
        self.conn.execute("DELETE FROM curriculum_knowledge_points")
        with self.assertRaisesRegex(MappingManifestError, "knowledge point must be approved"):
            import_question_mappings(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0], 0)

    def test_stage_conflict_blocks_all_writes(self):
        self.conn.execute("UPDATE curriculum_nodes SET stage='高中' WHERE id='triangle-proof'")
        with self.assertRaisesRegex(MappingManifestError, "stage must match"):
            import_question_mappings(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0], 0)

    def test_identical_import_is_not_silently_overwritten(self):
        import_question_mappings(self.conn, self.manifest_path)
        with self.assertRaisesRegex(MappingManifestError, "already exists"):
            import_question_mappings(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0], 1)

    def test_missing_source_file_blocks_all_writes(self):
        self.source_path.unlink()
        with self.assertRaisesRegex(MappingManifestError, "source file does not exist"):
            import_question_mappings(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM controlled_import_runs WHERE import_kind='question_mapping'").fetchone()[0], 0)

    def test_source_file_outside_manifest_directory_is_rejected(self):
        external = self.root.parent / "outside-mapping-source.txt"
        external.write_bytes(self.source_bytes)
        self.write_manifest(source={"reference": "fixture://mapping-source", "file": str(external), "sha256": self.source_hash})
        with self.assertRaisesRegex(MappingManifestError, "outside the manifest directory"):
            import_question_mappings(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0], 0)

    def test_tampered_source_file_blocks_all_writes(self):
        self.source_path.write_bytes(b"tampered mapping evidence")
        with self.assertRaisesRegex(MappingManifestError, "SHA-256"):
            import_question_mappings(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM controlled_import_runs WHERE import_kind='question_mapping'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
