"""Tests for fail-closed question-to-media adjacency evidence."""

import hashlib
import unittest

from docx_candidate_parser import CandidateQuestion
from docx_media_forensics import MediaInstance
from question_asset_adjacency import assign_instances


def candidate(number: str, paragraphs: tuple[int, ...]) -> CandidateQuestion:
    return CandidateQuestion(number, paragraphs, None, None, (), "stem", None, None, None)


def media(*, paragraph_index, host_kind="body_paragraph", part="word/document.xml", location="/document[1]", relationship_status="resolved_internal"):
    return MediaInstance(part, "rId1", "word/media/a.png", "a.png", hashlib.sha256(b"a").hexdigest(), host_kind, location, paragraph_index, relationship_status)


class AssetAdjacencyTests(unittest.TestCase):
    def test_assigns_only_exact_direct_body_stem_paragraph(self):
        rows = assign_instances([candidate("1", (3, 4))], [media(paragraph_index=4)])
        self.assertEqual(rows[0].assignment_status, "assigned")
        self.assertEqual(rows[0].source_question_no, "1")
        self.assertEqual(rows[0].assignment_reason, "exact_stem_paragraph_membership")

    def test_body_media_outside_stems_stays_unassigned(self):
        rows = assign_instances([candidate("1", (3,))], [media(paragraph_index=4)])
        self.assertEqual(rows[0].assignment_status, "unassigned")
        self.assertIsNone(rows[0].source_question_no)
        self.assertEqual(rows[0].assignment_reason, "paragraph_outside_question_stems")

    def test_non_body_hosts_stay_unassigned_even_if_index_is_present(self):
        instances = [
            media(paragraph_index=3, host_kind="table_cell"),
            media(paragraph_index=3, host_kind="textbox"),
            media(paragraph_index=3, host_kind="header", part="word/header1.xml"),
        ]
        rows = assign_instances([candidate("1", (3,))], instances)
        self.assertTrue(all(row.assignment_status == "unassigned" for row in rows))
        self.assertTrue(all(row.source_question_no is None for row in rows))
        self.assertEqual(
            [row.assignment_reason for row in rows],
            ["unsupported_host:table_cell", "unsupported_host:textbox", "unsupported_host:header"],
        )

    def test_anomalous_relationship_stays_unassigned_even_in_exact_stem(self):
        rows = assign_instances([candidate("1", (3,))], [media(paragraph_index=3, relationship_status="missing_media_part")])
        self.assertEqual(rows[0].assignment_status, "unassigned")
        self.assertEqual(rows[0].assignment_reason, "anomalous_relationship:missing_media_part")

    def test_duplicate_paragraph_ownership_is_ambiguous(self):
        rows = assign_instances([candidate("1", (3,)), candidate("2", (3,))], [media(paragraph_index=3)])
        self.assertEqual(rows[0].assignment_status, "ambiguous")
        self.assertIsNone(rows[0].source_question_no)

    def test_instance_identity_changes_with_host_location(self):
        rows = assign_instances(
            [candidate("1", (3,))],
            [media(paragraph_index=3, location="/a[1]"), media(paragraph_index=3, location="/a[2]")],
        )
        self.assertNotEqual(rows[0].instance_id, rows[1].instance_id)


if __name__ == "__main__":
    unittest.main()
