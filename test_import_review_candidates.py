"""Tests for conservative option extraction in review-candidate imports."""

import unittest

from import_review_candidates import split_options


class ReviewCandidateImportTests(unittest.TestCase):
    def test_splits_tab_separated_options(self):
        stem, options = split_options("题干( )\nA. 甲\tB. 乙\tC. 丙\tD. 丁")
        self.assertEqual(stem, "题干( )")
        self.assertEqual(options, ["A. 甲", "B. 乙", "C. 丙", "D. 丁"])

    def test_splits_line_separated_options(self):
        stem, options = split_options("题干\nA. 甲\nB. 乙\nC. 丙\nD. 丁")
        self.assertEqual(stem, "题干")
        self.assertEqual(options, ["A. 甲", "B. 乙", "C. 丙", "D. 丁"])

    def test_does_not_guess_options_without_a_complete_choice_sequence(self):
        source = "这是主观题，A. 只是正文中的字母。"
        stem, options = split_options(source)
        self.assertEqual(stem, source)
        self.assertEqual(options, [])


if __name__ == "__main__":
    unittest.main()
