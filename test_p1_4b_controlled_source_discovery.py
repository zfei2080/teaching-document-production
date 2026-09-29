"""Regression coverage for P1-4b source-bound candidate discovery."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from p1_4b_controlled_source_discovery import (
    build_p1_4b_source_discovery,
    write_p1_4b_source_discovery,
)


ROOT = Path(__file__).parent
LIVE_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"


class P14BControlledSourceDiscoveryTests(unittest.TestCase):
    def test_reports_exact_anchors_and_blocks_half_import(self) -> None:
        before = hashlib.sha256(LIVE_DATABASE.read_bytes()).hexdigest()
        report = build_p1_4b_source_discovery(LIVE_DATABASE)
        after = hashlib.sha256(LIVE_DATABASE.read_bytes()).hexdigest()

        self.assertEqual(before, after)
        self.assertTrue(report["database"]["unchanged"])
        self.assertFalse(report["candidate_import_authorized"])
        self.assertEqual(report["candidate"]["math"]["status"], "pass")
        self.assertEqual(report["candidate"]["math"]["computed_answer"], "C")
        self.assertEqual(report["candidate"]["field_anchors"]["stem"]["paragraph_index"], 2)
        self.assertEqual(report["candidate"]["field_anchors"]["options"]["paragraph_index"], 3)
        self.assertEqual(report["candidate"]["field_anchors"]["answer"]["paragraph_index"], 41)
        self.assertEqual(report["candidate"]["field_anchors"]["analysis"]["paragraph_index"], 42)
        self.assertTrue(report["controlled_source"]["coverage"]["student_prompt_covered"])
        self.assertFalse(report["controlled_source"]["coverage"]["answer_covered"])
        self.assertIn(
            "answer_and_analysis_are_outside_current_student_safe_segment",
            report["blockers"],
        )
        with tempfile.TemporaryDirectory() as directory:
            output = write_p1_4b_source_discovery(report, Path(directory) / "discovery.json")
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
