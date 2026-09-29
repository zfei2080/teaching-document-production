"""Tests for low-risk candidate consistency checks."""

import unittest

from candidate_consistency import all_pass, check_candidate_consistency


class CandidateConsistencyTests(unittest.TestCase):
    def test_choice_answer_must_reference_existing_option(self):
        findings = check_candidate_consistency(
            question_type="选择题",
            options_json='["A. 1", "B. 2", "C. 3", "D. 4"]',
            answer="B",
            asset_paths=[],
        )
        self.assertTrue(all_pass(findings))

    def test_wrong_choice_answer_is_blocked(self):
        findings = check_candidate_consistency(
            question_type="选择题",
            options_json='["A. 1", "B. 2"]',
            answer="D",
            asset_paths=[],
        )
        self.assertFalse(all_pass(findings))
        self.assertTrue(any(item.code == "choice_answer_matches_option" and not item.passed for item in findings))

    def test_fill_in_question_accepts_empty_options(self):
        findings = check_candidate_consistency(
            question_type="填空题",
            options_json="[]",
            answer="5",
            asset_paths=[],
        )
        self.assertTrue(all_pass(findings))

    def test_unknown_type_is_blocked(self):
        findings = check_candidate_consistency(
            question_type="待分类",
            options_json="[]",
            answer="5",
            asset_paths=[],
        )
        self.assertFalse(all_pass(findings))


if __name__ == "__main__":
    unittest.main()
