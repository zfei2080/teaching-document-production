from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from source_intake import (
    ISOLATION_REASON_COMPLEX_LAYOUT,
    ISOLATION_REASON_ARCHIVE_HASH_MISMATCH,
    ISOLATION_REASON_INVALID_DOCX,
    ISOLATION_REASON_MISSING_ANSWERS,
    ISOLATION_REASON_MISSING_DOCX_PARTS,
    ISOLATION_REASON_PATH_OUTSIDE_ROOT,
    ISOLATION_REASON_UNCUTTABLE,
    intake_trusted_source,
)


class SourceIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tempdir.name)
        self.docx_root = self.tmp_path / "fixtures"
        self.docx_root.mkdir()
        self.valid_docx = self.docx_root / "trusted-source.docx"
        self._write_docx(
            self.valid_docx,
            {
                "[Content_Types].xml": "<Types/>",
                "_rels/.rels": "<Relationships/>",
                "word/document.xml": "<w:document/>",
                "word/styles.xml": "<w:styles/>",
            },
        )
        self.archive_root = self.tmp_path / "archive"
        self.archive_root.mkdir()
        self.archive_docx = self.archive_root / self.valid_docx.name
        shutil.copy2(self.valid_docx, self.archive_docx)

    def _intake(self, source_path: Path | None = None, archive_path: Path | None = None, **flags):
        options = {
            "trusted_source": True,
            "has_answers": True,
            "has_complex_content": False,
            "can_reliably_segment": True,
            **flags,
        }
        return intake_trusted_source(
            source_path or self.valid_docx,
            self.docx_root,
            archive_path or self.archive_docx,
            self.archive_root,
            **options,
        )

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_intake_generates_deterministic_manifest(self) -> None:
        first = self._intake()
        second = self._intake()

        self.assertEqual(first.manifest, second.manifest)
        self.assertEqual(first.sha256_hex, second.sha256_hex)
        self.assertEqual(first.size_bytes, second.size_bytes)
        self.assertTrue(first.trusted_source)
        self.assertFalse(first.approved)
        self.assertFalse(first.isolated)
        self.assertTrue(first.archive_matches_source)

        record = first.to_source_document_record()
        self.assertTrue(record["trusted_source"])
        self.assertFalse(record["approved"])
        self.assertFalse(record["isolated"])
        self.assertEqual(record["file_hash"], record["original_file_hash"])
        self.assertTrue(json.dumps(record, sort_keys=True))

    def test_tampering_changes_hash_and_size(self) -> None:
        before = self._intake()

        self._write_docx(
            self.valid_docx,
            {
                "[Content_Types].xml": "<Types updated='1'/>",
                "_rels/.rels": "<Relationships/>",
                "word/document.xml": "<w:document><w:p>changed</w:p></w:document>",
            },
        )

        after = self._intake()

        self.assertNotEqual(before.sha256_hex, after.sha256_hex)
        self.assertNotEqual(before.size_bytes, after.size_bytes)
        self.assertTrue(after.isolated)
        self.assertIn(ISOLATION_REASON_ARCHIVE_HASH_MISMATCH, after.isolation_reasons)

    def test_archive_tampering_is_isolated_even_when_original_is_unchanged(self) -> None:
        self.archive_docx.write_bytes(b"tampered archive")
        result = self._intake()
        self.assertTrue(result.isolated)
        self.assertFalse(result.archive_matches_source)
        self.assertIn(ISOLATION_REASON_ARCHIVE_HASH_MISMATCH, result.isolation_reasons)

    def test_outside_root_is_isolated_even_when_source_is_trusted(self) -> None:
        allowed_root = self.tmp_path / "allowed"
        outside_root = self.tmp_path / "outside"
        allowed_root.mkdir()
        outside_root.mkdir()
        path = outside_root / "trusted-source.docx"
        self._write_docx(
            path,
            {
                "[Content_Types].xml": "<Types/>",
                "_rels/.rels": "<Relationships/>",
                "word/document.xml": "<w:document/>",
            },
        )

        archive = self.archive_root / path.name
        shutil.copy2(path, archive)
        result = intake_trusted_source(path, allowed_root, archive, self.archive_root,
                                       trusted_source=True, has_answers=True,
                                       has_complex_content=False, can_reliably_segment=True)

        self.assertTrue(result.trusted_source)
        self.assertFalse(result.approved)
        self.assertTrue(result.isolated)
        self.assertIn(ISOLATION_REASON_PATH_OUTSIDE_ROOT, result.isolation_reasons)

    def test_missing_required_docx_parts_isolated(self) -> None:
        path = self.docx_root / "missing-parts.docx"
        self._write_docx(
            path,
            {
                "[Content_Types].xml": "<Types/>",
                "word/document.xml": "<w:document/>",
            },
        )

        archive = self.archive_root / path.name
        shutil.copy2(path, archive)
        result = intake_trusted_source(path, self.docx_root, archive, self.archive_root,
                                       trusted_source=True, has_answers=True,
                                       has_complex_content=False, can_reliably_segment=True)

        self.assertTrue(result.isolated)
        self.assertIn(ISOLATION_REASON_MISSING_DOCX_PARTS, result.isolation_reasons)
        self.assertIn("_rels/.rels", result.missing_docx_parts)

    def test_invalid_docx_is_isolated(self) -> None:
        path = self.docx_root / "broken.docx"
        path.write_text("not-a-zip", encoding="utf-8")

        archive = self.archive_root / path.name
        shutil.copy2(path, archive)
        result = intake_trusted_source(path, self.docx_root, archive, self.archive_root,
                                       trusted_source=True, has_answers=True,
                                       has_complex_content=False, can_reliably_segment=True)

        self.assertTrue(result.isolated)
        self.assertIn(ISOLATION_REASON_INVALID_DOCX, result.isolation_reasons)

    def test_content_flags_drive_isolation(self) -> None:
        cases = [
            (False, False, True, ISOLATION_REASON_MISSING_ANSWERS),
            (True, True, True, ISOLATION_REASON_COMPLEX_LAYOUT),
            (True, False, False, ISOLATION_REASON_UNCUTTABLE),
        ]

        for has_answers, has_complex_content, can_reliably_segment, reason in cases:
            with self.subTest(reason=reason):
                result = self._intake(
                    has_answers=has_answers,
                    has_complex_content=has_complex_content,
                    can_reliably_segment=can_reliably_segment,
                )

                self.assertTrue(result.trusted_source)
                self.assertFalse(result.approved)
                self.assertTrue(result.isolated)
                self.assertIn(reason, result.isolation_reasons)

    @staticmethod
    def _write_docx(path: Path, parts: dict[str, str]) -> None:
        with ZipFile(path, "w") as archive:
            for name in sorted(parts):
                archive.writestr(name, parts[name])


if __name__ == "__main__":
    unittest.main()
