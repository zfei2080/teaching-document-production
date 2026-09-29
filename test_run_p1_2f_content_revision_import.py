"""P1-2f production-runner coverage on a disposable development DB copy."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from run_p1_2f_content_revision_import import import_revalidated_content_revision


ROOT = Path(__file__).parent
P1_2F_BASELINE_DATABASE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)
P1_2B_AUDIT = ROOT / "output" / "audits" / "p1-2b_controlled_content_validation.json"
TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
NODE_ID = "bsd-math-8x-2026-node-01-section-02"


class RunP12FContentRevisionImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "development-copy.db"
        shutil.copy2(P1_2F_BASELINE_DATABASE, self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _head_revisions(self) -> dict[str, int]:
        connection = sqlite3.connect(self.database)
        try:
            return {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    """SELECT content_type, head_revision
                         FROM controlled_content_source_revision_heads
                        WHERE textbook_id=? AND curriculum_node_id=?""",
                    (TEXTBOOK_ID, NODE_ID),
                )
            }
        finally:
            connection.close()

    def test_imports_or_reuses_current_word_verified_revision_without_business_writes(self) -> None:
        before_heads = self._head_revisions()
        report = import_revalidated_content_revision(
            self.database,
            audit_path=P1_2B_AUDIT,
            workspace=ROOT,
            report_path=self.root / "audit.json",
            backup_dir=self.root / "backups",
        )
        self.assertTrue((self.root / "audit.json").is_file())
        self.assertTrue(Path(report["backup"]["path"]).is_file())
        self.assertEqual(report["backup"]["quick_check"], "ok")
        self.assertTrue(report["protected_business_tables_unchanged"])
        self.assertEqual(report["q013_status"], {
            "quality_status": "blocked",
            "review_status": "pending",
            "fit_status": "pending",
        })
        self.assertEqual(report["delivery_counts"], {
            "teaching_documents": 0,
            "document_questions": 0,
            "quality_reports": 0,
            "question_usage": 0,
        })
        self.assertEqual({row["content_type"] for row in report["current_content_rows"]}, {
            "knowledge_explanation",
            "consolidation_practice",
        })
        after_heads = self._head_revisions()
        if report["outcome"]["status"] == "validated":
            self.assertEqual(
                after_heads,
                {content_type: revision + 1 for content_type, revision in before_heads.items()},
            )
        else:
            self.assertEqual(report["outcome"]["status"], "reused")
            self.assertEqual(after_heads, before_heads)


if __name__ == "__main__":
    unittest.main()
