"""Tests for source-derived mathematical validation without question-number dispatch."""
from __future__ import annotations

import unittest

from content_question_validation_contract import assess_candidate
from source_content_question_segmentation import FieldEvidence, QuestionCandidate


APPROVED = {"kp-triangle-side-inequality-v1", "kp-bsd8x-isosceles-triangle-properties-v1"}


def candidate(*, number: str, stem: str, options: tuple[dict[str, object], ...], answer: str) -> QuestionCandidate:
    evidence = FieldEvidence("source", 0, len(answer), "【答案】" + answer)
    return QuestionCandidate(number, "选择题", stem, options, tuple(), tuple(), (evidence,), tuple(), ("source",))


class ContentQuestionValidationContractTests(unittest.TestCase):
    def test_triangle_inequality_uses_textual_form_not_source_number(self) -> None:
        item = candidate(
            number="999", stem="以下各组长度的线段为边能组成一个三角形的是", answer="D",
            options=(
                {"label": "A", "text": "3，5，8"}, {"label": "B", "text": "8，8，18"},
                {"label": "C", "text": "3，4，8"}, {"label": "D", "text": "2，3，4"},
            ),
        )
        result = assess_candidate(item, approved_knowledge_point_ids=APPROVED)
        self.assertEqual(result.mathematical_validation.status, "pass")
        self.assertEqual(result.mathematical_validation.computed_answer, "D")
        self.assertTrue(result.content_eligible)
        self.assertEqual(result.difficulty, "基础")
        self.assertEqual([(x.knowledge_point_id, x.relation_type) for x in result.knowledge_mappings], [("kp-triangle-side-inequality-v1", "primary")])

    def test_answer_mismatch_blocks_the_candidate(self) -> None:
        item = candidate(
            number="2", stem="以下各组长度的线段为边能组成一个三角形的是", answer="A",
            options=(
                {"label": "A", "text": "3，5，8"}, {"label": "B", "text": "8，8，18"},
                {"label": "C", "text": "3，4，8"}, {"label": "D", "text": "2，3，4"},
            ),
        )
        result = assess_candidate(item, approved_knowledge_point_ids=APPROVED)
        self.assertEqual(result.mathematical_validation.status, "fail")
        self.assertFalse(result.content_eligible)

    def test_isosceles_case_analysis_requires_unique_perimeter(self) -> None:
        item = candidate(
            number="14", stem="等腰三角形的一边长为4cm，另一边长为8cm，则这个等腰三角形的周长为", answer="20cm", options=tuple(),
        )
        result = assess_candidate(item, approved_knowledge_point_ids=APPROVED)
        self.assertEqual(result.mathematical_validation.status, "pass")
        self.assertEqual(result.difficulty, "中等")
        self.assertTrue(result.content_eligible)
        self.assertEqual([x.relation_type for x in result.knowledge_mappings], ["primary", "secondary"])

    def test_unknown_form_remains_uneligible_without_guessing(self) -> None:
        item = candidate(number="1", stem="如图求未知角", answer="A", options=tuple())
        result = assess_candidate(item, approved_knowledge_point_ids=APPROVED)
        self.assertEqual(result.mathematical_validation.status, "unsupported")
        self.assertFalse(result.content_eligible)
        self.assertEqual(result.knowledge_mappings, tuple())
        self.assertIsNone(result.difficulty)


if __name__ == "__main__":
    unittest.main()
