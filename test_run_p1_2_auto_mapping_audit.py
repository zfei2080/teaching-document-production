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
from apply_schema_v2_25 import apply as apply_schema_v2_25
from input_snapshot import refresh_current_snapshot, stable_hash
from question_curriculum_mapping_evidence import MappingEvidence, record_mapping_evidence
from run_p1_2_auto_mapping_audit import guarded_connection, run

ROOT = Path(__file__).parent
BASE_SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15")
)


class RunP12AutoMappingAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        self.db_path = self.workspace / "teaching_docs_copy.db"
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            for schema in BASE_SCHEMAS:
                conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()
        for apply in (apply_schema_v2_16, apply_schema_v2_17, apply_schema_v2_18, apply_schema_v2_19, apply_schema_v2_20, apply_schema_v2_21, apply_schema_v2_22, apply_schema_v2_24, apply_schema_v2_25):
            apply(self.db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._seed_database()

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def _seed_database(self) -> None:
        self.conn.execute("""INSERT INTO source_documents
            (id, relative_path, file_hash, file_type, original_relative_path, original_file_hash, trusted_source)
            VALUES ('source', 'source.docx', 'source-hash', 'docx', 'original.docx', 'source-hash', 1)""")
        self.conn.execute("INSERT INTO source_fragments (id, source_document_id, location_type, raw_text, raw_hash) VALUES ('fragment', 'source', 'paragraph', '原题', 'fragment-hash')")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('book', '教材', 'catalog-v1')")
        self.conn.execute("""INSERT INTO controlled_import_runs
            (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status)
            VALUES ('catalog-import', 'catalog', 'fixture://catalog', 'catalog-hash', 'manifest-hash', 'fixture', '1', 'validated')""")
        self.conn.execute("""INSERT INTO catalog_releases
            (id, textbook_id, catalog_version, source_reference, source_hash, status)
            VALUES ('catalog-release', 'book', 'catalog-v1', 'fixture://catalog', 'catalog-hash', 'approved')""")
        self.conn.execute("""INSERT INTO catalog_audits
            (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id, audit_method, source_reference, source_hash, status)
            VALUES ('catalog-audit', 'catalog-release', 'catalog-import', 'audit-hash', 'fixture', 'fixture', 'fixture://catalog', 'catalog-hash', 'approved')""")
        self.conn.execute("""INSERT INTO curriculum_nodes
            (id, textbook_id, stage, grade_level, node_type, name, catalog_version)
            VALUES ('node', 'book', '初中', '八年级', 'topic', '三角形内角和定理', 'catalog-v1')""")
        self._seed_question('q003', '3', '题目提到三角形，但主题不完整。', complete=False)
        self._seed_question('q006', '6', '三角形内角和为180°，求第三个角。', complete=True)
        self.conn.commit()

    def _seed_question(self, question_id: str, source_no: str, stem: str, *, complete: bool) -> None:
        self.conn.execute(
            """INSERT INTO questions
            (id, stem, options_json, answer, analysis, question_type, stage, grade_level, source_document_id, source_fragment_id, source_question_no, content_hash)
            VALUES (?, ?, '[]', '', '', '解答题', '初中', '八年级', 'source', 'fragment', ?, ?)""",
            (question_id, stem, source_no, f'hash-{question_id}'),
        )
        refresh_current_snapshot(self.conn, question_id)
        evidence_id = record_mapping_evidence(
            self.conn,
            MappingEvidence(
                question_id=question_id,
                textbook_id='book',
                curriculum_node_id='node',
                features={
                    'core_theme': '三角形内角和定理' if complete else '',
                    'required_knowledge': ['三角形内角和为180°'] if complete else [],
                    'required_knowledge_evidence': {'三角形内角和为180°': {'supported': True, 'patterns': ['内角和', '180°']}} if complete else {},
                },
                decider_id='controlled-mapping-rules',
                decider_version='1.0.0',
                status='candidate',
                confidence=0.9,
                reason='草案候选',
            ),
        )
        current_hash = self.conn.execute("SELECT input_hash FROM question_input_snapshots WHERE question_id=?", (question_id,)).fetchone()[0]
        if question_id == 'q006':
            for verification_type in ('source_fidelity', 'structural_consistency', 'mathematical_independent', 'asset_semantics'):
                self.conn.execute(
                    """INSERT INTO question_verifications
                    (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash)
                    VALUES (?, ?, ?, ?, '1.0.0', 'pass', ?, ?)""",
                    (
                        stable_hash([question_id, verification_type, current_hash]),
                        question_id,
                        verification_type,
                        f'{verification_type}-validator',
                        json.dumps({'detail': verification_type}, ensure_ascii=False, sort_keys=True),
                        current_hash,
                    ),
                )
        setattr(self, f'{question_id}_evidence_id', evidence_id)

    def test_runner_generates_append_only_audits_and_blocks_q003(self) -> None:
        output = self.workspace / 'output' / 'p1_2_audit.json'
        backup_dir = self.workspace / 'backups'
        report = run(self.db_path, output, backup_dir=backup_dir)
        self.assertTrue(output.is_file())
        self.assertEqual(report['summary']['audit_count'], 2)
        self.assertEqual(report['summary']['created_count'], 2)
        self.assertEqual(report['summary']['reused_count'], 0)
        self.assertEqual(report['database']['path'], str(self.db_path.resolve()))
        self.assertIsNotNone(report['database']['sha256_before'])
        self.assertIsNotNone(report['database']['sha256_after'])
        self.assertEqual(report['database']['migrations_before'], report['database']['migrations_after'])
        self.assertIsNotNone(report['backup'])
        self.assertTrue(Path(report['backup']['path']).is_file())
        self.assertEqual(report['backup']['quick_check'], 'ok')
        self.assertEqual(report['summary']['current_pass_count'], 1)
        by_question = {item['question_id']: item for item in report['results']}
        self.assertEqual(by_question['q003']['audit_status'], 'unsupported')
        self.assertIn('required_knowledge_missing', by_question['q003']['findings']['feature_findings'])
        self.assertEqual(by_question['q006']['audit_status'], 'pass')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_knowledge_points").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM questions WHERE quality_status='approved' OR review_status='approved'").fetchone()[0], 0)

    def test_runner_is_idempotent_and_reuses_existing_audits(self) -> None:
        first_output = self.workspace / 'output' / 'first.json'
        first_report = run(self.db_path, first_output)
        self.assertEqual(first_report['summary']['created_count'], 2)
        self.assertEqual(first_report['summary']['reused_count'], 0)
        count_after_first = self.conn.execute("SELECT COUNT(*) FROM question_auto_mapping_audit_logs").fetchone()[0]

        second_output = self.workspace / 'output' / 'second.json'
        second_report = run(self.db_path, second_output)
        self.assertTrue(second_output.is_file())
        self.assertEqual(second_report['summary']['audit_count'], 2)
        self.assertEqual(second_report['summary']['created_count'], 0)
        self.assertEqual(second_report['summary']['reused_count'], 2)
        self.assertTrue(all(item['reused_existing'] for item in second_report['results']))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM question_auto_mapping_audit_logs").fetchone()[0],
            count_after_first,
        )

    def test_q006_without_full_chain_is_unsupported(self) -> None:
        self.conn.execute("DELETE FROM question_verifications WHERE question_id='q006' AND verification_type='asset_semantics'")
        self.conn.commit()
        output = self.workspace / 'output' / 'p1_2_missing_chain.json'
        report = run(self.db_path, output)
        self.assertEqual(report['summary']['created_count'], 2)
        self.assertEqual(report['summary']['reused_count'], 0)
        by_question = {item['question_id']: item for item in report['results']}
        self.assertEqual(by_question['q006']['audit_status'], 'unsupported')
        self.assertIn('validator_missing:asset_semantics', by_question['q006']['findings']['validator_findings'])
        self.assertEqual(report['summary']['current_pass_count'], 0)

    def test_authorizer_blocks_unauthorized_writes(self) -> None:
        with guarded_connection(self.db_path) as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE questions SET stem='bad' WHERE id='q003'")
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("INSERT INTO question_verifications (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash) VALUES ('x', 'q003', 'source_fidelity', 'v', '1', 'pass', '{}', 'h')")

    def test_real_db_copy_regression_q003_unsupported_q006_passes(self) -> None:
        source_db = ROOT / 'data' / 'dev' / 'teaching_docs_dev.db'
        if not source_db.is_file():
            self.skipTest('real db copy fixture missing')
        self.conn.close()
        self.db_path.write_bytes(source_db.read_bytes())
        for apply in (apply_schema_v2_24, apply_schema_v2_25):
            apply(self.db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute('PRAGMA foreign_keys=ON')
        output = self.workspace / 'output' / 'real_copy_regression.json'
        report = run(self.db_path, output)
        self.assertEqual(report['summary']['created_count'], 0)
        current_ids = {
            row[0] for row in self.conn.execute(
                'SELECT id FROM current_question_curriculum_mapping_evidence'
            )
        }
        result_ids = {item['evidence_id'] for item in report['results']}
        self.assertEqual(result_ids, current_ids)
        self.assertEqual(report['summary']['reused_count'], len(current_ids))
        by_question = {item['question_id']: item for item in report['results']}
        if 'golden-q003' in by_question:
            self.assertEqual(by_question['golden-q003']['audit_status'], 'unsupported')
            self.assertIn('required_knowledge_incomplete_against_question_signals', by_question['golden-q003']['findings']['feature_findings'])
        if 'golden-q006' in by_question:
            self.assertEqual(by_question['golden-q006']['audit_status'], 'pass')


if __name__ == '__main__':
    unittest.main()
