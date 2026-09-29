"""Tests for conservative source-linked DOCX candidate parsing."""

import unittest
from unittest.mock import patch

from docx_candidate_parser import parse_candidates
from docx_forensics import ParagraphFragment


class CandidateParserTests(unittest.TestCase):
    def test_pairs_questions_answers_knowledge_and_analysis(self):
        fragments = [
            ParagraphFragment(0, "一、选择题", "h0"),
            ParagraphFragment(1, "1. 第一题", "h1"),
            ParagraphFragment(2, "A. 甲\tB. 乙", "h2"),
            ParagraphFragment(3, "2. 第二题", "h3"),
            ParagraphFragment(4, "第II卷", "h4"),
            ParagraphFragment(5, "1.【答案】B", "h5"),
            ParagraphFragment(6, "【知识点】测试知识点", "h6"),
            ParagraphFragment(7, "【解析】故选B。", "h7"),
            ParagraphFragment(8, "2.【答案】C", "h8"),
            ParagraphFragment(9, "【解析】故选C。", "h9"),
        ]
        with patch("docx_candidate_parser.extract_paragraphs", return_value=fragments):
            candidates = parse_candidates("demo.docx")

        self.assertEqual([item.number for item in candidates], ["1", "2"])
        self.assertEqual(candidates[0].answer, "B")
        self.assertEqual(candidates[0].knowledge_text, "测试知识点")
        self.assertIn("故选B", candidates[0].analysis_text)
        self.assertEqual(candidates[1].answer, "C")
        self.assertIsNone(candidates[1].knowledge_text)

    def test_multiline_answer_prefix_starts_a_new_answer_block(self):
        fragments = [
            ParagraphFragment(0, "1. 第一题", "h0"),
            ParagraphFragment(1, "2. 第二题", "h1"),
            ParagraphFragment(2, "1.【答案】A\n补充说明", "h2"),
            ParagraphFragment(3, "【知识点】知识点一", "h3"),
            ParagraphFragment(4, "【解析】第一题解析", "h4"),
            ParagraphFragment(5, "2.【答案】B\n补充说明", "h5"),
            ParagraphFragment(6, "【知识点】知识点二", "h6"),
            ParagraphFragment(7, "【解析】第二题解析", "h7"),
        ]
        with patch("docx_candidate_parser.extract_paragraphs", return_value=fragments):
            candidates = parse_candidates("demo.docx")

        self.assertEqual(candidates[0].answer, "A\n补充说明")
        self.assertEqual(candidates[1].answer, "B\n补充说明")
        self.assertIn("第一题解析", candidates[0].analysis_text)
        self.assertIn("第二题解析", candidates[1].analysis_text)
        self.assertNotIn("第二题解析", candidates[0].analysis_text)

    def test_keeps_candidate_incomplete_when_answer_is_missing(self):
        fragments = [
            ParagraphFragment(0, "1. 第一题", "h0"),
            ParagraphFragment(1, "A. 甲\tB. 乙", "h1"),
        ]
        with patch("docx_candidate_parser.extract_paragraphs", return_value=fragments):
            candidates = parse_candidates("demo.docx")

        self.assertEqual(len(candidates), 1)
        self.assertIsNone(candidates[0].answer)
        self.assertIsNone(candidates[0].analysis_text)


if __name__ == "__main__":
    unittest.main()
