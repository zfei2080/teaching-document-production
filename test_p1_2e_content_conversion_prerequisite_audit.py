"""P1-2e tests for the read-only conversion-prerequisite audit."""
from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from p1_2e_content_conversion_prerequisite_audit import (
    TARGET_NODE_ID,
    TARGET_TEXTBOOK_ID,
    build_conversion_prerequisite_audit,
    write_conversion_prerequisite_audit,
)


class P12EConversionPrerequisiteAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "db.sqlite"
        self.original = self.root / "original.doc"
        self.archive = self.root / "archive.doc"
        self.converted = self.root / "converted.docx"
        self.original.write_bytes(b"original")
        self.archive.write_bytes(b"original")
        self.converted.write_bytes(b"converted")
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript(
                """
                CREATE TABLE controlled_content_sources (
                    id TEXT PRIMARY KEY, original_path TEXT, original_sha256 TEXT,
                    archive_path TEXT, archive_sha256 TEXT, converted_path TEXT, converted_sha256 TEXT
                );
                CREATE TABLE current_controlled_content_segments (
                    id TEXT PRIMARY KEY, content_type TEXT, source_id TEXT,
                    textbook_id TEXT, curriculum_node_id TEXT
                );
                """
            )
            for index, content_type in enumerate(("knowledge_explanation", "consolidation_practice"), 1):
                source_id = f"source-{index}"
                connection.execute(
                    "INSERT INTO controlled_content_sources VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        source_id,
                        str(self.original),
                        hashlib.sha256(self.original.read_bytes()).hexdigest().upper(),
                        str(self.archive),
                        hashlib.sha256(self.archive.read_bytes()).hexdigest().upper(),
                        str(self.converted),
                        hashlib.sha256(self.converted.read_bytes()).hexdigest().upper(),
                    ),
                )
                connection.execute(
                    "INSERT INTO current_controlled_content_segments VALUES (?, ?, ?, ?, ?)",
                    (f"segment-{index}", content_type, source_id, TARGET_TEXTBOOK_ID, TARGET_NODE_ID),
                )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _pandoc_docx_only(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args[0], 0, stdout="docx\n", stderr="")

    def test_reports_unreadable_or_mismatched_conversion_without_database_write(self) -> None:
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.converted.unlink()
        report = build_conversion_prerequisite_audit(
            self.database,
            word_com_probe=lambda: False,
            command_runner=self._pandoc_docx_only,
        )
        after = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["database"]["unchanged"])
        self.assertEqual(before, after)
        self.assertIn("microsoft_word_com_not_registered", report["recovery_blockers"])
        self.assertIn("pandoc_cannot_replace_word_com_for_legacy_doc_fidelity", report["recovery_blockers"])
        self.assertTrue(any(item.startswith("converted_not_current:") for item in report["blockers"]))
        output = write_conversion_prerequisite_audit(report, self.root / "audit.json")
        self.assertTrue(output.is_file())

    def test_current_triple_is_ready_even_when_com_recovery_is_unavailable(self) -> None:
        report = build_conversion_prerequisite_audit(
            self.database,
            word_com_probe=lambda: False,
            command_runner=self._pandoc_docx_only,
        )
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["controlled_content_chain_current"])
        self.assertIn("microsoft_word_com_not_registered", report["recovery_blockers"])


if __name__ == "__main__":
    unittest.main()
