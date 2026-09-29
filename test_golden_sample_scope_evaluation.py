"""Tests for the read-only golden-sample scope audit utility."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from golden_sample_scope_evaluation import (
    DEFAULT_TEXTBOOK_ID,
    evaluate_golden_sample_scope,
    sha256_file,
    write_report,
)


def make_fixture(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE textbooks (id TEXT PRIMARY KEY, name TEXT, catalog_version TEXT, status TEXT);
        CREATE TABLE curriculum_nodes (
            id TEXT PRIMARY KEY, textbook_id TEXT, parent_id TEXT, node_type TEXT,
            name TEXT, sequence INTEGER, catalog_version TEXT, status TEXT
        );
        CREATE TABLE questions (id TEXT PRIMARY KEY, source_document_id TEXT, source_question_no TEXT, stem TEXT);
        """
    )
    conn.execute("INSERT INTO textbooks VALUES (?, '北师大版八下', 'test-v1', 'active')", (DEFAULT_TEXTBOOK_ID,))
    conn.executemany(
        "INSERT INTO curriculum_nodes VALUES (?, ?, ?, ?, ?, ?, 'test-v1', 'active')",
        [
            ("term", DEFAULT_TEXTBOOK_ID, None, "term", "八年级下册", 1),
            ("chapter-one", DEFAULT_TEXTBOOK_ID, "term", "chapter", "第一章 三角形", 1),
            ("triangle-topic", DEFAULT_TEXTBOOK_ID, "chapter-one", "topic", "三角形内角和定理", 1),
            ("chapter-two", DEFAULT_TEXTBOOK_ID, "term", "chapter", "第二章 不等式与不等式组", 2),
            ("inequality-topic", DEFAULT_TEXTBOOK_ID, "chapter-two", "topic", "一元一次不等式", 1),
        ],
    )
    conn.executemany(
        "INSERT INTO questions VALUES (?, 'golden-source-001', ?, ?)",
        [
            ("q-in", "1", "根据三角形内角和定理计算角度"),
            ("q-out", "2", "求一元一次不等式的解集"),
            ("q-unknown", "3", "这是一道没有课程锚点的题目"),
        ],
    )
    conn.commit()
    conn.close()


class GoldenSampleScopeEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "fixture.db"
        make_fixture(self.db_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_evaluates_scope_and_preserves_database_hash(self) -> None:
        before = sha256_file(self.db_path)
        report = evaluate_golden_sample_scope(
            db_path=self.db_path, current_chapter="chapter-two", limit=20
        )
        self.assertEqual(before, sha256_file(self.db_path))
        self.assertTrue(report["input"]["database_hash_unchanged"])
        self.assertEqual(report["input"]["database_sha256_before"], before)
        by_id = {item["question_id"]: item for item in report["questions"]}
        self.assertEqual(by_id["q-in"]["scope_status"], "out_of_scope")
        self.assertEqual(by_id["q-out"]["scope_status"], "in_scope")
        self.assertEqual(by_id["q-unknown"]["scope_status"], "unknown")
        self.assertFalse(by_id["q-unknown"]["determinable"])

    def test_report_is_machine_readable_and_only_artifact_written(self) -> None:
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        report = evaluate_golden_sample_scope(db_path=self.db_path, limit=2)
        output = Path(self.temp.name) / "audit" / "report.json"
        write_report(report, output)
        self.assertTrue(output.is_file())
        self.assertEqual(before, hashlib.sha256(self.db_path.read_bytes()).hexdigest())
        self.assertIn('"questions"', output.read_text(encoding="utf-8"))


class RealDevelopmentDatabaseReadOnlyTests(unittest.TestCase):
    """Keep one bounded regression check against the actual development DB."""

    def test_default_golden_sample_evaluation_keeps_dev_database_unchanged(self) -> None:
        db_path = Path(__file__).parent / "data" / "dev" / "teaching_docs_dev.db"
        self.assertTrue(db_path.is_file(), "real development database is required for this regression")
        before = sha256_file(db_path)
        report = evaluate_golden_sample_scope(db_path=db_path)
        self.assertEqual(before, sha256_file(db_path))
        self.assertTrue(report["input"]["database_hash_unchanged"])
        self.assertEqual(report["input"]["loaded_question_count"], 20)
        self.assertEqual(len(report["questions"]), 20)
        self.assertTrue(all(item["scope_status"] in {"in_scope", "out_of_scope", "unknown"}
                            for item in report["questions"]))


if __name__ == "__main__":
    unittest.main()
