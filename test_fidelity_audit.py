"""Tests for deterministic source-fidelity checks."""

import unittest

from docx_forensics import ParagraphFragment
from fidelity_audit import all_pass, check_question_fields


class FidelityAuditTests(unittest.TestCase):
    def setUp(self):
        self.fragments = {
            "stem": ParagraphFragment(1, "1. 求 x+1 的值", "stem-hash"),
            "options": ParagraphFragment(2, "A. 1\tB. 2\tC. 3\tD. 4", "options-hash"),
            "answer": ParagraphFragment(3, "1.【答案】B", "answer-hash"),
            "analysis-a": ParagraphFragment(4, "【解析】由 x+1=2，", "analysis-a-hash"),
            "analysis-b": ParagraphFragment(5, "故选 B。", "analysis-b-hash"),
        }
        self.provenance = {
            "stem": ["stem"],
            "options": ["options"],
            "answer": ["answer"],
            "analysis": ["analysis-a", "analysis-b"],
        }

    def checks(self, *, answer="B", analysis="【解析】由 x+1=2，故选 B。", question_type="选择题", provenance=None):
        return check_question_fields(
            stem="求 x+1 的值",
            options_json='["A. 1", "B. 2", "C. 3", "D. 4"]',
            answer=answer,
            analysis=analysis,
            question_type=question_type,
            fragments=self.fragments,
            provenance=provenance or self.provenance,
        )

    def test_all_fields_pass_when_values_are_in_source_fragments(self):
        self.assertTrue(all_pass(self.checks()))

    def test_answer_change_is_blocked(self):
        checks = self.checks(answer="C")
        answer_check = next(check for check in checks if check.field_name == "answer")
        self.assertFalse(answer_check.passed)
        self.assertFalse(all_pass(checks))

    def test_missing_provenance_is_blocked(self):
        provenance = {key: value for key, value in self.provenance.items() if key != "analysis"}
        checks = self.checks(provenance=provenance)
        analysis_check = next(check for check in checks if check.field_name == "analysis")
        self.assertFalse(analysis_check.passed)
        self.assertFalse(all_pass(checks))

    def test_non_choice_question_allows_empty_options(self):
        checks = check_question_fields(
            stem="填空题",
            options_json="[]",
            answer="B",
            analysis="【解析】由 x+1=2，故选 B。",
            question_type="填空题",
            fragments={**self.fragments, "stem": ParagraphFragment(1, "填空题", "stem-hash")},
            provenance={key: value for key, value in self.provenance.items() if key != "options"},
        )
        self.assertTrue(all_pass(checks))


if __name__ == "__main__":
    unittest.main()
