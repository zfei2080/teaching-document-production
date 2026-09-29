"""Focused tests for the FIDELITY-001 read-only sample-audit helpers."""

import unittest

from fidelity_sample_audit import FidelitySampleAuditError, choose_sample, resolve_under_root
from pathlib import Path


class FidelitySampleAuditTests(unittest.TestCase):
    def _row(self, index: int) -> dict[str, object]:
        types = ("选择题", "填空题", "解答题", "计算题")
        return {
            "id": f"q{index:03d}", "question_type": types[index % len(types)],
            "source_asset_count": 1 if index < 6 else 0,
            "formula_asset_count": 1 if 5 <= index < 10 else 0,
            "math_signal_count": 1 if 5 <= index < 10 else 0,
            "answer_evidence_count": 0 if index >= 15 else 1,
        }

    def test_sample_selection_is_stable_and_unique(self):
        rows = [self._row(index) for index in range(30)]
        first = choose_sample(rows, seed=20260804)
        second = choose_sample(rows, seed=20260804)
        self.assertEqual([row["id"] for row in first], [row["id"] for row in second])
        self.assertEqual(len(first), 20)
        self.assertEqual(len({row["id"] for row in first}), 20)

    def test_sample_pool_must_be_large_enough(self):
        with self.assertRaisesRegex(FidelitySampleAuditError, "sample_pool_too_small"):
            choose_sample([self._row(index) for index in range(19)])

    def test_source_path_cannot_escape_root(self):
        root = Path("E:/audit-root")
        with self.assertRaisesRegex(FidelitySampleAuditError, "source_path_escapes_configured_root"):
            resolve_under_root(root, "../outside.doc")


if __name__ == "__main__":
    unittest.main()
