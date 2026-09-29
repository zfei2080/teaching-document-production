"""Tests for deterministic, source-anchored question segmentation."""
from __future__ import annotations

import unittest

from source_content_question_segmentation import segment_questions


class SourceContentQuestionSegmentationTests(unittest.TestCase):
    def blocks(self):
        return [
            {"id": "b0", "ordinal": 0, "raw_text": "\u4e00.\u9009\u62e9\u9898"},
            {"id": "b1", "ordinal": 1, "raw_text": "1. \u539f\u9898\u5e72\u9898"},
            {"id": "b2", "ordinal": 2, "raw_text": "A. \u9009\u9879\u4e00  B. \u9009\u9879\u4e8c"},
            {"id": "b3", "ordinal": 3, "raw_text": "2. \u7b2c\u4e8c\u9898"},
            {"id": "b4", "ordinal": 4, "raw_text": "\u3010\u7b54\u6848\u4e0e\u89e3\u6790\u3011"},
            {"id": "b5", "ordinal": 5, "raw_text": "1. \u3010\u7b54\u6848\u3011B;\u3010\u89e3\u6790\u3011\u539f\u89e3\u6790"},
            {"id": "b6", "ordinal": 6, "raw_text": "2. \u3010\u89e3\u6790\u3011\u53ea\u6709\u89e3\u6790"},
            {"id": "b7", "ordinal": 7, "raw_text": "\x01"},
        ]

    def test_segments_student_content_options_and_internal_evidence_without_generating_text(self) -> None:
        candidates = segment_questions(self.blocks())
        self.assertEqual([candidate.source_question_no for candidate in candidates], ["1", "2"])
        first, second = candidates
        self.assertEqual(first.question_type, "\u9009\u62e9\u9898")
        self.assertEqual(first.stem, "1. \u539f\u9898\u5e72\u9898")
        self.assertEqual(first.options, ({"label": "A", "text": "\u9009\u9879\u4e00"}, {"label": "B", "text": "\u9009\u9879\u4e8c"}))
        self.assertEqual(first.answer_evidence[0].text, "\u3010\u7b54\u6848\u3011B;")
        self.assertEqual(first.analysis_evidence[0].text, "\u3010\u89e3\u6790\u3011\u539f\u89e3\u6790")
        self.assertEqual(second.answer_evidence, ())
        self.assertEqual(second.analysis_evidence[0].text, "\u3010\u89e3\u6790\u3011\u53ea\u6709\u89e3\u6790")
        self.assertNotIn("\x01", second.stem)



    def test_accepts_calculation_sections_and_answer_entries_without_heading(self) -> None:
        blocks = [
            {"id": "h1", "ordinal": 0, "raw_text": "\u4e00.\u9009\u62e9\u9898"},
            {"id": "q1", "ordinal": 1, "raw_text": "1. \u4e09\u89d2\u5f62\u4e09\u8fb9\u5173\u7cfb"},
            {"id": "o1", "ordinal": 2, "raw_text": "A. \u7532  B. \u4e59"},
            {"id": "h2", "ordinal": 3, "raw_text": "\u4e8c.\u8ba1\u7b97\u9898"},
            {"id": "q2", "ordinal": 4, "raw_text": "2. \u8ba1\u7b97\u9898\u539f\u9898"},
            {"id": "a1", "ordinal": 5, "raw_text": "1.\u3010\u7b54\u6848\u3011A\u3010\u89e3\u6790\u3011\u6e90\u89e3\u6790"},
            {"id": "a2", "ordinal": 6, "raw_text": "2.\u3010\u7b54\u6848\u3011x=1"},
        ]
        candidates = segment_questions(blocks)
        self.assertEqual([candidate.source_question_no for candidate in candidates], ["1", "2"])
        self.assertEqual(candidates[1].question_type, "\u8ba1\u7b97\u9898")
        self.assertEqual(candidates[0].answer_evidence[0].text, "\u3010\u7b54\u6848\u3011A")
        self.assertEqual(candidates[1].answer_evidence[0].text, "\u3010\u7b54\u6848\u3011x=1")

    def test_preserves_image_only_options_as_asset_only_without_inventing_text(self) -> None:
        blocks = [
            {"id": "h", "ordinal": 0, "raw_text": "\u4e00.\u9009\u62e9\u9898"},
            {"id": "q", "ordinal": 1, "raw_text": "1. \u770b\u56fe\u9009\u62e9"},
            {"id": "o", "ordinal": 2, "raw_text": "A\uff0e \x01 B\uff0e \x01 C\uff0e \x01 D\uff0e \x01"},
        ]
        candidate = segment_questions(blocks)[0]
        self.assertEqual(
            candidate.options,
            (
                {"label": "A", "text": None, "asset_only": True},
                {"label": "B", "text": None, "asset_only": True},
                {"label": "C", "text": None, "asset_only": True},
                {"label": "D", "text": None, "asset_only": True},
            ),
        )
        self.assertEqual([evidence.text for evidence in candidate.option_evidence], ["A\uff0e \x01", "B\uff0e \x01", "C\uff0e \x01", "D\uff0e \x01"])


    def test_rejects_candidate_with_repeated_option_labels_instead_of_merging_questions(self) -> None:
        blocks = [
            {"id": "h", "ordinal": 0, "raw_text": "\u4e00.\u9009\u62e9\u9898"},
            {"id": "q", "ordinal": 1, "raw_text": "1. \u9898\u5e72 A. \u7532 B. \u4e59 A. \u4e19 B. \u4e01"},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate_option_label"):
            segment_questions(blocks)


    def test_geometry_labels_do_not_be_misclassified_as_multiple_choice_options(self) -> None:
        blocks = [
            {"id": "h", "ordinal": 0, "raw_text": "\u4e00.\u586b\u7a7a\u9898"},
            {"id": "q", "ordinal": 1, "raw_text": "1. \u2460\u2220BAD=\u2220ACD\uff1b\u2461AB=AC\uff0e"},
        ]
        candidate = segment_questions(blocks)[0]
        self.assertEqual(candidate.options, ())

    def test_rejects_source_blocks_without_required_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "source_block_shape_invalid"):
            segment_questions([{"id": "missing-ordinal", "raw_text": "1. x"}])


if __name__ == "__main__":
    unittest.main()
