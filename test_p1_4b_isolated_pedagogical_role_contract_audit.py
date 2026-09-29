"""P1-4b isolated pedagogical-role contract audit coverage."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from p1_4b_isolated_pedagogical_role_contract_audit import (
    LIVE_DATABASE,
    run_isolated_pedagogical_role_contract_audit,
)


class P14BIsolatedPedagogicalRoleContractAuditTests(unittest.TestCase):
    def test_records_zero_role_supply_without_changing_live_database(self) -> None:
        before = hashlib.sha256(LIVE_DATABASE.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "role-contract.json"
            report = run_isolated_pedagogical_role_contract_audit(report_path=output)
            text = output.read_text(encoding="utf-8")
            self.assertEqual(json.loads(text), report)
            self.assertNotIn("[PUBLIC_CORE_EXPLICIT_ROLE_MARKER]", text)
        after = hashlib.sha256(LIVE_DATABASE.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertTrue(report["live_database"]["unchanged"])
        self.assertFalse(report["live_database"]["v2_32_applied"])
        self.assertTrue(report["isolated_database"]["v2_32_applied"])
        self.assertTrue(report["isolated_database"]["protected_counts_unchanged"])
        self.assertTrue(report["role_contract_available"])
        self.assertEqual(report["role_evidence_count"], 0)
        self.assertEqual(report["current_role_evidence_count"], 0)
        self.assertEqual(report["unallocated_eligible_question_ids"], ["golden-q013"])
        self.assertTrue(all(not values for values in report["role_assignments"].values()))
        self.assertFalse(report["question_import_authorized"])
        self.assertFalse(report["student_document_generation_authorized"])


if __name__ == "__main__":
    unittest.main()
