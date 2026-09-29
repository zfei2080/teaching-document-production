from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from controlled_content_evidence import (
    ControlledContentBlockedError,
    build_controlled_content_evidence,
    write_evidence_manifest,
)


class ControlledContentEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.source = self.sources / "lesson.doc"
        self.source.write_bytes(b"legacy-office-fixture")
        self.archive = self.root / "archive"
        self.converted = self.root / "converted"
        self.converter = self.root / "converter.py"
        self.converter.write_text(
            """
import sys
from pathlib import Path
from zipfile import ZipFile
if "--version" in sys.argv:
    print("fixture-converter 1.0")
    raise SystemExit(0)
outdir = Path(sys.argv[sys.argv.index("--outdir") + 1])
source = Path(sys.argv[-1])
target = outdir / (source.stem + ".docx")
with ZipFile(target, "w") as archive:
    archive.writestr("[Content_Types].xml", "<Types/>")
    archive.writestr("_rels/.rels", "<Relationships/>")
    archive.writestr("word/document.xml", "<w:document/>")
""".strip(),
            encoding="utf-8",
        )
        self.converter_command = (sys.executable, str(self.converter))
        self.text = "等腰三角形基础讲解直角三角形后续内容"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _profiler(self, path: str | Path) -> dict[str, object]:
        suffix = Path(path).suffix.lower()
        return {
            "schema": "word-com-readonly-profile-v1",
            "file_type": suffix.removeprefix("."),
            "normalized_content_text": self.text,
            "normalized_content_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            "normalized_content_length": len(self.text),
            "paragraph_count": 4,
            "table_signatures": [{"rows": 1, "columns": 2, "text_sha256": "A" * 64}],
            "inline_shape_signatures": [{"type": 3, "width": 24.0, "height": 12.0}],
            "shape_signatures": [],
            "omath_count": 0,
        }

    def test_archives_converts_and_segments_without_delivery_authority(self) -> None:
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        evidence = build_controlled_content_evidence(
            self.source,
            content_type="knowledge_explanation",
            allowed_root=self.sources,
            archive_root=self.archive,
            conversion_root=self.converted,
            converter_command=self.converter_command,
            profiler=self._profiler,
        )
        self.assertEqual(evidence.status, "candidate_content_only")
        self.assertFalse(evidence.eligible_for_delivery)
        manifest = evidence.manifest
        self.assertEqual(manifest["source"]["sha256"].lower(), source_hash)
        self.assertEqual(manifest["archive"]["sha256"].lower(), source_hash)
        self.assertEqual(manifest["profiles"]["conversion_differences"], [])
        self.assertEqual(manifest["segments"][0]["scope_status"], "candidate_only")
        self.assertTrue(any(item["reason"] == "future_dependency_detected" for item in manifest["quarantined_segments"]))
        self.assertFalse(manifest["delivery_eligible"])
        destination = write_evidence_manifest(evidence, self.root / "manifests" / "record.json")
        self.assertTrue(destination.is_file())

    def test_fidelity_mismatch_is_quarantined(self) -> None:
        def mismatching_profiler(path: str | Path) -> dict[str, object]:
            profile = dict(self._profiler(path))
            if Path(path).suffix.lower() == ".docx":
                profile["inline_shape_signatures"] = []
            return profile

        evidence = build_controlled_content_evidence(
            self.source,
            content_type="consolidation_practice",
            allowed_root=self.sources,
            archive_root=self.archive,
            conversion_root=self.converted,
            converter_command=self.converter_command,
            profiler=mismatching_profiler,
        )
        self.assertEqual(evidence.status, "quarantined")
        self.assertIn("inline_shape_signatures", evidence.manifest["profiles"]["conversion_differences"])
        self.assertEqual(evidence.manifest["quarantined_segments"][0]["reason"], "conversion_fidelity_mismatch")

    def test_source_outside_allowed_root_is_blocked(self) -> None:
        outside = self.root / "outside.doc"
        outside.write_bytes(b"outside")
        with self.assertRaisesRegex(ControlledContentBlockedError, "source_outside_allowed_root"):
            build_controlled_content_evidence(
                outside,
                content_type="knowledge_explanation",
                allowed_root=self.sources,
                archive_root=self.archive,
                conversion_root=self.converted,
                converter_command=self.converter_command,
                profiler=self._profiler,
            )

    def test_unknown_content_type_is_blocked(self) -> None:
        with self.assertRaisesRegex(ControlledContentBlockedError, "unsupported_content_type"):
            build_controlled_content_evidence(
                self.source,
                content_type="teacher_answer",
                allowed_root=self.sources,
                archive_root=self.archive,
                conversion_root=self.converted,
                converter_command=self.converter_command,
                profiler=self._profiler,
            )


if __name__ == "__main__":
    unittest.main()
