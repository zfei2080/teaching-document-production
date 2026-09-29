"""Tests for append-only Word content-block database import."""
from __future__ import annotations

from hashlib import sha256
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apply_schema_v2_30 import apply as apply_v30
from apply_schema_v2_34 import apply as apply_v34
from apply_schema_v2_35 import apply as apply_v35
from content_library_extraction import ContentLibraryExtractionError, import_current_word_source
from source_library_intake import digest_file, register_sources

ROOT = Path(__file__).parent
BASELINE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)


def text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest().upper()


class ContentLibraryExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "content.db"
        self.source_root = self.root / "sources"
        self.source_root.mkdir()
        self.source = self.source_root / "lesson.doc"
        self.source.write_bytes(b"trusted Word source")
        self.manifest_root = self.root / "manifests"
        shutil.copy2(BASELINE, self.database)
        apply_v30(self.database)
        apply_v34(self.database)
        apply_v35(self.database)
        self.connection = sqlite3.connect(self.database)
        self.connection.execute("PRAGMA foreign_keys=ON")
        register_sources(
            self.connection, source_root=self.source_root, mode="baseline", explicit_paths=None,
            workspace=ROOT, actor="test",
        )
        self.source_version_id = self.connection.execute(
            "SELECT id FROM content_source_versions WHERE original_relative_path='lesson.doc'"
        ).fetchone()[0]

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def fake_profile(self) -> dict:
        first = "1. source question"
        second = "Answer evidence"
        return {
            "schema": "word-com-content-extraction-v1",
            "file_type": "doc",
            "source_sha256": digest_file(self.source),
            "normalized_content_sha256": text_hash("1.sourcequestionAnswerevidence"),
            "engine_id": "Microsoft Word COM",
            "engine_version": "test",
            "blocks": [
                {"ordinal": 0, "kind": "paragraph", "locator": {"paragraph_index": 1}, "raw_text": first,
                 "normalized_text": "1.sourcequestion", "raw_sha256": text_hash(first), "normalized_sha256": text_hash("1.sourcequestion")},
                {"ordinal": 1, "kind": "paragraph", "locator": {"paragraph_index": 2}, "raw_text": second,
                 "normalized_text": "Answerevidence", "raw_sha256": text_hash(second), "normalized_sha256": text_hash("Answerevidence")},
            ],
            "assets": [
                {"ordinal": 0, "kind": "formula", "locator": {"equation_index": 1, "range_start": 0, "range_end": 3}},
            ],
        }

    def test_persists_source_blocks_assets_and_ledger_events_without_creating_questions(self) -> None:
        with patch("content_library_extraction.extract_document", return_value=self.fake_profile()) as extract:
            result = import_current_word_source(
                self.connection, source_version_id=self.source_version_id, source_root=self.source_root,
                workspace=ROOT, manifest_root=self.manifest_root, actor="test",
            )
        self.assertEqual(result.status, "extracted")
        self.assertEqual(result.source_blocks, 2)
        self.assertEqual(result.source_assets, 1)
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM source_content_blocks").fetchone()[0], 2)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM source_content_assets").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 20)
        tools = self.connection.execute(
            "SELECT DISTINCT tool_id,tool_version FROM content_change_ledger WHERE import_run_id=?",
            (result.import_run_id,),
        ).fetchall()
        self.assertEqual([tuple(row) for row in tools], [("content-library-word-extraction", "1.0.0")])
        self.assertTrue(list(self.manifest_root.glob("*.json")))
        with patch("content_library_extraction.extract_document") as extract_again:
            duplicate = import_current_word_source(
                self.connection, source_version_id=self.source_version_id, source_root=self.source_root,
                workspace=ROOT, manifest_root=self.manifest_root, actor="test",
            )
        self.assertEqual(duplicate.status, "already_extracted")
        extract_again.assert_not_called()
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM source_content_blocks").fetchone()[0], 2)

    def test_hash_drift_is_blocked_before_word_com_is_called(self) -> None:
        self.source.write_bytes(b"changed")
        with patch("content_library_extraction.extract_document") as extract:
            with self.assertRaisesRegex(ContentLibraryExtractionError, "source_hash_drift_before_word_extraction"):
                import_current_word_source(
                    self.connection, source_version_id=self.source_version_id, source_root=self.source_root,
                    workspace=ROOT, manifest_root=self.manifest_root, actor="test",
                )
        extract.assert_not_called()
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM source_content_blocks").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
