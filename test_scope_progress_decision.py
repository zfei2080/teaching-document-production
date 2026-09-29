"""Regression tests for pure, non-authoritative scope/progress decisions."""

from __future__ import annotations

import unittest

from scope_progress_decision import (
    ActiveClassProgress,
    ApprovedScopeEvidence,
    CurriculumNode,
    decide_progress_scope,
    suggest_mapping,
)


HASH = "a" * 64
TEXTBOOK = "nbsd-math-8-lower"


class ScopeProgressDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = (
            CurriculumNode("chapter-2", TEXTBOOK, "2026.1", "全等三角形"),
            CurriculumNode("chapter-3", TEXTBOOK, "2026.1", "勾股定理"),
        )
        self.progress = ActiveClassProgress(
            class_id="class-8-1",
            textbook_id=TEXTBOOK,
            current_curriculum_node_id="chapter-2",
            allowed_curriculum_node_ids=("chapter-1", "chapter-2"),
        )

    def test_exact_unique_anchor_is_a_candidate_with_reproducible_evidence(self):
        result = suggest_mapping(
            question_id="q-1",
            question_text="已知两个三角形全等三角形，求对应边的长度。",
            textbook_id=TEXTBOOK,
            nodes=self.nodes,
        )
        self.assertEqual(result.status, "candidate")
        self.assertEqual(result.candidates[0].node_id, "chapter-2")
        self.assertEqual(result.candidates[0].anchors[0].anchor, "全等三角形")
        self.assertEqual(result.candidates[0].anchors[0].question_offsets, (7,))

    def test_weak_or_missing_evidence_is_isolated(self):
        result = suggest_mapping(
            question_id="q-2",
            question_text="求一个三角形的周长。",
            textbook_id=TEXTBOOK,
            nodes=self.nodes,
        )
        self.assertEqual(result.status, "isolated")
        self.assertEqual(result.isolation_reasons, ("no_exact_catalog_anchor",))

    def test_tied_catalog_anchors_are_isolated_not_guessed(self):
        result = suggest_mapping(
            question_id="q-3",
            question_text="证明两个全等三角形的对应角相等。",
            textbook_id=TEXTBOOK,
            nodes=(
                CurriculumNode("node-a", TEXTBOOK, "2026.1", "全等三角形判定"),
                CurriculumNode("node-b", TEXTBOOK, "2026.1", "全等三角形性质"),
            ),
        )
        self.assertEqual(result.status, "isolated")
        self.assertEqual(result.isolation_reasons, ("ambiguous_top_candidate",))

    def test_cross_textbook_catalog_input_is_isolated(self):
        result = suggest_mapping(
            question_id="q-4",
            question_text="全等三角形的对应边相等。",
            textbook_id=TEXTBOOK,
            nodes=(CurriculumNode("node", "other-book", "2026.1", "全等三角形"),),
        )
        self.assertEqual(result.status, "isolated")
        self.assertIn("cross_textbook_catalog_node", result.isolation_reasons)

    def test_progress_requires_audited_mapping_and_all_nodes_in_range(self):
        suggestion_only = decide_progress_scope(
            ApprovedScopeEvidence("q-5", TEXTBOOK, ("chapter-2",), "candidate", HASH),
            self.progress,
        )
        self.assertEqual(suggestion_only.status, "blocked")
        self.assertIn("mapping_not_approved", suggestion_only.reasons)

        outside = decide_progress_scope(
            ApprovedScopeEvidence("q-5", TEXTBOOK, ("chapter-2", "chapter-3"), "approved", HASH),
            self.progress,
        )
        self.assertEqual(outside.status, "blocked")
        self.assertIn("mapped_node_outside_active_progress", outside.reasons)

        allowed = decide_progress_scope(
            ApprovedScopeEvidence("q-5", TEXTBOOK, ("chapter-1", "chapter-2"), "approved", HASH),
            self.progress,
        )
        self.assertEqual(allowed.status, "within_active_progress")

    def test_invalid_progress_or_provenance_blocks(self):
        result = decide_progress_scope(
            ApprovedScopeEvidence("q-6", TEXTBOOK, ("chapter-2",), "approved", "not-a-hash"),
            ActiveClassProgress("class-8-1", TEXTBOOK, "chapter-2", ("chapter-1",)),
        )
        self.assertEqual(result.status, "blocked")
        self.assertIn("invalid_mapping_provenance", result.reasons)
        self.assertIn("current_node_not_allowed", result.reasons)


if __name__ == "__main__":
    unittest.main()
