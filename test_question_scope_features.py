import unittest
from question_scope_features import extract_question_scope_features

class QuestionScopeFeaturesTests(unittest.TestCase):
    def test_deterministic_without_mutation(self):
        data={"stem":"已知等腰三角形底边为6，求周长", "options":"A.1", "answer":"12"}
        a=extract_question_scope_features(**data); b=extract_question_scope_features(**data)
        self.assertEqual(a,b); self.assertEqual(data["stem"], "已知等腰三角形底边为6，求周长")
    def test_term_and_condition_offsets(self):
        r=extract_question_scope_features(stem="已知等腰三角形，求角度")
        self.assertTrue(any(x["term"]=="等腰三角形" for x in r.term_anchors)); self.assertTrue(any(x["term"]=="已知" for x in r.condition_anchors))
    def test_symbols(self):
        r=extract_question_scope_features(stem="x²+1=0")
        self.assertTrue(r.symbol_anchors); self.assertIn("symbolic_expression",r.candidate_features)
    def test_missing_stem_fails_closed(self):
        self.assertEqual(extract_question_scope_features(stem=None).status,"unsupported")
    def test_hash_changes(self):
        self.assertNotEqual(extract_question_scope_features(stem="x=1").input_hash,extract_question_scope_features(stem="x=2").input_hash)
    def test_plain_text_unknown(self):
        self.assertEqual(extract_question_scope_features(stem="今天天气不错").status,"unknown")
if __name__ == '__main__': unittest.main()
