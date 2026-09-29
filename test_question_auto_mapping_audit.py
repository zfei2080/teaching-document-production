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
from input_snapshot import refresh_current_snapshot, stable_hash
from question_auto_mapping_audit import AutoMappingAuditRequest, record_auto_mapping_audit
from question_curriculum_mapping_evidence import MappingEvidence, record_mapping_evidence

ROOT = Path(__file__).parent
BASE_SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15")
)


class QuestionAutoMappingAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "auto_mapping_audit.db"
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            for schema in BASE_SCHEMAS:
                conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()
        for apply in (apply_schema_v2_16, apply_schema_v2_17, apply_schema_v2_18, apply_schema_v2_19, apply_schema_v2_20, apply_schema_v2_21, apply_schema_v2_22, apply_schema_v2_24, apply_schema_v2_25):
            apply(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._seed_base()

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def _seed_base(self) -> None:
        self.conn.execute("""INSERT INTO source_documents
            (id, relative_path, file_hash, file_type, original_relative_path, original_file_hash, trusted_source)
            VALUES ('source', 'source.docx', 'source-hash', 'docx', 'original.docx', 'source-hash', 1)""")
        self.conn.execute("INSERT INTO source_fragments (id, source_document_id, location_type, raw_text, raw_hash) VALUES ('fragment', 'source', 'paragraph', '原题', 'fragment-hash')")
        self.conn.execute("""INSERT INTO questions
            (id, stem, options_json, answer, analysis, question_type, stage, source_document_id, source_fragment_id, content_hash)
            VALUES ('question', '三角形内角和为180°，求第三个角', '[]', '', '', '解答题', '初中', 'source', 'fragment', 'question-hash')""")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('book', '教材', 'catalog-v1')")
        self.conn.execute("""INSERT INTO curriculum_nodes
            (id, textbook_id, stage, grade_level, node_type, name, catalog_version)
            VALUES ('node', 'book', '初中', '八年级', 'topic', '三角形内角和定理', 'catalog-v1')""")
        self.conn.execute("""INSERT INTO controlled_import_runs
            (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status)
            VALUES ('catalog-import', 'catalog', 'fixture://catalog', 'catalog-hash', 'manifest-hash', 'fixture', '1', 'validated')""")
        self.conn.execute("""INSERT INTO catalog_releases
            (id, textbook_id, catalog_version, source_reference, source_hash, status)
            VALUES ('catalog-release', 'book', 'catalog-v1', 'fixture://catalog', 'catalog-hash', 'approved')""")
        self.conn.execute("""INSERT INTO catalog_audits
            (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id, audit_method, source_reference, source_hash, status)
            VALUES ('catalog-audit', 'catalog-release', 'catalog-import', 'audit-hash', 'fixture', 'fixture', 'fixture://catalog', 'catalog-hash', 'approved')""")
        refresh_current_snapshot(self.conn, 'question')
        self.conn.commit()

    def _candidate_features(self, complete: bool = True, supported: bool = True) -> dict[str, object]:
        features = {
            'core_theme': '三角形内角和定理',
            'required_knowledge': ['三角形内角和为180°'],
            'required_knowledge_evidence': {
                '三角形内角和为180°': {'supported': supported, 'patterns': ['内角和', '180°']}
            },
        }
        if not complete:
            features['required_knowledge'] = []
        return features

    def _record_candidate(self, *, status: str = 'candidate', features: dict[str, object] | None = None, question_id: str = 'question', textbook_id: str = 'book', node_id: str = 'node') -> str:
        return record_mapping_evidence(
            self.conn,
            MappingEvidence(
                question_id=question_id,
                textbook_id=textbook_id,
                curriculum_node_id=node_id,
                features=features or self._candidate_features(),
                decider_id='controlled-mapping-rules',
                decider_version='1.0.0',
                status=status,
                confidence=0.91,
                reason='候选草案',
            ),
        )

    def _verification(self, verification_type: str, status: str = 'pass', *, input_hash: str | None = None, evidence: dict[str, object] | None = None, validator_id: str | None = None, validator_version: str = '1.0.0') -> None:
        if input_hash is None:
            input_hash = self.conn.execute("SELECT input_hash FROM question_input_snapshots WHERE question_id='question'").fetchone()[0]
        self.conn.execute(
            """INSERT INTO question_verifications
            (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash)
            VALUES (?, 'question', ?, ?, ?, ?, ?, ?)""",
            (
                stable_hash([verification_type, status, validator_id or verification_type, input_hash, validator_version, json.dumps(evidence or {'detail': verification_type}, ensure_ascii=False, sort_keys=True)]),
                verification_type,
                validator_id or f'{verification_type}-validator',
                validator_version,
                status,
                json.dumps(evidence or {'detail': verification_type}, ensure_ascii=False, sort_keys=True),
                input_hash,
            ),
        )

    def _add_four_pass_verifiers(self, *, input_hash: str | None = None) -> None:
        for verification_type in ('source_fidelity', 'structural_consistency', 'mathematical_independent', 'asset_semantics'):
            self._verification(verification_type, 'pass', input_hash=input_hash)

    def test_complete_current_candidate_writes_pass_without_formal_mapping(self) -> None:
        evidence_id = self._record_candidate()
        self._add_four_pass_verifiers()
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        self.assertEqual(result.audit_status, 'pass')
        row = self.conn.execute("SELECT audit_status, validator_bundle_id, validator_bundle_version FROM question_auto_mapping_audit_logs WHERE id=?", (result.audit_id,)).fetchone()
        self.assertEqual(tuple(row), ('pass', 'question-auto-mapping-audit', '2.0.0'))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM current_question_auto_mapping_audits").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM questions WHERE quality_status='approved' OR review_status='approved'").fetchone()[0], 0)

    def test_incomplete_required_knowledge_and_unsupported_validator_become_unsupported(self) -> None:
        evidence_id = self._record_candidate(features=self._candidate_features(complete=False))
        self._add_four_pass_verifiers()
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        self.assertEqual(result.audit_status, 'unsupported')
        self.assertIn('required_knowledge_missing', result.findings['feature_findings'])

        self.conn.execute("DELETE FROM question_auto_mapping_audit_logs")
        self.conn.execute("DELETE FROM question_curriculum_mapping_evidence")
        self.conn.execute("DELETE FROM question_verifications")
        evidence_id = self._record_candidate()
        self._add_four_pass_verifiers()
        self.conn.execute("DELETE FROM question_verifications WHERE verification_type='asset_semantics'")
        self._verification('asset_semantics', 'unsupported', evidence={'reason': 'no image'})
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        self.assertEqual(result.audit_status, 'unsupported')
        self.assertIn('validator_unsupported:asset_semantics', result.findings['validator_findings'])

    def test_question_signal_structure_blocks_incomplete_draft_even_with_four_pass_validators(self) -> None:
        self.conn.execute(
            """UPDATE questions
            SET stem=?,
                options_json=?,
                analysis=?,
                answer='C',
                content_hash='question-hash-q003-like'
            WHERE id='question'""",
            (
                '下列说法错误的是',
                json.dumps([
                    'A. 锐角三角形的三条高线、三条中线、三条角平分线分别交于一点',
                    'B. 钝角三角形有两条高线在三角形的外部',
                    'C. 直角三角形只有一条高线',
                    'D. 任意三角形都有三条高线、中线、角平分线',
                ], ensure_ascii=False),
                '解析：任意三角形的三条高线、三条中线、三条角平分线分别交于一点；直角三角形也有三条高线。',
            ),
        )
        refresh_current_snapshot(self.conn, 'question')
        self.conn.execute("DELETE FROM question_curriculum_mapping_evidence")
        self.conn.execute("DELETE FROM question_verifications")
        evidence_id = self._record_candidate(features={
            'core_theme': '直角三角形基础性质',
            'required_knowledge': ['识别直角三角形或90°条件', '仅使用当前进度内的直角三角形基础性质'],
            'required_knowledge_evidence': {
                '识别直角三角形或90°条件': {'supported': True, 'patterns': ['直角三角形', '90°']},
                '仅使用当前进度内的直角三角形基础性质': {'supported': True, 'patterns': ['直角三角形']},
            },
        })
        self._add_four_pass_verifiers()
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        self.assertEqual(result.audit_status, 'unsupported')
        self.assertIn('required_knowledge_incomplete_against_question_signals', result.findings['feature_findings'])
        self.assertIn('question_signal_rule:triangle_centers_and_special_lines', result.findings['feature_findings'])

    def test_question_signal_check_allows_inner_angle_sum_candidate_when_structure_matches(self) -> None:
        evidence_id = self._record_candidate(features={
            'core_theme': '三角形内角和',
            'required_knowledge': ['识别三角形的三个内角', '应用三角形内角和为180°'],
            'required_knowledge_evidence': {
                '识别三角形的三个内角': {'supported': True, 'patterns': ['三角形', '内角']},
                '应用三角形内角和为180°': {'supported': True, 'patterns': ['内角和', '180°']},
            },
        })
        self._add_four_pass_verifiers()
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        self.assertEqual(result.audit_status, 'pass')

    def test_missing_or_failed_validator_never_passes(self) -> None:
        evidence_id = self._record_candidate()
        self._add_four_pass_verifiers()
        self.conn.execute("DELETE FROM question_verifications WHERE verification_type='asset_semantics'")
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        self.assertEqual(result.audit_status, 'unsupported')
        self.assertIn('validator_missing:asset_semantics', result.findings['validator_findings'])

        self.conn.execute("DELETE FROM question_auto_mapping_audit_logs")
        self.conn.execute("DELETE FROM question_verifications")
        self._add_four_pass_verifiers()
        self.conn.execute("DELETE FROM question_verifications WHERE verification_type='mathematical_independent'")
        self._verification('mathematical_independent', 'fail')
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        self.assertEqual(result.audit_status, 'unsupported')
        self.assertIn('validator_fail:mathematical_independent', result.findings['validator_findings'])

    def test_non_candidate_conflict_and_forged_insert_are_rejected(self) -> None:
        non_candidate_id = self._record_candidate(status='unsupported')
        self._add_four_pass_verifiers()
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=non_candidate_id))
        self.assertEqual(result.audit_status, 'unsupported')
        self.assertEqual(result.decision_basis, 'non_candidate_current')

        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('book2', '教材2', 'catalog-v1')")
        self.conn.execute("INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, catalog_version) VALUES ('node2', 'book2', '初中', '八年级', 'topic', '三角形', 'catalog-v1')")
        self.conn.execute("INSERT INTO controlled_import_runs (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status) VALUES ('catalog-import-2', 'catalog', 'fixture://catalog2', 'catalog-hash-2', 'manifest-hash-2', 'fixture', '1', 'validated')")
        self.conn.execute("INSERT INTO catalog_releases (id, textbook_id, catalog_version, source_reference, source_hash, status) VALUES ('catalog-release-2', 'book2', 'catalog-v1', 'fixture://catalog2', 'catalog-hash-2', 'approved')")
        self.conn.execute("INSERT INTO catalog_audits (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id, audit_method, source_reference, source_hash, status) VALUES ('catalog-audit-2', 'catalog-release-2', 'catalog-import-2', 'audit-hash-2', 'fixture', 'fixture', 'fixture://catalog2', 'catalog-hash-2', 'approved')")
        evidence1 = self._record_candidate(features={**self._candidate_features(), 'branch': 'book1'})
        self._record_candidate(textbook_id='book2', node_id='node2', features={**self._candidate_features(), 'branch': 'book2'})
        self.conn.execute("DELETE FROM question_verifications")
        self._add_four_pass_verifiers()
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence1))
        self.assertEqual(result.audit_status, 'unsupported')
        self.assertIn('candidate_conflict', result.findings['conflict_findings'])

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_auto_mapping_audit_logs
                (id, evidence_id, question_id, textbook_id, curriculum_node_id, audit_status, decision_basis, blocked_reason,
                 question_input_hash, question_snapshot_revision, evidence_input_hash,
                 evidence_question_snapshot_revision, evidence_hash, textbook_catalog_version, node_catalog_version,
                 validator_bundle_id, validator_bundle_version, validator_results_hash,
                 validator_results_json, findings_json, auditor_id, auditor_version, audit_json, audit_hash)
                VALUES ('forged', ?, 'question', 'book', 'node', 'pass', 'current_candidate', NULL, 'bad', 1, 'bad', 1,
                        'bad-evidence-hash', 'catalog-v1', 'catalog-v1', 'bundle', '1', 'hash', '{}', '{}', 'auditor', '1', '{}', 'audit')""",
                (evidence1,),
            )

    def test_current_view_fails_closed_after_input_catalog_or_source_changes(self) -> None:
        evidence_id = self._record_candidate()
        current_hash = self.conn.execute("SELECT input_hash FROM question_input_snapshots WHERE question_id='question'").fetchone()[0]
        self._add_four_pass_verifiers(input_hash=current_hash)
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM current_question_auto_mapping_audits WHERE id=?", (result.audit_id,)).fetchone()[0], 1)

        self.conn.execute("UPDATE questions SET stem='新题干' WHERE id='question'")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM current_question_auto_mapping_audits WHERE id=?", (result.audit_id,)).fetchone()[0], 0)
        refresh_current_snapshot(self.conn, 'question')
        self.conn.execute("UPDATE source_documents SET trusted_source=0 WHERE id='source'")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM current_question_auto_mapping_audits WHERE id=?", (result.audit_id,)).fetchone()[0], 0)
        self.conn.execute("UPDATE source_documents SET trusted_source=1 WHERE id='source'")
        self.conn.execute("UPDATE textbooks SET catalog_version='catalog-v2' WHERE id='book'")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM current_question_auto_mapping_audits WHERE id=?", (result.audit_id,)).fetchone()[0], 0)

    def test_stale_validator_input_hash_is_unsupported_and_duplicate_insert_conflicts(self) -> None:
        evidence_id = self._record_candidate()
        self._add_four_pass_verifiers()
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        self.assertEqual(result.audit_status, 'pass')
        with self.assertRaises(sqlite3.IntegrityError):
            record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))

        self.conn.execute("DELETE FROM question_auto_mapping_audit_logs")
        self.conn.execute("DELETE FROM question_verifications")
        self._add_four_pass_verifiers(input_hash='stale-hash')
        blocked = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        self.assertEqual(blocked.audit_status, 'unsupported')
        self.assertIn('validator_missing:source_fidelity', blocked.findings['validator_findings'])

    def test_migration_is_idempotent(self) -> None:
        apply_schema_v2_25(self.path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone()[0], 1)

    def test_pass_bundle_requires_exact_four_validator_keys(self) -> None:
        evidence_id = self._record_candidate()
        self._add_four_pass_verifiers()
        result = record_auto_mapping_audit(self.conn, AutoMappingAuditRequest(evidence_id=evidence_id))
        row = self.conn.execute(
            "SELECT validator_results_json, question_input_hash FROM question_auto_mapping_audit_logs WHERE id=?",
            (result.audit_id,),
        ).fetchone()
        question_input_hash = row[1]

        self.conn.execute(
            "CREATE TEMP TABLE question_auto_mapping_audit_logs_backup_source AS SELECT * FROM question_auto_mapping_audit_logs WHERE id=?",
            (result.audit_id,),
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_auto_mapping_audit_logs
                (id, evidence_id, question_id, textbook_id, curriculum_node_id, audit_status, decision_basis, blocked_reason,
                 question_input_hash, question_snapshot_revision, evidence_input_hash,
                 evidence_question_snapshot_revision, evidence_hash, textbook_catalog_version, node_catalog_version,
                 validator_bundle_id, validator_bundle_version, validator_results_hash,
                 validator_results_json, findings_json, auditor_id, auditor_version, audit_json, audit_hash)
                SELECT 'forged-empty', evidence_id, question_id, textbook_id, curriculum_node_id, 'pass', decision_basis, NULL,
                       question_input_hash, question_snapshot_revision, evidence_input_hash,
                       evidence_question_snapshot_revision, evidence_hash, textbook_catalog_version, node_catalog_version,
                       validator_bundle_id, validator_bundle_version, 'hash-empty',
                       '{}', findings_json, auditor_id, auditor_version, audit_json, 'audit-empty'
                FROM question_auto_mapping_audit_logs_backup_source""",
            )

        forged_missing = json.dumps({
            'source_fidelity': {'validator_id': 'source_fidelity-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': question_input_hash},
            'structural_consistency': {'validator_id': 'structural_consistency-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': question_input_hash},
            'mathematical_independent': {'validator_id': 'mathematical_independent-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': question_input_hash},
        }, ensure_ascii=False, sort_keys=True)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_auto_mapping_audit_logs
                (id, evidence_id, question_id, textbook_id, curriculum_node_id, audit_status, decision_basis, blocked_reason,
                 question_input_hash, question_snapshot_revision, evidence_input_hash,
                 evidence_question_snapshot_revision, evidence_hash, textbook_catalog_version, node_catalog_version,
                 validator_bundle_id, validator_bundle_version, validator_results_hash,
                 validator_results_json, findings_json, auditor_id, auditor_version, audit_json, audit_hash)
                SELECT 'forged-missing', evidence_id, question_id, textbook_id, curriculum_node_id, 'pass', decision_basis, NULL,
                       question_input_hash, question_snapshot_revision, evidence_input_hash,
                       evidence_question_snapshot_revision, evidence_hash, textbook_catalog_version, node_catalog_version,
                       validator_bundle_id, validator_bundle_version, 'hash-missing',
                       ?, findings_json, auditor_id, auditor_version, audit_json, 'audit-missing'
                FROM question_auto_mapping_audit_logs_backup_source""",
                (forged_missing,),
            )

        forged_extra = json.dumps({
            'source_fidelity': {'validator_id': 'source_fidelity-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': question_input_hash},
            'structural_consistency': {'validator_id': 'structural_consistency-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': question_input_hash},
            'mathematical_independent': {'validator_id': 'mathematical_independent-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': question_input_hash},
            'asset_semantics': {'validator_id': 'asset_semantics-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': question_input_hash},
            'unexpected': {'validator_id': 'unexpected-validator', 'validator_version': '1.0.0', 'status': 'pass', 'input_hash': question_input_hash},
        }, ensure_ascii=False, sort_keys=True)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_auto_mapping_audit_logs
                (id, evidence_id, question_id, textbook_id, curriculum_node_id, audit_status, decision_basis, blocked_reason,
                 question_input_hash, question_snapshot_revision, evidence_input_hash,
                 evidence_question_snapshot_revision, evidence_hash, textbook_catalog_version, node_catalog_version,
                 validator_bundle_id, validator_bundle_version, validator_results_hash,
                 validator_results_json, findings_json, auditor_id, auditor_version, audit_json, audit_hash)
                SELECT 'forged-extra', evidence_id, question_id, textbook_id, curriculum_node_id, 'pass', decision_basis, NULL,
                       question_input_hash, question_snapshot_revision, evidence_input_hash,
                       evidence_question_snapshot_revision, evidence_hash, textbook_catalog_version, node_catalog_version,
                       validator_bundle_id, validator_bundle_version, 'hash-extra',
                       ?, findings_json, auditor_id, auditor_version, audit_json, 'audit-extra'
                FROM question_auto_mapping_audit_logs_backup_source""",
                (forged_extra,),
            )


if __name__ == '__main__':
    unittest.main()
