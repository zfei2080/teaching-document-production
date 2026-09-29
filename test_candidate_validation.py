"""Tests for conservative candidate eligibility rules."""

import unittest

from candidate_validation import validate_candidate
from docx_candidate_parser import CandidateQuestion


class CandidateValidationTests(unittest.TestCase):
    def make_candidate(self, number="1", answer="A", knowledge="知识点", analysis="解析"):
        return CandidateQuestion(
            number=number,
            stem_fragment_ids=(1,),
            answer_fragment_id=2 if answer else None,
            knowledge_fragment_id=3 if knowledge else None,
            analysis_fragment_ids=(4,) if analysis else (),
            stem_text="题干",
            answer=answer,
            knowledge_text=knowledge,
            analysis_text=analysis,
        )

    def test_complete_objective_candidate_is_review_eligible(self):
        result = validate_candidate(self.make_candidate())
        self.assertTrue(result.eligible_for_review)
        self.assertEqual(result.reasons, ())

    def test_missing_answer_is_blocked(self):
        result = validate_candidate(self.make_candidate(answer=None))
        self.assertFalse(result.eligible_for_review)
        self.assertIn("missing answer", result.reasons)

    def test_later_subjective_batch_is_not_imported_yet(self):
        result = validate_candidate(self.make_candidate(number="21"))
        self.assertFalse(result.eligible_for_review)
        self.assertIn("outside first objective-question batch", result.reasons)


if __name__ == "__main__":
    unittest.main()
