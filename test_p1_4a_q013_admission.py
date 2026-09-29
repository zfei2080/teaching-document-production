"""P1-4a q013-only admission runner coverage on disposable DB copies."""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from p1_4a_q013_admission import run_q013_admission


ROOT = Path(__file__).parent
LIVE_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
QUESTION_ID = "golden-q013"


class P14AQ013AdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "development-copy.db"
        shutil.copy2(LIVE_DATABASE, self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _run(self) -> dict:
        return run_q013_admission(
            self.database,
            report_path=self.root / "audit.json",
            backup_dir=self.root / "backups",
        )

    def test_rehearses_then_approves_only_q013_without_delivery_writes(self) -> None:
        # The real development database may already have completed P1-4a.
        # Exercise the full rehearsal transition on this disposable copy.
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id=?",
                (QUESTION_ID,),
            )
            connection.commit()
        finally:
            connection.close()
        report = self._run()
        self.assertEqual(report["action"], "approved_q013_after_rehearsed_automatic_verification")
        self.assertTrue(report["database_changes_committed"])
        self.assertTrue(Path(report["backup"]["path"]).is_file())
        self.assertEqual(report["backup"]["integrity_check"], "ok")
        self.assertEqual(report["final"]["verification_statuses"], {
            "asset_semantics": "pass",
            "mathematical_independent": "pass",
            "source_fidelity": "pass",
            "structural_consistency": "pass",
            "textbook_scope": "pass",
        })
        self.assertEqual(report["delivery_tables"], {
            "teaching_documents": 0,
            "document_questions": 0,
            "quality_reports": 0,
            "question_usage": 0,
        })
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT quality_status, review_status FROM questions WHERE id=?", (QUESTION_ID,)
                ).fetchone(),
                ("approved", "approved"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM questions WHERE id<>? AND quality_status='approved'", (QUESTION_ID,)
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version='v2.30-p14-current-p13c-question-admission-path'"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

        before_replay = self._hash(self.database)
        replay = self._run()
        self.assertEqual(replay["action"], "reused_existing_q013_admission")
        self.assertFalse(replay["database_changes_committed"])
        self.assertEqual(before_replay, self._hash(self.database))

    def test_preflight_failure_leaves_the_copy_byte_for_byte_unchanged(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                """UPDATE question_textbooks SET fit_status='pending'
                     WHERE question_id=? AND textbook_id='bsd-math-grade8-lower-2026-spring-extsrc'
                       AND curriculum_node_id='bsd-math-8x-2026-node-01-section-02'""",
                (QUESTION_ID,),
            )
            connection.commit()
        finally:
            connection.close()
        before = self._hash(self.database)
        report = self._run()
        self.assertEqual(report["action"], "blocked_q013_admission")
        self.assertFalse(report["database_changes_committed"])
        self.assertTrue(report["database"]["unchanged_after_block"])
        self.assertEqual(before, self._hash(self.database))


if __name__ == "__main__":
    unittest.main()
