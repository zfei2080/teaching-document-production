"""Tests for question_classifier.py"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent

BASE_SCHEMAS = tuple(
    f"schema_v2{s}.sql"
    for s in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9",
              "_10", "_11", "_12", "_13", "_14", "_15")
)

from apply_schema_v2_16 import apply_schema as apply_v16
from apply_schema_v2_17 import apply_schema as apply_v17
from apply_schema_v2_18 import apply_schema as apply_v18
from apply_schema_v2_19 import apply_schema as apply_v19
from apply_schema_v2_20 import apply as apply_v20
from question_classifier import (
    ClassificationWriteDisabled,
    classify_questions,
    extract_chinese_keywords,
    score_question_node,
    score_question_nodes,
)

TEXTBOOK_ID = "test-textbook-001"
CATALOG_RELEASE_ID = "test-catalog-release-001"
CATALOG_IMPORT_RUN_ID = "test-catalog-import-001"


def build_temp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    for s in BASE_SCHEMAS:
        conn.executescript((ROOT / s).read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    for fn in (apply_v16, apply_v17, apply_v18, apply_v19):
        fn(Path(path))
    apply_v20(path)
    return path


def seed_catalog(conn: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO textbooks (id, name, subject, publisher, status, catalog_version) "
        "VALUES (?, '测试教材', '数学', '测试出版社', 'active', 'test-v1')",
        (TEXTBOOK_ID,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO curriculum_nodes "
        "(id, textbook_id, parent_id, stage, grade_level, node_type, name, sequence, catalog_version, status) "
        "VALUES (?, ?, NULL, '初中', '八下', 'topic', '等腰三角形', 1, 'test-v1', 'active')",
        ("node-isosceles", TEXTBOOK_ID),
    )
    conn.execute(
        "INSERT OR IGNORE INTO curriculum_nodes "
        "(id, textbook_id, parent_id, stage, grade_level, node_type, name, sequence, catalog_version, status) "
        "VALUES (?, ?, NULL, '初中', '八下', 'topic', '三角形内角和定理', 2, 'test-v1', 'active')",
        ("node-angle-sum", TEXTBOOK_ID),
    )
    conn.execute(
        "INSERT OR IGNORE INTO catalog_releases "
        "(id, textbook_id, catalog_version, source_reference, source_hash, status) "
        "VALUES (?, ?, 'test-v1', 'http://example.com/test', 'aabbcc', 'approved')",
        (CATALOG_RELEASE_ID, TEXTBOOK_ID),
    )
    conn.execute(
        "INSERT OR IGNORE INTO controlled_import_runs "
        "(id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status, created_at) "
        "VALUES (?, 'catalog', 'http://example.com/test', 'aabbcc', 'ddeeff', "
        "'test_importer', 'v1', 'approved', ?)",
        (CATALOG_IMPORT_RUN_ID, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO catalog_release_imports (catalog_release_id, import_run_id) "
        "VALUES (?, ?)",
        (CATALOG_RELEASE_ID, CATALOG_IMPORT_RUN_ID),
    )
    conn.commit()


def seed_question(conn: sqlite3.Connection, qid: str, stem: str, stage: str = "初中") -> None:
    # source_document must exist before question (FK constraint)
    conn.execute(
        "INSERT OR IGNORE INTO source_documents "
        "(id, relative_path, file_hash, file_type, parse_status) "
        "VALUES ('test-src', 'test.docx', 'aabbccdd', 'docx', 'parsed')",
    )
    conn.execute(
        "INSERT OR IGNORE INTO questions "
        "(id, stem, question_type, difficulty, stage, source_document_id, content_hash, "
        "extraction_status, quality_status, review_status) "
        "VALUES (?, ?, '选择题', '中等', ?, 'test-src', 'hash-' || ?, 'candidate', 'pending', 'pending')",
        (qid, stem, stage, qid),
    )
    conn.commit()


class TestExtractChineseKeywords(unittest.TestCase):
    def test_extracts_2_to_4_char_substrings(self):
        kws = extract_chinese_keywords("等腰三角形")
        self.assertIn("等腰三角", kws)
        self.assertIn("腰三角形", kws)
        self.assertIn("等腰三", kws)
        self.assertIn("等腰", kws)

    def test_ignores_single_char_and_non_chinese(self):
        kws = extract_chinese_keywords("abc等d腰e")
        # only single Chinese chars between non-Chinese — no 2+ runs
        for kw in kws:
            self.assertGreaterEqual(len(kw), 2)

    def test_empty_returns_empty(self):
        self.assertEqual(extract_chinese_keywords("123 abc"), [])


class TestScoreQuestionNode(unittest.TestCase):
    def test_exact_node_label_has_full_candidate_score(self):
        self.assertEqual(
            score_question_node("等腰三角形的一边长为4cm", "等腰三角形"),
            1.0,
        )

    def test_high_score_for_matching_node(self):
        score = score_question_node("等腰三角形的一边长为4cm", "等腰三角形")
        self.assertGreaterEqual(score, 0.35)

    def test_zero_score_for_unrelated_node(self):
        score = score_question_node("等腰三角形的一边长为4cm", "一元二次方程")
        self.assertLess(score, 0.35)

    def test_returns_float(self):
        self.assertIsInstance(score_question_node("等腰三角形", "等腰三角形"), float)


class TestClassifyQuestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = build_temp_db()
        conn = sqlite3.connect(cls.db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        seed_catalog(conn)
        seed_question(conn, "q-isosceles", "等腰三角形的一边长为4cm，另一边长为8cm，则周长为多少")
        seed_question(conn, "q-unrelated", "今天天气很好，适合出门")
        conn.close()

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.db_path)
        except Exception:
            pass

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def test_3_dry_run_returns_result_without_writing(self):
        """dry_run=True returns matches but writes nothing to DB."""
        conn = self._conn()
        results = classify_questions(
            conn, ["q-isosceles"], TEXTBOOK_ID,
            confidence_threshold=0.30, dry_run=True,
        )
        conn.close()
        self.assertIn("q-isosceles", results)
        self.assertGreater(len(results["q-isosceles"]), 0)
        # verify nothing was written
        conn2 = self._conn()
        count = conn2.execute(
            "SELECT COUNT(*) FROM question_textbooks WHERE question_id='q-isosceles'"
        ).fetchone()[0]
        conn2.close()
        self.assertEqual(count, 0)

    def test_4_full_run_is_read_only(self):
        """Keyword classification cannot create scope or knowledge-point evidence."""
        conn = self._conn()
        results = classify_questions(
            conn, ["q-isosceles"], TEXTBOOK_ID,
            confidence_threshold=0.30,
        )
        conn.close()
        self.assertIn("q-isosceles", results)

        conn2 = self._conn()
        self.assertEqual(
            conn2.execute(
                "SELECT COUNT(*) FROM question_textbooks WHERE question_id='q-isosceles'"
            ).fetchone()[0],
            0,
        )
        conn2.close()

    def test_5_repeated_preview_is_read_only(self):
        """Repeated previews do not create mappings."""
        conn = self._conn()
        classify_questions(conn, ["q-isosceles"], TEXTBOOK_ID)
        conn.close()
        conn2 = self._conn()
        count_before = conn2.execute(
            "SELECT COUNT(*) FROM question_textbooks WHERE question_id='q-isosceles'"
        ).fetchone()[0]
        conn2.close()
        conn3 = self._conn()
        classify_questions(conn3, ["q-isosceles"], TEXTBOOK_ID)
        conn3.close()
        conn4 = self._conn()
        count_after = conn4.execute(
            "SELECT COUNT(*) FROM question_textbooks WHERE question_id='q-isosceles'"
        ).fetchone()[0]
        conn4.close()
        self.assertEqual(count_before, 0)
        self.assertEqual(count_after, 0)

    def test_legacy_write_helper_is_disabled(self):
        conn = self._conn()
        from question_classifier import write_classification_results
        with self.assertRaises(ClassificationWriteDisabled):
            write_classification_results(conn, "q-isosceles", TEXTBOOK_ID, [], "run", "classifier")
        conn.close()

    def test_6_low_confidence_not_written(self):
        """Unrelated question gets no mapping written."""
        conn = self._conn()
        results = classify_questions(conn, ["q-unrelated"], TEXTBOOK_ID)
        conn.close()
        # Either no entry or empty matches
        matches = results.get("q-unrelated", [])
        self.assertEqual(len(matches), 0)
        conn2 = self._conn()
        count = conn2.execute(
            "SELECT COUNT(*) FROM question_textbooks WHERE question_id='q-unrelated'"
        ).fetchone()[0]
        conn2.close()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
