"""P1-4b disposable-database derivation audit coverage."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from p1_4b_isolated_source_derivation_audit import LIVE_DATABASE, run_isolated_source_derivation


class P14BIsolatedSourceDerivationAuditTests(unittest.TestCase):
    def test_writes_internal_evidence_audit_without_changing_live_database(self) -> None:
        before = hashlib.sha256(LIVE_DATABASE.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "derivation.json"
            report = run_isolated_source_derivation(report_path=output)
            self.assertTrue(output.is_file())
            output_text = output.read_text(encoding="utf-8")
            self.assertEqual(json.loads(output_text), report)
            self.assertNotIn("\"answer\":", output_text)
            self.assertNotIn("注意分类讨论", output_text)
        after = hashlib.sha256(LIVE_DATABASE.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertTrue(report["live_database"]["unchanged"])
        self.assertFalse(report["live_database"]["v2_31_applied"])
        self.assertTrue(report["isolated_database"]["migration_applied"])
        self.assertTrue(report["isolated_database"]["protected_counts_unchanged"])
        self.assertTrue(report["student_answer_separation_proven"])
        self.assertEqual({row["field_name"] for row in report["internal_answer_evidence"]}, {"answer", "analysis"})
        self.assertFalse(report["question_import_authorized"])
        self.assertFalse(report["student_document_generation_authorized"])


if __name__ == "__main__":
    unittest.main()
