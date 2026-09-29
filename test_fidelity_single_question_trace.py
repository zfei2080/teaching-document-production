"""Focused safety tests for the FIDELITY-002 single-question trace."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fidelity_single_question_trace import (
    FidelityTraceError, public_record, readonly_connection, resolve_under_root,
    stage_result, write_outputs,
)


class FidelitySingleQuestionTraceTests(unittest.TestCase):
    def test_readonly_connection_rejects_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "trace.db"
            import sqlite3
            sqlite3.connect(db).execute("CREATE TABLE items (id INTEGER)").connection.close()
            conn = readonly_connection(db)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO items VALUES (1)")
            conn.close()

    def test_stage_result_rejects_unknown_conclusion(self) -> None:
        with self.assertRaises(FidelityTraceError):
            stage_result("A", "invented")

    def test_public_output_excludes_question_text_and_secret(self) -> None:
        trace = {
            "question_id": "q", "source_version_id": "source-version:test",
            "source_relative_path": "trusted/sample.doc", "source_question_no": "1",
            "source_hash_matches": True, "first_proven_issue": None, "stages": [],
            "local_evidence": {"question": {"stem": "secret question text", "token": "secret"}},
        }
        self.assertNotIn("secret question text", json.dumps(public_record(trace), ensure_ascii=False))
        with tempfile.TemporaryDirectory() as directory:
            markdown = Path(directory) / "public.md"
            local, _ = write_outputs(trace, Path(directory), Path(directory) / "public.json", markdown)
            self.assertTrue(local.is_file())
            self.assertNotIn("secret question text", (Path(directory) / "public.json").read_text(encoding="utf-8"))
            self.assertNotIn("secret question text", markdown.read_text(encoding="utf-8"))

    def test_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Exception):
                resolve_under_root(Path(directory), "..\\outside.doc")


if __name__ == "__main__":
    unittest.main()
