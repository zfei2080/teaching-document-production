"""Regression tests for controlled textbook-scope evidence."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from textbook_scope_validator import validate

ROOT = Path(__file__).parent
SCHEMAS = ("schema_v2.sql", "schema_v2_5.sql", "schema_v2_6.sql", "schema_v2_7.sql", "schema_v2_9.sql")


class TextbookScopeValidatorTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self.conn.execute("INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('source', 'source.docx', 'hash', 'docx')")
        self.conn.execute("INSERT INTO questions (id, stem, question_type, stage, source_document_id, content_hash) VALUES ('q1', '题干', '选择题', '初中', 'source', 'content')")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('nbsd-8-lower', '北师大版八年级下册', '2026.1')")
        self.conn.execute("INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, catalog_version) VALUES ('triangle-proof', 'nbsd-8-lower', '初中', '八下', 'chapter', '三角形的证明', '2026.1')")

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def approve_complete_mapping(self):
        self.conn.execute("INSERT INTO catalog_releases (id, textbook_id, catalog_version, source_reference, source_hash, status) VALUES ('catalog', 'nbsd-8-lower', '2026.1', 'controlled-source', 'source-hash', 'approved')")
        self.conn.execute("INSERT INTO controlled_import_runs (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status) VALUES ('catalog-import', 'catalog', 'controlled-source', 'source-hash', 'catalog-manifest', 'fixture', 'v1', 'approved')")
        self.conn.execute("INSERT INTO catalog_release_imports VALUES ('catalog', 'catalog-import')")
        self.conn.execute("INSERT INTO knowledge_points (id, canonical_name, knowledge_type, stage_scope, version, review_status) VALUES ('kp', '全等三角形', 'criterion', '初中', 'v1', 'approved')")
        self.conn.execute("INSERT INTO curriculum_knowledge_points VALUES ('triangle-proof', 'kp', 'primary')")
        self.conn.execute("INSERT INTO question_knowledge_points VALUES ('q1', 'kp', 'primary')")
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'nbsd-8-lower', 'triangle-proof', 'approved')")
        self.conn.execute("INSERT INTO controlled_import_runs (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status) VALUES ('mapping-import', 'question_mapping', 'controlled-mapping', 'mapping-source-hash', 'mapping-manifest', 'fixture', 'v1', 'approved')")
        self.conn.execute("INSERT INTO question_textbook_imports VALUES ('q1', 'nbsd-8-lower', 'triangle-proof', 'mapping-import', 'mapping-hash')")
        self.conn.execute("INSERT INTO question_knowledge_point_imports VALUES ('q1', 'kp', 'mapping-import', 'mapping-hash')")

    def test_missing_mapping_is_unsupported(self):
        result = validate(self.conn, "q1")
        self.assertEqual(result.status, "unsupported")

    def test_complete_approved_mapping_passes(self):
        self.approve_complete_mapping()
        result = validate(self.conn, "q1")
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.evidence["passes"][0]["knowledge_points"], ["kp"])

    def test_stage_conflict_fails_even_with_otherwise_complete_mapping(self):
        self.approve_complete_mapping()
        self.conn.execute("UPDATE curriculum_nodes SET stage='高中' WHERE id='triangle-proof'")
        result = validate(self.conn, "q1")
        self.assertEqual(result.status, "fail")
        self.assertIn("题目学段", result.evidence["failures"][0]["reason"])

    def test_mapping_without_controlled_import_is_unsupported(self):
        self.approve_complete_mapping()
        self.conn.execute("DELETE FROM question_textbook_imports WHERE question_id='q1'")
        result = validate(self.conn, "q1")
        self.assertEqual(result.status, "unsupported")
        self.assertIn("受控导入", result.evidence["unsupported"][0]["reason"])

    def test_missing_knowledge_point_evidence_is_unsupported(self):
        self.approve_complete_mapping()
        self.conn.execute("DELETE FROM question_knowledge_points WHERE question_id='q1'")
        result = validate(self.conn, "q1")
        self.assertEqual(result.status, "unsupported")

    def test_knowledge_point_without_same_import_provenance_is_unsupported(self):
        self.approve_complete_mapping()
        self.conn.execute("DELETE FROM question_knowledge_point_imports WHERE question_id='q1'")
        result = validate(self.conn, "q1")
        self.assertEqual(result.status, "unsupported")

    def test_catalog_release_rejects_question_mapping_import_kind(self):
        self.conn.execute("INSERT INTO catalog_releases (id, textbook_id, catalog_version, source_reference, source_hash, status) VALUES ('catalog', 'nbsd-8-lower', '2026.1', 'controlled-source', 'source-hash', 'approved')")
        self.conn.execute("INSERT INTO controlled_import_runs (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status) VALUES ('wrong-kind', 'question_mapping', 'controlled-source', 'source-hash', 'manifest', 'fixture', 'v1', 'approved')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO catalog_release_imports VALUES ('catalog', 'wrong-kind')")

    def test_catalog_release_rejects_mismatched_source_identity(self):
        self.conn.execute("INSERT INTO catalog_releases (id, textbook_id, catalog_version, source_reference, source_hash, status) VALUES ('catalog', 'nbsd-8-lower', '2026.1', 'controlled-source', 'source-hash', 'approved')")
        self.conn.execute("INSERT INTO controlled_import_runs (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status) VALUES ('wrong-source', 'catalog', 'other-source', 'source-hash', 'manifest', 'fixture', 'v1', 'approved')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO catalog_release_imports VALUES ('catalog', 'wrong-source')")

    def test_question_knowledge_point_rejects_catalog_import_kind(self):
        self.conn.execute("INSERT INTO knowledge_points (id, canonical_name, knowledge_type, stage_scope, version, review_status) VALUES ('kp', '全等三角形', 'criterion', '初中', 'v1', 'approved')")
        self.conn.execute("INSERT INTO question_knowledge_points VALUES ('q1', 'kp', 'primary')")
        self.conn.execute("INSERT INTO controlled_import_runs (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status) VALUES ('wrong-kind-kp', 'catalog', 'controlled-source', 'source-hash', 'manifest', 'fixture', 'v1', 'approved')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO question_knowledge_point_imports VALUES ('q1', 'kp', 'wrong-kind-kp', 'mapping-hash')")

    def test_question_mapping_rejects_catalog_import_kind(self):
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'nbsd-8-lower', 'triangle-proof', 'approved')")
        self.conn.execute("INSERT INTO controlled_import_runs (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status) VALUES ('wrong-kind', 'catalog', 'controlled-source', 'source-hash', 'manifest', 'fixture', 'v1', 'approved')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO question_textbook_imports VALUES ('q1', 'nbsd-8-lower', 'triangle-proof', 'wrong-kind', 'mapping-hash')")


if __name__ == "__main__":
    unittest.main()
