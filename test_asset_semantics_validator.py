"""Regression tests for the fail-closed no-image asset-semantics validator."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from asset_semantics_validator import validate
from import_review_candidates import SOURCE_DOC, SOURCE_ID, file_sha256

ROOT = Path(__file__).parent


class AssetSemanticsValidatorTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in ("schema_v2.sql", "schema_v2_1.sql"):
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self.conn.execute(
            """INSERT INTO source_documents
            (id, relative_path, file_hash, file_type)
            VALUES (?, ?, ?, 'docx')""",
            (SOURCE_ID, SOURCE_DOC.relative_to(ROOT).as_posix(), file_sha256(SOURCE_DOC)),
        )
        self._add_question("golden-q002", "2")
        self._add_question("golden-q001", "1")

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _add_question(self, question_id, question_no):
        from docx_candidate_parser import parse_candidates
        from docx_forensics import extract_paragraphs
        from import_review_candidates import content_hash, split_options

        candidate = next(item for item in parse_candidates(SOURCE_DOC) if item.number == question_no)
        fragments = {item.index: item for item in extract_paragraphs(SOURCE_DOC)}
        stem, options = split_options(candidate.stem_text)
        fragment_id = f"test-p{candidate.stem_fragment_ids[0]:03d}"
        fragment = fragments[candidate.stem_fragment_ids[0]]
        self.conn.execute(
            """INSERT INTO source_fragments
            (id, source_document_id, location_type, paragraph_index, question_number, raw_text, raw_hash)
            VALUES (?, ?, 'paragraph', ?, ?, ?, ?)""",
            (fragment_id, SOURCE_ID, fragment.index, question_no, fragment.text, fragment.sha256),
        )
        self.conn.execute(
            """INSERT INTO questions
            (id, stem, options_json, answer, analysis, question_type, stage, source_document_id,
             source_fragment_id, source_question_no, content_hash)
            VALUES (?, ?, ?, ?, ?, '选择题', '初中', ?, ?, ?, ?)""",
            (question_id, stem, __import__("json").dumps(options, ensure_ascii=False), candidate.answer,
             candidate.analysis_text, SOURCE_ID, fragment_id, question_no,
             content_hash(stem, options, candidate.answer or "", candidate.analysis_text or "")),
        )
        for index in candidate.stem_fragment_ids:
            current = fragments[index]
            current_id = f"test-p{index:03d}"
            if index != candidate.stem_fragment_ids[0]:
                self.conn.execute(
                    """INSERT INTO source_fragments
                    (id, source_document_id, location_type, paragraph_index, question_number, raw_text, raw_hash)
                    VALUES (?, ?, 'paragraph', ?, ?, ?, ?)""",
                    (current_id, SOURCE_ID, current.index, question_no, current.text, current.sha256),
                )
            self.conn.execute(
                """INSERT INTO question_source_fragments
                (question_id, source_fragment_id, field_name, source_hash)
                VALUES (?, ?, 'stem', ?)""",
                (question_id, current_id, current.sha256),
            )

    def test_reviewed_source_locked_no_image_question_passes(self):
        status, evidence = validate(self.conn, "golden-q002")
        self.assertEqual(status, "pass")
        self.assertEqual(evidence["media_filenames"], [])

    def test_question_with_source_media_remains_unsupported(self):
        status, evidence = validate(self.conn, "golden-q001")
        self.assertEqual(status, "unsupported")
        self.assertEqual(evidence["reason"], "no_reviewed_source_locked_rule")

    def test_linked_asset_cancels_no_image_exemption(self):
        self.conn.execute(
            """INSERT INTO question_assets (id, question_id, asset_type, relative_path, status)
            VALUES ('asset-1', 'golden-q002', 'image', 'unused.png', 'pending')"""
        )
        status, evidence = validate(self.conn, "golden-q002")
        self.assertEqual(status, "unsupported")
        self.assertEqual(evidence["reason"], "linked_question_assets_present")


if __name__ == "__main__":
    unittest.main()
