import unittest

from exam_structure_extractor import extract_exam_structure


class ExtractExamStructureTests(unittest.TestCase):
    def test_extracts_sections_questions_options_and_subjective_questions(self) -> None:
        text = """
        2026学年期末测试卷
        一、选择题
        1. 下列说法正确的是
        A. 甲
        B. 乙
        C. 丙
        D. 丁
        二、简答题
        2. 请简答牛顿第一定律的内容。
        本题需要结合生活现象说明。
        """

        result = extract_exam_structure(text)

        self.assertEqual(result.approved_questions, [])
        self.assertEqual(result.approved_answers, [])
        self.assertEqual(
            [segment.kind for segment in result.segments if segment.kind == "section"],
            ["section", "section"],
        )

        first_question = result.questions[0]
        self.assertEqual(first_question.number, "1")
        self.assertEqual(first_question.status, "candidate")
        self.assertEqual([option["label"] for option in first_question.options], ["A", "B", "C", "D"])
        self.assertTrue(first_question.answer_missing)
        self.assertEqual(first_question.evidence[0]["text"], "1. 下列说法正确的是")

        second_question = result.questions[1]
        self.assertEqual(second_question.number, "2")
        self.assertTrue(second_question.subjective)
        self.assertTrue(second_question.prompt.endswith("本题需要结合生活现象说明。"))
        self.assertEqual([item["line_index"] for item in second_question.evidence], [9, 10])

    def test_isolates_subjective_body_from_following_section(self) -> None:
        text = """
        三、解答题
        3. 计算 2+3 的结果，并写出过程。
        写出关键步骤。
        四、附加题
        4. 说明你的思路。
        """

        result = extract_exam_structure(text)

        third = result.questions[0]
        self.assertTrue(third.subjective)
        self.assertEqual(third.prompt, "计算 2+3 的结果，并写出过程。 写出关键步骤。")
        self.assertEqual(result.questions[1].number, "4")
        self.assertEqual(result.questions[1].evidence[0]["line_index"], 5)

    def test_quarantines_duplicate_or_out_of_order_numbers(self) -> None:
        text = """
        一、选择题
        2. 第二题先出现
        A. 甲
        1. 第一题后出现
        A. 乙
        2. 重复的第二题
        B. 丙
        """

        result = extract_exam_structure(text)

        self.assertEqual([question.number for question in result.questions], ["2", "1", "2"])
        self.assertEqual(result.questions[0].status, "candidate")
        self.assertEqual(result.questions[1].status, "candidate")
        self.assertEqual(result.questions[2].status, "unsupported/quarantined")
        self.assertEqual(result.questions[2].quarantine_reason, "duplicate-question-number")
        self.assertTrue(any(item["reason"] == "duplicate-question-number" for item in result.quarantined))

    def test_blocks_unbound_images_or_formulae(self) -> None:
        text = """
        一、选择题
        5. 如图，判断电路中的电流方向。
        A. 从左到右
        B. 从右到左
        6. 直接可答的文字题
        A. 甲
        B. 乙
        """

        result = extract_exam_structure(text)

        self.assertEqual(result.questions[0].number, "6")
        self.assertTrue(all(question.number != "5" for question in result.questions))
        self.assertEqual(result.quarantined[0]["reason"], "image-or-formula-unbound")
        self.assertEqual(result.segments[1].status, "unsupported/quarantined")

    def test_marks_answer_lines_missing_and_never_approves_answers(self) -> None:
        text = """
        一、选择题
        7. 下列选项中正确的是
        A. 甲
        答案：A
        8. 再下一题
        A. 乙
        """

        result = extract_exam_structure(text)

        self.assertEqual([question.answer_missing for question in result.questions], [True, True])
        self.assertTrue(any(segment.kind == "answer_reference" for segment in result.segments))
        self.assertTrue(any(item["reason"] == "answers-not-approved" for item in result.quarantined))
        self.assertEqual(result.approved_answers, [])


if __name__ == "__main__":
    unittest.main()
