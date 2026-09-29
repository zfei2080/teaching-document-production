"""Append-only P1-2f content revision import tests."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from apply_schema_v2_28 import apply as apply_v28
from apply_schema_v2_29 import apply as apply_v29
from controlled_content_revision_import import (
    ControlledContentRevisionImportBlockedError,
    build_revision_manifest,
    import_revision_manifest,
)


ROOT = Path(__file__).parent
P1_2F_BASELINE_DATABASE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)
TEXTBOOK = "bsd-math-grade8-lower-2026-spring-extsrc"
NODE = "bsd-math-8x-2026-node-01-section-02"
ALLOWED = ["bsd-math-8x-2026-node-01-section-01", NODE]


class ControlledContentRevisionImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "db.sqlite"
        self.audit = self.root / "p1-2b.json"
        self.source_paths: dict[str, tuple[Path, Path, Path]] = {}
        shutil.copy2(P1_2F_BASELINE_DATABASE, self.database)
        apply_v28(self.database)
        apply_v29(self.database)
        self._write_audit()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest().upper()

    def _docx(self, path: Path, marker: str) -> None:
        with ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("_rels/.rels", "<Relationships/>")
            archive.writestr("word/document.xml", f"<document>{marker}</document>")

    def _record(self, content_type: str, text: str) -> dict:
        source = self.root / f"{content_type}.doc"
        archive = self.root / f"{content_type}-archive.doc"
        converted = self.root / f"{content_type}.docx"
        source.write_bytes((content_type + " source").encode())
        archive.write_bytes(source.read_bytes())
        self._docx(converted, content_type)
        self.source_paths[content_type] = (source, archive, converted)
        segment = {
            "segment_id": f"{content_type}-segment",
            "content_type": content_type,
            "scope_status": "candidate_only",
            "textbook_id": TEXTBOOK,
            "curriculum_node_id": NODE,
            "required_node_ids": ALLOWED,
            "source_character_range": [0, len(text)],
            "normalized_text_sha256": hashlib.sha256(text.encode()).hexdigest().upper(),
        }
        return {
            "status": "candidate_content_only",
            "content_type": content_type,
            "manifest": {
                "source": {"path": str(source), "sha256": self._hash(source)},
                "archive": {"path": str(archive), "sha256": self._hash(archive)},
                "conversion": {
                    "converted": {"path": str(converted), "sha256": self._hash(converted)},
                    "converter": {"fingerprint": "A" * 64},
                },
                "profiles": {
                    "fidelity_status": "passed",
                    "conversion_differences": [],
                    "source": {"normalized_content_text": text},
                },
                "segments": [segment],
                "quarantined_segments": [],
            },
        }

    def _write_audit(self) -> None:
        audit = {
            "schema": "p1-2b-controlled-content-run-v1",
            "records": [
                self._record("knowledge_explanation", "等腰三角形基础内容"),
                self._record("consolidation_practice", "等腰三角形基础练习"),
            ],
        }
        self.audit.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    def test_revalidated_manifest_appends_new_sources_and_advances_only_content_heads(self) -> None:
        manifest = build_revision_manifest(self.audit, workspace=self.root)
        connection = sqlite3.connect(self.database)
        try:
            result = import_revision_manifest(connection, manifest)
            self.assertEqual(result["status"], "validated")
            self.assertEqual(result["sources"], 2)
            self.assertEqual(result["segments"], 2)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM controlled_content_sources").fetchone()[0],
                4,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM controlled_content_source_revisions").fetchone()[0],
                4,
            )
            self.assertEqual(
                connection.execute("SELECT MIN(head_revision), MAX(head_revision) FROM controlled_content_source_revision_heads").fetchone(),
                (2, 2),
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM current_controlled_content_segments").fetchone()[0],
                2,
            )
            replay = import_revision_manifest(connection, manifest)
            self.assertEqual(replay["status"], "reused")
        finally:
            connection.close()

    def test_unreadable_or_invalid_docx_blocks_with_no_write(self) -> None:
        self.source_paths["knowledge_explanation"][2].write_bytes(b"not-a-docx")
        with self.assertRaisesRegex(
            ControlledContentRevisionImportBlockedError,
            "controlled_content_hash_mismatch|converted_docx_not_readable",
        ):
            build_revision_manifest(self.audit, workspace=self.root)


if __name__ == "__main__":
    unittest.main()
