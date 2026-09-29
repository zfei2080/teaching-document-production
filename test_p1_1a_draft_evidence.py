from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apply_schema_v2_16 import apply_schema as apply_schema_v2_16
from apply_schema_v2_17 import apply_schema as apply_schema_v2_17
from apply_schema_v2_18 import apply_schema as apply_schema_v2_18
from apply_schema_v2_19 import apply_schema as apply_schema_v2_19
from apply_schema_v2_20 import apply as apply_schema_v2_20
from apply_schema_v2_21 import apply as apply_schema_v2_21
from apply_schema_v2_22 import apply as apply_schema_v2_22
from input_snapshot import refresh_current_snapshot
from run_p1_1a_draft_evidence import RUNNER_ID, RUNNER_VERSION, run

ROOT = Path(__file__).parent
BASE_SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15")
)


class P1_1ADraftEvidenceTests(unittest.TestCase):
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
        for apply in (
            apply_schema_v2_16,
            apply_schema_v2_17,
            apply_schema_v2_18,
            apply_schema_v2_19,
            apply_schema_v2_20,
            apply_schema_v2_21,
            apply_schema_v2_22,
        ):
            apply(self.db_path)
        self._seed_files()
        self._seed_database()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _seed_files(self) -> None:
        original = self.workspace / "<local-scratch>"
        archive = self.workspace / "data" / "history" / "source-documents"
        extractor = self.workspace / "data" / "extractor"
        original.mkdir(parents=True, exist_ok=True)
        archive.mkdir(parents=True, exist_ok=True)
        extractor.mkdir(parents=True, exist_ok=True)
        content = b"trusted fixture document"
        (original / "2026-07-15初中数学合格性考试-自定义类型 (1).docx").write_bytes(content)
        (archive / "2026-07-15初中数学合格性考试-自定义类型 (1).docx").write_bytes(content)
        (extractor / "golden-source-copy.docx").write_bytes(content)

    def _seed_database(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """INSERT INTO source_documents
                (id, relative_path, file_hash, file_type, source_label, copyright_status, parse_status)
                VALUES (?, ?, ?, 'docx', 'fixture', 'authorized', 'parsed')""",
                ("golden-source-001", "data/extractor/golden-source-copy.docx", self._file_hash()),
            )
            conn.execute(
                "INSERT INTO source_fragments (id, source_document_id, location_type, raw_text, raw_hash) VALUES ('fragment-1', 'golden-source-001', 'paragraph', '原题', 'fragment-hash')"
            )
            conn.execute(
                "INSERT INTO textbooks (id, name, subject, publisher, status, catalog_version) VALUES (?, '北师大版八下', '数学', '北师大', 'active', 'catalog-v1')",
                ("bsd-math-grade8-lower-2026-spring-extsrc",),
            )
            conn.execute(
                """INSERT INTO controlled_import_runs
                (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status)
                VALUES ('catalog-import', 'catalog', 'fixture://catalog', 'catalog-hash', 'manifest-hash', 'fixture', '1', 'validated')"""
            )
            conn.execute(
                """INSERT INTO catalog_releases
                (id, textbook_id, catalog_version, source_reference, source_hash, status)
                VALUES ('catalog-release', ?, 'catalog-v1', 'fixture://catalog', 'catalog-hash', 'approved')""",
                ("bsd-math-grade8-lower-2026-spring-extsrc",),
            )
            conn.execute(
                """INSERT INTO catalog_audits
                (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id, audit_method, source_reference, source_hash, status)
                VALUES ('catalog-audit', 'catalog-release', 'catalog-import', 'audit-hash', 'fixture', 'fixture', 'fixture://catalog', 'catalog-hash', 'approved')"""
            )
            self._seed_curriculum(conn)
            self._seed_questions(conn)
            conn.commit()
        finally:
            conn.close()

    def _seed_curriculum(self, conn: sqlite3.Connection) -> None:
        book = "bsd-math-grade8-lower-2026-spring-extsrc"
        nodes = [
            ("root", None, "chapter", "第一章", 1),
            ("bsd-math-8x-2026-node-01", None, "chapter", "第二章", 2),
            ("bsd-math-8x-2026-node-01-section-01", "bsd-math-8x-2026-node-01", "topic", "三角形内角和定理", 1),
            ("bsd-math-8x-2026-node-01-section-02", "bsd-math-8x-2026-node-01", "topic", "等腰三角形", 2),
            ("bsd-math-8x-2026-node-01-section-03", "bsd-math-8x-2026-node-01", "topic", "直角三角形", 3),
            ("bsd-math-8x-2026-node-01-section-04", "bsd-math-8x-2026-node-01", "topic", "线段的垂直平分线", 4),
            ("bsd-math-8x-2026-node-01-section-05", "bsd-math-8x-2026-node-01", "topic", "角平分线", 5),
            ("future-node", None, "chapter", "第三章", 3),
        ]
        for node_id, parent_id, node_type, name, sequence in nodes:
            conn.execute(
                """INSERT INTO curriculum_nodes
                (id, textbook_id, parent_id, stage, grade_level, node_type, name, sequence, catalog_version, status)
                VALUES (?, ?, ?, '初中', '八年级', ?, ?, ?, 'catalog-v1', 'active')""",
                (node_id, book, parent_id, node_type, name, sequence),
            )

    def _seed_questions(self, conn: sqlite3.Connection) -> None:
        texts = [
            ("q01", "1", "已知三角形内角和定理，三角形三个内角中两个角之和为100，内角和180，求第三个角。", "candidate"),
            ("q02", "2", "在等腰三角形中，底角相等，已知一个底角为40，求另一个底角。", "candidate"),
            ("q03", "3", "已知角平分线上的点到角两边距离相等，求相关线段长度。", "candidate"),
            ("q04", "4", "题目提到直角三角形背景，但没有90°条件，也没有任何当前章节性质可证。", "unsupported"),
            ("q05", "5", "如图是三角形问题，但无法确定使用哪条已学性质。", "unsupported"),
            ("q06", "6", "题目提到三角形内角和定理背景，但没有内角和结论数值证据。", "unsupported"),
            ("q07", "7", "题目提到等腰三角形背景，但没有底角相等或两腰相等的明确证据。", "unsupported"),
            ("q08", "8", "题目提到线段的垂直平分线背景，但没有垂直平分线定义，只给出模糊长度关系。", "unsupported"),
            ("q09", "9", "题目提到角平分线背景，但没有点到角两边距离相等的性质证据。", "unsupported"),
            ("q10", "10", "直角三角形题，后续要用勾股定理求边长。", "rejected"),
            ("q11", "11", "已知正方形ABCD，连接对角线。", "rejected"),
            ("q12", "12", "在平行四边形中研究角度关系。", "rejected"),
            ("q13", "13", "已知⊙O的半径为5，求弦长。", "rejected"),
            ("q14", "14", "已知圆心O到点A的距离是半径。", "rejected"),
            ("q15", "15", "两个三角形相似，求对应边。", "rejected"),
            ("q16", "16", "利用三角函数sin求高。", "rejected"),
            ("q17", "17", "利用tan计算坡度。", "rejected"),
            ("q18", "18", "研究正方体表面展开图。", "rejected"),
            ("q19", "19", "涉及余弦公式前置的cos表达。", "rejected"),
            ("q20", "20", "普通阅读题，没有足够主题证据。", "unsupported"),
        ]
        for question_id, no, stem, _expected in texts:
            conn.execute(
                """INSERT INTO questions
                (id, stem, options_json, answer, analysis, question_type, stage, grade_level, source_document_id, source_fragment_id, source_question_no, source_page, content_hash)
                VALUES (?, ?, '[]', '', '', '解答题', '初中', '八年级', 'golden-source-001', 'fragment-1', ?, 1, ?)""",
                (question_id, stem, no, f"hash-{question_id}"),
            )
            refresh_current_snapshot(conn, question_id)

    def _file_hash(self) -> str:
        return __import__("hashlib").sha256(b"trusted fixture document").hexdigest()

    def test_runner_writes_only_draft_evidence_and_binding_on_temp_copy(self) -> None:
        output = self.workspace / "output" / "audit.json"
        with mock.patch("run_p1_1a_draft_evidence.Path.resolve", return_value=self.workspace / "run_p1_1a_draft_evidence.py"):
            report = run(self.db_path, output)
        self.assertTrue(output.is_file())
        summary = report["summary"]
        self.assertEqual(summary["question_count"], 20)
        self.assertEqual(summary["approved_count"], 0)
        self.assertEqual(summary["teaching_documents_count"], 0)
        self.assertEqual(summary["quality_reports_count"], 0)
        self.assertEqual(summary["question_usage_count"], 0)
        self.assertEqual(summary["statuses"], {"candidate": 7, "unsupported": 3, "rejected": 10})
        for row in report["questions"]:
            self.assertIn("core_theme", row["features"])
            self.assertIn("required_knowledge", row["features"])
            self.assertIn("required_knowledge_evidence", row["features"])
        conn = sqlite3.connect(self.db_path)
        try:
            manifest = conn.execute(
                "SELECT original_relative_path, original_file_hash, trusted_source, intake_manifest_json FROM source_documents WHERE id='golden-source-001'"
            ).fetchone()
            self.assertEqual(manifest[0], "<local-scratch>/2026-07-15初中数学合格性考试-自定义类型 (1).docx")
            self.assertEqual(manifest[1], self._file_hash())
            self.assertEqual(manifest[2], 1)
            parsed = json.loads(manifest[3])
            self.assertEqual(set(parsed.keys()) & {"original", "history_archive", "extractor_copy"}, {"original", "history_archive", "extractor_copy"})
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM question_curriculum_mapping_evidence").fetchone()[0],
                20,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM teaching_documents").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM quality_reports").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM question_usage").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM questions WHERE quality_status='approved' OR review_status='approved'").fetchone()[0],
                0,
            )
        finally:
            conn.close()

    def test_report_failure_rolls_back_database(self) -> None:
        output = self.workspace / "output" / "audit.json"
        with mock.patch("run_p1_1a_draft_evidence.Path.resolve", return_value=self.workspace / "run_p1_1a_draft_evidence.py"):
            with mock.patch("run_p1_1a_draft_evidence._write_report_atomic", side_effect=RuntimeError("report_write_failed")):
                with self.assertRaisesRegex(RuntimeError, "report_write_failed"):
                    run(self.db_path, output)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM question_curriculum_mapping_evidence").fetchone()[0], 0)
            row = conn.execute(
                "SELECT original_relative_path, original_file_hash, trusted_source, intake_manifest_json FROM source_documents WHERE id='golden-source-001'"
            ).fetchone()
            self.assertEqual(row, (None, None, 0, None))
        finally:
            conn.close()
        self.assertFalse(output.exists())

    def test_authorizer_blocks_unauthorized_writes(self) -> None:
        from run_p1_1a_draft_evidence import guarded_connection

        with guarded_connection(self.db_path) as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE questions SET stem='bad' WHERE id='q01'")
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("INSERT INTO teaching_documents (id, request_id, ruleset_id, document_type, audience, status) VALUES ('d', 'r', 'r', 'worksheet', 'student', 'draft')")


if __name__ == "__main__":
    unittest.main()
