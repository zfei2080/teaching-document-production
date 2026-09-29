import unittest

from source_family_dedupe import classify_source_relationship


class SourceFamilyDedupeTestCase(unittest.TestCase):
    def test_exact_duplicate_when_hash_matches(self) -> None:
        left = {
            "content_hash": "ABC123HASH",
            "filename": "math_grade7_unit1_v1.pdf",
            "stem_text": "解方程 x + 3 = 10 的结果是？",
        }
        right = {
            "content_hash": "abc123hash",
            "filename": "math_grade7_unit1_final.pdf",
            "stem_text": "解方程 x + 3 = 10 的结果是？",
        }

        result = classify_source_relationship(left, right)

        self.assertEqual(result["decision"], "exact_duplicate")
        self.assertEqual(result["uncertainty"], "low")
        self.assertFalse(result["auto_merge"])
        self.assertFalse(result["auto_delete"])
        self.assertTrue(result["evidence"]["hash_match"])

    def test_similar_version_is_only_suspected_same_family(self) -> None:
        left = {
            "content_hash": "hash-left-001",
            "filename": "grade7_linear_equation_v1.docx",
            "stem_text": "已知一次方程 2x + 5 = 17，求 x 的值。",
        }
        right = {
            "content_hash": "hash-right-002",
            "filename": "grade7_linear_equation_final.docx",
            "stem_text": "已知一次方程 2x + 5 = 17，求出 x 的值。",
        }

        result = classify_source_relationship(left, right)

        self.assertEqual(result["decision"], "suspected_same_family")
        self.assertEqual(result["recommended_action"], "manual_review_only")
        self.assertEqual(result["uncertainty"], "medium")
        self.assertFalse(result["auto_merge"])
        self.assertFalse(result["auto_delete"])
        self.assertFalse(result["evidence"]["hash_match"])
        self.assertGreaterEqual(
            result["evidence"]["filename_similarity"]["score"],
            result["thresholds"]["filename_base_similarity_gte"],
        )
        self.assertGreaterEqual(
            result["evidence"]["stem_fingerprint_similarity"]["score"],
            result["thresholds"]["stem_fingerprint_similarity_gte"],
        )

    def test_distinct_sources_do_not_match(self) -> None:
        left = {
            "content_hash": "hash-geometry-100",
            "filename": "geometry_triangle_area.docx",
            "stem_text": "已知三角形底为 6，高为 4，求面积。",
        }
        right = {
            "content_hash": "hash-english-200",
            "filename": "english_reading_unit3.docx",
            "stem_text": "阅读短文后回答作者为什么喜欢春天。",
        }

        result = classify_source_relationship(left, right)

        self.assertEqual(result["decision"], "no_match")
        self.assertEqual(result["uncertainty"], "low")
        self.assertEqual(result["recommended_action"], "none")

    def test_missing_input_fails_closed(self) -> None:
        left = {
            "filename": "grade8_probability_v2.docx",
            "stem_text": "袋中有 3 个红球和 2 个白球，任取一个是红球的概率是多少？",
        }
        right = {
            "content_hash": "hash-probability-002",
            "filename": "grade8_probability_final.docx",
            "stem_text": "袋中有 3 个红球和 2 个白球，求任取一个红球的概率。",
        }

        result = classify_source_relationship(left, right)

        self.assertEqual(result["decision"], "no_match")
        self.assertEqual(result["uncertainty"], "low")
        self.assertEqual(result["recommended_action"], "none")
        self.assertIn("left.content_hash", result["evidence"]["missing_inputs"])
        self.assertFalse(result["auto_merge"])
        self.assertFalse(result["auto_delete"])


if __name__ == "__main__":
    unittest.main()
