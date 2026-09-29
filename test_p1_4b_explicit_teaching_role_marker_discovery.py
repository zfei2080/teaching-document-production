"""Regression tests for source-bound literal teaching-role marker discovery."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import tempfile
import unittest

from p1_4b_explicit_teaching_role_marker_discovery import (
    EXPECTED_SOURCE_KEYS,
    ExplicitMarkerDiscoveryError,
    build_explicit_teaching_role_marker_discovery,
)


@dataclass(frozen=True)
class Paragraph:
    index: int
    text: str
    sha256: str


class ExplicitTeachingRoleMarkerDiscoveryTests(unittest.TestCase):
    def _audit(self, root: Path) -> Path:
        records = []
        for index, key in enumerate(sorted(EXPECTED_SOURCE_KEYS)):
            source = root / f"{key}.doc"
            converted = root / f"{key}.docx"
            source.write_bytes(f"source-{key}".encode())
            converted.write_bytes(f"converted-{key}".encode())
            records.append(
                {
                    "source_key": key,
                    "source_relative_path": f"known/{key}.doc",
                    "content_type": "knowledge_explanation" if "explanation" in key else "consolidation_practice",
                    "status": "candidate_content_only",
                    "manifest": {
                        "delivery_eligible": False,
                        "source": {"path": str(source), "sha256": sha256(source.read_bytes()).hexdigest().upper()},
                        "conversion": {"converted": {"path": str(converted), "sha256": sha256(converted.read_bytes()).hexdigest().upper()}},
                        "profiles": {"converted": {"normalized_content_sha256": f"{index:064X}"}},
                    },
                }
            )
        audit = root / "p1-2b.json"
        audit.write_text(json.dumps({"schema": "p1-2b-controlled-content-run-v1", "records": records}), encoding="utf-8")
        return audit

    def test_reports_literal_candidates_without_assigning_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit = self._audit(Path(directory))
            def extractor(path: str | Path):
                text = "例题方法：这是原文。" if "advanced" in str(path) else "普通段落"
                return (Paragraph(3, text, sha256(text.encode()).hexdigest().upper()),)
            report = build_explicit_teaching_role_marker_discovery(audit, paragraph_extractor=extractor)
            self.assertFalse(report["role_assignment_performed"])
            self.assertFalse(report["student_document_generation_authorized"])
            self.assertGreater(report["candidate_counts"]["G3_controlled_example_or_method"], 0)
            self.assertTrue(all(item["evidence_status"] == "literal_marker_candidate_only" for item in report["candidates"]))
            self.assertTrue(all("paragraph_sha256" in item for item in report["candidates"]))

    def test_rejects_a_changed_converted_source_before_marker_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = self._audit(root)
            payload = json.loads(audit.read_text(encoding="utf-8"))
            changed = Path(payload["records"][0]["manifest"]["conversion"]["converted"]["path"])
            changed.write_bytes(b"tampered")
            with self.assertRaisesRegex(ExplicitMarkerDiscoveryError, "source_conversion_not_current"):
                build_explicit_teaching_role_marker_discovery(audit, paragraph_extractor=lambda _: ())


if __name__ == "__main__":
    unittest.main()
