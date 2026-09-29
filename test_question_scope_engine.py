"""Tests for the pure, conservative automatic question-scope engine."""
from __future__ import annotations

import copy
import unittest

from curriculum_progress_scope import CurriculumProgressScope
from question_scope_engine import ScopeCatalogNode, determine_question_scope

BOOK = "math-8-lower"
VERSION = "2026.1"


def node(node_id: str, name: str, textbook_id: str = BOOK) -> ScopeCatalogNode:
    return ScopeCatalogNode(node_id, textbook_id, VERSION, name)


class QuestionScopeEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = (
            node("chapter-2", "全等三角形"),
            node("chapter-3", "勾股定理"),
        )
        self.question = {"stem": "已知两个全等三角形，求对应边的长度。"}

    def test_unique_exact_evidence_in_allowed_scope_is_candidate(self) -> None:
        original = copy.deepcopy(self.question)
        result = determine_question_scope(
            question=self.question, textbook_id=BOOK, nodes=self.nodes,
            allowed_node_ids=("chapter-1", "chapter-2"),
        )
        self.assertEqual(result.status, "candidate")
        self.assertEqual(result.candidate_nodes[0].node_id, "chapter-2")
        self.assertGreaterEqual(result.confidence, 0.75)
        self.assertEqual(result.reasons[0]["code"], "unique_evidence_chain")
        self.assertEqual(self.question, original)
        self.assertFalse(result.approval_eligible)

    def test_is_deterministic_and_exposes_input_catalog_and_decision_hashes(self) -> None:
        first = determine_question_scope(
            question=self.question, textbook_id=BOOK, nodes=reversed(self.nodes),
            allowed_node_ids=("chapter-2",),
        )
        second = determine_question_scope(
            question=self.question, textbook_id=BOOK, nodes=self.nodes,
            allowed_node_ids=("chapter-2",),
        )
        self.assertEqual(first, second)
        for digest in (first.input_hash, first.catalog_hash, first.decision_hash):
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_insufficient_features_and_bad_input_are_unsupported(self) -> None:
        for question in ({"stem": "阅读短文后回答问题。"}, {}, None):
            with self.subTest(question=question):
                result = determine_question_scope(
                    question=question, textbook_id=BOOK, nodes=self.nodes,
                    allowed_node_ids=("chapter-2",),
                )
                self.assertEqual(result.status, "unsupported")
                self.assertEqual(result.candidate_nodes, ())

    def test_tied_candidates_are_rejected(self) -> None:
        result = determine_question_scope(
            question={"stem": "证明两个全等三角形的对应角相等。"}, textbook_id=BOOK,
            nodes=(node("a", "全等三角形判定"), node("b", "全等三角形性质")),
            allowed_node_ids=("a", "b"),
        )
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reasons[0]["code"], "ambiguous_top_candidate")

    def test_cross_textbook_or_outside_progress_is_rejected(self) -> None:
        cross_book = determine_question_scope(
            question=self.question, textbook_id=BOOK,
            nodes=(node("chapter-2", "全等三角形", "other-book"),),
            allowed_node_ids=("chapter-2",),
        )
        self.assertEqual(cross_book.status, "rejected")
        self.assertEqual(cross_book.reasons[0]["code"], "cross_textbook_catalog_node")

        outside = determine_question_scope(
            question={"stem": "勾股定理中，求斜边长度。"}, textbook_id=BOOK,
            nodes=self.nodes, allowed_node_ids=("chapter-2",),
        )
        self.assertEqual(outside.status, "rejected")
        self.assertEqual(outside.reasons[0]["code"], "candidate_node_outside_allowed_progress")

    def test_unresolved_progress_scope_never_means_unrestricted(self) -> None:
        progress = CurriculumProgressScope("blocked", BOOK, VERSION, None, (), (), ("progress_node_not_found",))
        result = determine_question_scope(
            question=self.question, textbook_id=BOOK, nodes=self.nodes,
            allowed_node_ids=("chapter-2",), progress_scope=progress,
        )
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reasons[0]["code"], "unresolved_progress_scope")

    def test_compound_question_with_future_anchor_is_rejected(self) -> None:
        result = determine_question_scope(
            question={"stem": "全等三角形与勾股定理综合题。"}, textbook_id=BOOK,
            nodes=self.nodes, allowed_node_ids=("chapter-2",),
        )
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reasons[0]["code"], "candidate_node_outside_allowed_progress")


if __name__ == "__main__":
    unittest.main()
