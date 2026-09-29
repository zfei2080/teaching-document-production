"""Read-only P1-4 student-lecture readiness audit regression coverage."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from p1_4_pilot_readiness_audit import (
    _pedagogical_role_supply,
    build_p1_4_readiness_audit,
    write_p1_4_readiness_audit,
)


ROOT = Path(__file__).parent
LIVE_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"


class P14PilotReadinessAuditTests(unittest.TestCase):
    def test_shallow_role_table_is_not_accepted_as_a_role_evidence_contract(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                "CREATE TABLE question_pedagogical_role_evidence (question_id TEXT, role TEXT, status TEXT)"
            )
            result = _pedagogical_role_supply(connection, {"question_ids": ["golden-q013"]})
            self.assertFalse(result["contract_available"])
            self.assertEqual(result["reason"], "question_pedagogical_role_evidence_columns_invalid")
        finally:
            connection.close()

    def test_read_only_report_exposes_separate_content_mapping_admission_and_supply_states(self) -> None:
        before = hashlib.sha256(LIVE_DATABASE.read_bytes()).hexdigest()
        report = build_p1_4_readiness_audit(LIVE_DATABASE)
        after = hashlib.sha256(LIVE_DATABASE.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertTrue(report["database"]["unchanged"])
        self.assertIn(report["decision"], {
            "blocked_p1_4_readiness_gap_report",
            "ready_for_student_document_generation",
        })
        self.assertIn("controlled_content", report)
        self.assertIn("q013", report)
        self.assertIn("virtual_pilot_progress", report)
        self.assertIn("approved_question_supply", report)
        self.assertIn("section_readiness", report)
        self.assertIn("question_pedagogical_role_supply", report)
        self.assertIn("auxiliary_controlled_content_supply", report)
        self.assertFalse(report["question_pedagogical_role_supply"]["contract_available"])
        self.assertEqual(report["section_readiness"]["G4_public_core"], "blocked")
        self.assertIn(
            "G4_public_core:question_pedagogical_role_evidence_contract_missing",
            report["blockers"],
        )
        with tempfile.TemporaryDirectory() as directory:
            output = write_p1_4_readiness_audit(report, Path(directory) / "readiness.json")
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
