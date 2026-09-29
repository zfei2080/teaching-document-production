from __future__ import annotations

import json
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
from apply_schema_v2_22 import apply as apply_schema_v2_22
from apply_schema_v2_24 import apply as apply_schema_v2_24
from apply_schema_v2_25 import MIGRATION, apply as apply_schema_v2_25
from input_snapshot import refresh_current_snapshot
from question_auto_mapping_audit import AutoMappingAuditRequest, record_auto_mapping_audit
from question_curriculum_mapping_evidence import MappingEvidence, record_mapping_evidence

ROOT = Path(__file__).parent
BASE_SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15")
)


class SchemaV225Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "schema_v225.db"
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            for schema in BASE_SCHEMAS:
                conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()
        for apply in (
            apply_schema_v2_16,
            apply_schema_v2_17,
            apply_schema_v2_18,
            apply_schema_v2_19,
            apply_schema_v2_20,
            apply_schema_v2_21,
            apply_schema_v2_22,
            apply_schema_v2_24,
            apply_schema_v2_25,
        ):
            apply(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._seed_base()

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def _seed_base(self) -> None:
        self.audit_id, self.question_input_hash = self._seed_base_common(self.conn)

    def _base_insert_sql(self) -> str:
        return """INSERT INTO question_auto_mapping_audit_logs
        (id, evidence_id, question_id, textbook_id, curriculum_node_id, audit_status, decision_basis, blocked_reason,
         question_input_hash, question_snapshot_revision, evidence_input_hash,
         evidence_question_snapshot_revision, evidence_hash, textbook_catalog_version, node_catalog_version,
         validator_bundle_id, validator_bundle_version, validator_results_hash,
         validator_results_json, findings_json, auditor_id, auditor_version, audit_json, audit_hash)
        SELECT ?, evidence_id, question_id, textbook_id, curriculum_node_id, 'pass', decision_basis, NULL,
               question_input_hash, question_snapshot_revision, evidence_input_hash,
               evidence_question_snapshot_revision, evidence_hash, textbook_catalog_version, node_catalog_version,
               validator_bundle_id, validator_bundle_version, ?,
               ?, findings_json, auditor_id, auditor_version, audit_json, ?
        FROM question_auto_mapping_audit_logs WHERE id=?"""

    def test_empty_bundle_insert_rejected(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(self._base_insert_sql(), ('forged-empty', 'hash-empty', '{}', 'audit-empty', self.audit_id))

    def test_missing_required_key_bundle_insert_rejected(self) -> None:
        payload = json.dumps({
            'source_fidelity': {'validator_id': 'source_fidelity-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': self.question_input_hash},
            'structural_consistency': {'validator_id': 'structural_consistency-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': self.question_input_hash},
            'mathematical_independent': {'validator_id': 'mathematical_independent-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': self.question_input_hash},
        }, ensure_ascii=False, sort_keys=True)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(self._base_insert_sql(), ('forged-missing', 'hash-missing', payload, 'audit-missing', self.audit_id))

    def test_extra_key_bundle_insert_rejected(self) -> None:
        payload = json.dumps({
            'source_fidelity': {'validator_id': 'source_fidelity-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': self.question_input_hash},
            'structural_consistency': {'validator_id': 'structural_consistency-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': self.question_input_hash},
            'mathematical_independent': {'validator_id': 'mathematical_independent-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': self.question_input_hash},
            'asset_semantics': {'validator_id': 'asset_semantics-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': self.question_input_hash},
            'unexpected': {'validator_id': 'unexpected-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': self.question_input_hash},
        }, ensure_ascii=False, sort_keys=True)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(self._base_insert_sql(), ('forged-extra', 'hash-extra', payload, 'audit-extra', self.audit_id))

    def test_current_view_excludes_tampered_bundle(self) -> None:
        legacy_path = Path(self.tempdir.name) / "schema_v225_legacy.db"
        legacy = sqlite3.connect(legacy_path)
        try:
            legacy.execute("PRAGMA foreign_keys=ON")
            for schema in BASE_SCHEMAS:
                legacy.executescript((ROOT / schema).read_text(encoding="utf-8"))
            legacy.commit()
        finally:
            legacy.close()
        for apply in (
            apply_schema_v2_16,
            apply_schema_v2_17,
            apply_schema_v2_18,
            apply_schema_v2_19,
            apply_schema_v2_20,
            apply_schema_v2_21,
            apply_schema_v2_22,
            apply_schema_v2_24,
        ):
            apply(legacy_path)

        legacy = sqlite3.connect(legacy_path)
        try:
            legacy.execute("PRAGMA foreign_keys=ON")
            legacy_audit_id, _ = self._seed_base_common(legacy)
            row = legacy.execute("SELECT * FROM question_auto_mapping_audit_logs WHERE id=?", (legacy_audit_id,)).fetchone()
            self.assertIsNotNone(row)
            legacy.execute("DELETE FROM question_auto_mapping_audit_logs WHERE id=?", (legacy_audit_id,))
            columns = [info[1] for info in legacy.execute("PRAGMA table_info(question_auto_mapping_audit_logs)")]
            values = dict(zip(columns, row))
            values["id"] = "tampered-audit"
            values["validator_results_json"] = "{}"
            values["validator_results_hash"] = "tampered-hash"
            values["audit_hash"] = "tampered-audit-hash"
            placeholders = ", ".join("?" for _ in columns)
            legacy.execute(
                f"INSERT INTO question_auto_mapping_audit_logs ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(values[col] for col in columns),
            )
            legacy.commit()
        finally:
            legacy.close()

        apply_schema_v2_25(legacy_path)
        legacy = sqlite3.connect(legacy_path)
        try:
            count = legacy.execute("SELECT COUNT(*) FROM current_question_auto_mapping_audits WHERE id='tampered-audit'").fetchone()[0]
        finally:
            legacy.close()
        self.assertEqual(count, 0)

    def _seed_base_common(self, conn: sqlite3.Connection) -> None:
        conn.execute("""INSERT INTO source_documents
            (id, relative_path, file_hash, file_type, original_relative_path, original_file_hash, trusted_source)
            VALUES ('source', 'source.docx', 'source-hash', 'docx', 'original.docx', 'source-hash', 1)""")
        conn.execute("INSERT INTO source_fragments (id, source_document_id, location_type, raw_text, raw_hash) VALUES ('fragment', 'source', 'paragraph', '原题', 'fragment-hash')")
        conn.execute("""INSERT INTO questions
            (id, stem, question_type, stage, source_document_id, source_fragment_id, content_hash)
            VALUES ('question', '三角形内角和为180°，求第三个角', '解答题', '初中', 'source', 'fragment', 'question-hash')""")
        conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('book', '教材', 'catalog-v1')")
        conn.execute("""INSERT INTO curriculum_nodes
            (id, textbook_id, stage, grade_level, node_type, name, catalog_version)
            VALUES ('node', 'book', '初中', '八年级', 'topic', '三角形内角和定理', 'catalog-v1')""")
        conn.execute("""INSERT INTO controlled_import_runs
            (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status)
            VALUES ('catalog-import', 'catalog', 'fixture://catalog', 'catalog-hash', 'manifest-hash', 'fixture', '1', 'validated')""")
        conn.execute("""INSERT INTO catalog_releases
            (id, textbook_id, catalog_version, source_reference, source_hash, status)
            VALUES ('catalog-release', 'book', 'catalog-v1', 'fixture://catalog', 'catalog-hash', 'approved')""")
        conn.execute("""INSERT INTO catalog_audits
            (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id, audit_method, source_reference, source_hash, status)
            VALUES ('catalog-audit', 'catalog-release', 'catalog-import', 'audit-hash', 'fixture', 'fixture', 'fixture://catalog', 'catalog-hash', 'approved')""")
        refresh_current_snapshot(conn, 'question')
        evidence_id = record_mapping_evidence(
            conn,
            MappingEvidence(
                question_id='question',
                textbook_id='book',
                curriculum_node_id='node',
                features={
                    'core_theme': '三角形内角和定理',
                    'required_knowledge': ['三角形内角和为180°'],
                    'required_knowledge_evidence': {
                        '三角形内角和为180°': {'supported': True, 'patterns': ['内角和', '180°']}
                    },
                },
                decider_id='controlled-mapping-rules',
                decider_version='1.0.0',
                status='candidate',
                confidence=0.91,
                reason='候选草案',
            ),
        )
        input_hash = conn.execute("SELECT input_hash FROM question_input_snapshots WHERE question_id='question'").fetchone()[0]
        for verification_type in ('source_fidelity', 'structural_consistency', 'mathematical_independent', 'asset_semantics'):
            conn.execute(
                """INSERT INTO question_verifications
                (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash)
                VALUES (?, 'question', ?, ?, '1.0.0', 'pass', '{"detail":"ok"}', ?)""",
                (f'{verification_type}-id', verification_type, f'{verification_type}-validator', input_hash),
            )
        result = record_auto_mapping_audit(conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        conn.commit()
        return result.audit_id, input_hash

    def test_migration_is_idempotent(self) -> None:
        apply_schema_v2_25(self.path)
        count = self.conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()
