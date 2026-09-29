from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automatic_verification_runner as runner

ROOT = Path(__file__).parent
SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14")
)


class AutomaticVerificationRunnerTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = Path(handle.name)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self.temp_root = Path(tempfile.mkdtemp())
        self.doc1 = self.temp_root / "docs" / "source1.docx"
        self.doc1.parent.mkdir(parents=True, exist_ok=True)
        self.doc1.write_bytes(b"doc1")
        self.doc2 = self.temp_root / "docs" / "source2.docx"
        self.doc2.write_bytes(b"doc2")
        self.golden = self.temp_root / "data" / "dev" / "golden-samples" / "golden_source_001.docx"
        self.golden.parent.mkdir(parents=True, exist_ok=True)
        self.golden.write_bytes(b"golden")
        self.conn.execute(
            "INSERT INTO source_documents (id, relative_path, file_hash, file_type, parse_status) VALUES ('s1', ?, 'hash-1', 'docx', 'parsed')",
            (str(self.doc1.relative_to(self.temp_root)),),
        )
        self.conn.execute(
            "INSERT INTO source_documents (id, relative_path, file_hash, file_type, parse_status) VALUES ('s2', ?, 'hash-2', 'docx', 'parsed')",
            (str(self.doc2.relative_to(self.temp_root)),),
        )
        self.conn.execute(
            "INSERT INTO source_documents (id, relative_path, file_hash, file_type, parse_status) VALUES ('s3', 'docs/source3.pdf', 'hash-3', 'pdf', 'parsed')"
        )
        self.conn.execute(
            "INSERT INTO source_documents (id, relative_path, file_hash, file_type, parse_status) VALUES ('s4', 'docs/missing.docx', 'hash-4', 'docx', 'parsed')"
        )
        self.conn.execute(
            "INSERT INTO source_documents (id, relative_path, file_hash, file_type, parse_status) VALUES ('s5', 'docs/unparsed.docx', 'hash-5', 'docx', 'failed')"
        )
        self._insert_question('q1', 's1', '题干一', '["A. 1", "B. 2"]', 'A', '解析一', '1')
        self._insert_question('q2', 's2', '题干二', '["A. 3", "B. 4"]', 'B', '解析二', '2')
        self._insert_question('q3', 's3', '题干三', '["A. 5", "B. 6"]', 'A', '解析三', '3')
        self._insert_question('q4', 's4', '题干四', '["A. 7", "B. 8"]', 'A', '解析四', '4')
        self._insert_question('q5', 's5', '题干五', '["A. 9", "B. 10"]', 'A', '解析五', '5')
        self.conn.execute("INSERT INTO source_fragments (id, source_document_id, location_type, paragraph_index, raw_text, raw_hash) VALUES ('s1-p001', 's1', 'paragraph', 1, '题干一 A. 1 B. 2 A 解析一', 'fh1')")
        self.conn.execute("INSERT INTO source_fragments (id, source_document_id, location_type, paragraph_index, raw_text, raw_hash) VALUES ('s2-p001', 's2', 'paragraph', 1, '题干二 A. 3 B. 4 B 解析二', 'fh2')")
        for field in ('stem', 'options', 'answer', 'analysis'):
            self.conn.execute("INSERT INTO question_source_fragments (question_id, source_fragment_id, field_name, source_hash) VALUES ('q1', 's1-p001', ?, 'fh1')", (field,))
            self.conn.execute("INSERT INTO question_source_fragments (question_id, source_fragment_id, field_name, source_hash) VALUES ('q2', 's2-p001', ?, 'fh2')", (field,))
        self.conn.execute("UPDATE source_documents SET file_hash=? WHERE id='s1'", (__import__('hashlib').sha256(self.doc1.read_bytes()).hexdigest(),))
        self.conn.execute("UPDATE source_documents SET file_hash=? WHERE id='s2'", (__import__('hashlib').sha256(self.doc2.read_bytes()).hexdigest(),))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.db_path.unlink(missing_ok=True)

    def _insert_question(self, question_id, source_document_id, stem, options_json, answer, analysis, no):
        self.conn.execute(
            """INSERT INTO questions
            (id, stem, options_json, answer, analysis, question_type, stage, source_document_id, source_question_no, content_hash, quality_status, review_status)
            VALUES (?, ?, ?, ?, ?, '选择题', '初中', ?, ?, ?, 'needs_review', 'pending')""",
            (question_id, stem, options_json, answer, analysis, source_document_id, no, f'content-{question_id}'),
        )

    def _run(self):
        paragraphs = {
            str(self.doc1): [runner.ParagraphFragment(index=1, text='题干一 A. 1 B. 2 A 解析一', sha256='fh1')],
            str(self.doc2): [runner.ParagraphFragment(index=1, text='题干二 A. 3 B. 4 B 解析二', sha256='fh2')],
        }

        def fake_extract(path):
            return paragraphs[str(path)]

        class FakeMathResult:
            def __init__(self, status, evidence, computed_answer=None):
                self.status = status
                self.evidence = evidence
                self.computed_answer = computed_answer

        def fake_math(question):
            if question['source_question_no'] == '1':
                return FakeMathResult('pass', 'ok', 'A')
            if question['source_question_no'] == '2':
                return FakeMathResult('pass', 'ok', 'B')
            return FakeMathResult('unsupported', '题型未覆盖', None)

        class FakeTextbookResult:
            def __init__(self, status, evidence):
                self.status = status
                self.evidence = evidence

        def fake_textbook(_conn, question_id):
            return FakeTextbookResult('unsupported' if question_id == 'q3' else 'pass', {'detail': question_id})

        def fake_asset(_conn, question_id):
            return ('unsupported', {'reason': '无图规则未覆盖'}) if question_id == 'q3' else ('pass', {'detail': question_id})

        with mock.patch.object(runner, 'ROOT', self.temp_root), \
             mock.patch.object(runner, 'DB_PATH', self.db_path), \
             mock.patch.object(runner, 'extract_paragraphs', side_effect=fake_extract), \
             mock.patch.object(runner, 'validate_math', side_effect=fake_math), \
             mock.patch.object(runner, 'validate_textbook_scope', side_effect=fake_textbook), \
             mock.patch.object(runner, 'validate_asset_semantics', side_effect=fake_asset):
            runner.main()

    def _latest_evidence(self, question_id, verification_type):
        row = self.conn.execute(
            "SELECT status, evidence_json FROM question_verifications WHERE question_id=? AND verification_type=? ORDER BY rowid DESC LIMIT 1",
            (question_id, verification_type),
        ).fetchone()
        return row['status'], json.loads(row['evidence_json'])

    def test_routes_each_docx_question_to_its_bound_source_document(self):
        self._run()
        status1, evidence1 = self._latest_evidence('q1', 'source_fidelity')
        status2, evidence2 = self._latest_evidence('q2', 'source_fidelity')
        self.assertEqual(status1, 'pass')
        self.assertEqual(status2, 'pass')
        self.assertEqual(evidence1['source_document_id'], 's1')
        self.assertEqual(evidence2['source_document_id'], 's2')
        self.assertNotEqual(evidence1['source_path'], evidence2['source_path'])
        self.assertEqual(evidence1['coverage'], 'full')
        self.assertIsNone(evidence1['unsupported_reason'])

    def test_unsupported_source_file_type_is_recorded_structurally(self):
        self._run()
        status, evidence = self._latest_evidence('q3', 'source_fidelity')
        self.assertEqual(status, 'unsupported')
        self.assertEqual(evidence['source_document_id'], 's3')
        self.assertEqual(evidence['coverage'], 'none')
        self.assertEqual(evidence['unsupported_reason'], 'unsupported_source_document_type:pdf')

    def test_missing_path_and_parse_failure_are_recorded_structurally(self):
        self._run()
        missing_status, missing_evidence = self._latest_evidence('q4', 'source_fidelity')
        failed_status, failed_evidence = self._latest_evidence('q5', 'source_fidelity')
        self.assertEqual(missing_status, 'unsupported')
        self.assertEqual(missing_evidence['unsupported_reason'], 'source_document_missing')
        self.assertEqual(missing_evidence['coverage'], 'none')
        self.assertEqual(failed_status, 'unsupported')
        self.assertEqual(failed_evidence['unsupported_reason'], 'source_document_parse_status:failed')

    def test_archive_hash_mismatch_is_recorded_and_not_parsed(self):
        self.conn.execute("UPDATE source_documents SET file_hash='wrong-hash' WHERE id='s1'")
        self.conn.commit()
        self._run()
        status, evidence = self._latest_evidence('q1', 'source_fidelity')
        self.assertEqual(status, 'unsupported')
        self.assertEqual(evidence['coverage'], 'none')
        self.assertEqual(evidence['unsupported_reason'], 'source_file_hash_mismatch')

    def test_all_verification_evidence_includes_structured_source_metadata(self):
        self._run()
        rows = self.conn.execute("SELECT verification_type, evidence_json FROM question_verifications WHERE question_id='q3'").fetchall()
        payloads = {row['verification_type']: json.loads(row['evidence_json']) for row in rows}
        for evidence in payloads.values():
            self.assertIn('source_document_id', evidence)
            self.assertIn('coverage', evidence)
            self.assertIn('unsupported_reason', evidence)
        self.assertEqual(payloads['mathematical_independent']['coverage'], 'none')
        self.assertEqual(payloads['mathematical_independent']['unsupported_reason'], '题型未覆盖')
        self.assertEqual(payloads['asset_semantics']['unsupported_reason'], '无图规则未覆盖')

    def test_existing_golden_docx_flow_remains_compatible(self):
        resolver = runner.SourceDocumentResolver(self.temp_root)
        with mock.patch.object(runner, 'extract_paragraphs', return_value=[runner.ParagraphFragment(index=7, text='demo', sha256='h')]):
            result = resolver.load('golden-source-001', 'data/dev/golden-samples/golden_source_001.docx', 'docx', 'parsed', __import__('hashlib').sha256(self.golden.read_bytes()).hexdigest())
        self.assertEqual(result['status'], 'loaded')
        self.assertEqual(result['coverage'], 'full')
        self.assertIn('golden-source-001-p007', result['fragments'])


if __name__ == '__main__':
    unittest.main()
