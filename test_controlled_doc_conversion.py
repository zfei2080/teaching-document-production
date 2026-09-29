from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from controlled_doc_conversion import (
    LegacyDocConversionBlockedError,
    convert_legacy_doc,
)


class ControlledLegacyDocConversionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        self.source_root = self.workspace / "source"
        self.output_root = self.workspace / "conversion"
        self.source_root.mkdir()
        self.source = self.source_root / "sample.doc"
        self.source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"legacy-doc-fixture")
        self.converter = self.workspace / "fake_converter.py"
        self.converter.write_text(
            """
import sys
from pathlib import Path
from zipfile import ZipFile

if "--version" in sys.argv:
    print("fake-converter 1.0")
    raise SystemExit(0)

outdir = Path(sys.argv[sys.argv.index("--outdir") + 1])
source = Path(sys.argv[-1])
target = outdir / (source.stem + ".docx")
with ZipFile(target, "w") as archive:
    archive.writestr("[Content_Types].xml", "<Types/>")
    archive.writestr("_rels/.rels", "<Relationships/>")
    archive.writestr("word/document.xml", "<w:document/>")
    archive.writestr("word/media/image1.png", b"fixture-image")
""".strip(),
            encoding="utf-8",
        )
        self.command = (sys.executable, str(self.converter))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_conversion_preserves_original_and_creates_structural_only_docx(self) -> None:
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = convert_legacy_doc(
            self.source,
            allowed_root=self.source_root,
            conversion_root=self.output_root,
            converter_command=self.command,
        )
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        self.assertTrue(Path(result.converted_path).is_file())
        self.assertFalse(result.eligible_for_delivery)
        self.assertTrue(result.manifest["conversion"]["source_unchanged"])
        self.assertEqual(
            result.manifest["conversion"]["fidelity_status"],
            "conversion_structural_only",
        )
        self.assertIn("word/media/image1.png", result.manifest["converted"]["media_parts"])

    def test_same_source_and_converter_reuses_exact_converted_copy(self) -> None:
        first = convert_legacy_doc(
            self.source,
            allowed_root=self.source_root,
            conversion_root=self.output_root,
            converter_command=self.command,
        )
        second = convert_legacy_doc(
            self.source,
            allowed_root=self.source_root,
            conversion_root=self.output_root,
            converter_command=self.command,
        )
        self.assertEqual(first.converted_path, second.converted_path)
        self.assertEqual(first.converted_sha256, second.converted_sha256)

    def test_source_outside_allowed_root_is_blocked(self) -> None:
        outside = self.workspace / "outside.doc"
        outside.write_bytes(b"outside")
        with self.assertRaisesRegex(
            LegacyDocConversionBlockedError,
            "source_outside_allowed_root",
        ):
            convert_legacy_doc(
                outside,
                allowed_root=self.source_root,
                conversion_root=self.output_root,
                converter_command=self.command,
            )

    def test_conversion_root_inside_source_root_is_blocked(self) -> None:
        with self.assertRaisesRegex(
            LegacyDocConversionBlockedError,
            "conversion_root_inside_source_root",
        ):
            convert_legacy_doc(
                self.source,
                allowed_root=self.source_root,
                conversion_root=self.source_root / "conversion",
                converter_command=self.command,
            )

    def test_invalid_converted_docx_is_blocked(self) -> None:
        bad_converter = self.workspace / "bad_converter.py"
        bad_converter.write_text(
            """
import sys
from pathlib import Path
if "--version" in sys.argv:
    print("bad-converter 1.0")
    raise SystemExit(0)
outdir = Path(sys.argv[sys.argv.index("--outdir") + 1])
source = Path(sys.argv[-1])
(outdir / (source.stem + ".docx")).write_bytes(b"not-a-docx")
""".strip(),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            LegacyDocConversionBlockedError,
            "converted_docx_invalid",
        ):
            convert_legacy_doc(
                self.source,
                allowed_root=self.source_root,
                conversion_root=self.output_root,
                converter_command=(sys.executable, str(bad_converter)),
            )


if __name__ == "__main__":
    unittest.main()
