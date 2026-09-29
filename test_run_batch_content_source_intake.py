"""Focused tests for the explicit batch source-intake CLI.

All tests use temporary paths and a temporary SQLite file.  The trusted-source,
Word COM, and candidate-import modules are mocked so no real source document or
development database is processed.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import run_batch_content_source_intake as intake
import source_library_intake as source_intake


class BatchContentSourceIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source_root = self.root / "sources"
        self.source_root.mkdir()
        self.database = self.root / "intake.db"
        sqlite3.connect(self.database).close()
        (self.source_root / "one.doc").write_bytes(b"one")
        (self.source_root / "two.docx").write_bytes(b"two")
        (self.source_root / "notes.txt").write_text("not Word", encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def arguments(self, *values: str):
        return intake.build_parser().parse_args([
            "--source-root", str(self.source_root), "--database", str(self.database), *values,
        ])

    def test_help_lists_required_parameters(self) -> None:
        help_text = intake.build_parser().format_help()
        for option in (
            "--file", "--file-list", "--all", "--confirm-all", "--continue-on-error",
            "--source-root", "--database", "--actor", "--report",
        ):
            with self.subTest(option=option):
                self.assertIn(option, help_text)

    def test_single_repeated_file_and_utf8_file_list_selection(self) -> None:
        single_mode, single = intake._selection(self.arguments("--file", "one.doc"), source_root=self.source_root)
        self.assertEqual((single_mode, single), ("files", (Path("one.doc"),)))

        repeated_mode, repeated = intake._selection(
            self.arguments("--file", "two.docx", "--file", "one.doc", "--file", "two.docx"),
            source_root=self.source_root,
        )
        self.assertEqual(repeated_mode, "files")
        self.assertEqual(repeated, (Path("two.docx"), Path("one.doc")))

        file_list = self.root / "selection.txt"
        file_list.write_text("# authorized source files\n\none.doc\ntwo.docx\n", encoding="utf-8")
        list_mode, from_list = intake._selection(
            self.arguments("--file-list", str(file_list)), source_root=self.source_root,
        )
        self.assertEqual(list_mode, "file_list")
        self.assertEqual(from_list, (Path("one.doc"), Path("two.docx")))

    def test_all_requires_confirmation_before_any_registration_or_scan(self) -> None:
        with patch.object(intake, "register_sources") as register, redirect_stdout(StringIO()):
            exit_code = intake.main([
                "--source-root", str(self.source_root), "--database", str(self.database), "--all",
            ])
        self.assertEqual(exit_code, 2)
        register.assert_not_called()

        mode, selected = intake._selection(
            self.arguments("--all", "--confirm-all"), source_root=self.source_root,
        )
        self.assertEqual((mode, selected), ("all", None))

    def test_absolute_escape_missing_and_invalid_database_fail_closed(self) -> None:
        for value, expected in (
            (str((self.source_root / "one.doc").resolve()), "source_path_must_be_relative"),
            ("..\\outside.doc", "source_path_escapes_source_root"),
            ("missing.doc", "source_file_missing"),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(intake.BatchContentSourceIntakeError, expected):
                    intake._safe_relative_path(value, source_root=self.source_root)

        invalid_database = self.root / "missing.db"
        arguments = intake.build_parser().parse_args([
            "--source-root", str(self.source_root), "--database", str(invalid_database),
            "--file", "one.doc",
        ])
        with patch.object(intake, "register_sources") as register:
            report = intake._failure_report(arguments, intake.BatchContentSourceIntakeError("database_missing"))
            with self.assertRaisesRegex(intake.BatchContentSourceIntakeError, "database_missing"):
                intake.run(arguments)
        register.assert_not_called()
        self.assertEqual(report["fatal_error"]["stage"], "preflight")

    def test_explicit_word_file_orchestrates_existing_modules_and_reports_counts(self) -> None:
        registration = SimpleNamespace(import_run_id="registration:one")
        extraction = SimpleNamespace(status="extracted")
        imported = SimpleNamespace(status="imported")
        source_version = intake.SourceVersion("source-version:one", "doc", True)
        summary = {
            "structured_questions": 3,
            "math_validation_status_counts": {"pass": 1, "unsupported": 2},
            "content_eligible": 1,
            "blocked": 2,
        }
        with (
            patch.object(intake, "register_sources", return_value=registration) as register,
            patch.object(intake, "_source_version_for_path", return_value=source_version),
            patch.object(intake, "import_current_word_source", return_value=extraction) as extract,
            patch.object(intake, "import_question_candidates", return_value=imported) as import_candidates,
            patch.object(intake, "_source_summary", return_value=summary),
        ):
            report, exit_code = intake.run(self.arguments("--file", "one.doc", "--actor", "test"))

        self.assertEqual(exit_code, 0)
        register.assert_called_once()
        self.assertEqual(register.call_args.kwargs["mode"], "delta")
        self.assertEqual(register.call_args.kwargs["explicit_paths"], ["one.doc"])
        extract.assert_called_once()
        import_candidates.assert_called_once()
        file_result = report["files"][0]
        self.assertEqual(file_result["status"], "completed")
        self.assertEqual(file_result["source_version_id"], "source-version:one")
        self.assertEqual(file_result["blocked"], 2)
        self.assertEqual(report["summary"]["math_validation_status_counts"], {"pass": 1, "unsupported": 2})
        self.assertTrue(report["summary"]["foreign_key_check"]["passed"])

    def test_all_uses_existing_baseline_registration_and_explicit_selection_record(self) -> None:
        registration = SimpleNamespace(import_run_id="registration:all")
        def source_version(*_args, relative_path: Path, **_kwargs):
            if relative_path == Path("one.doc"):
                return intake.SourceVersion("source-version:one", "doc", True)
            return intake.SourceVersion(None, None, True)

        with (
            patch.object(intake, "register_sources", return_value=registration) as register,
            patch.object(intake, "_relative_paths_from_registration", return_value=(Path("one.doc"), Path("notes.txt"))),
            patch.object(intake, "_source_version_for_path", side_effect=source_version),
            patch.object(intake, "import_current_word_source", return_value=SimpleNamespace(status="extracted")) as extract,
            patch.object(intake, "import_question_candidates", return_value=SimpleNamespace(status="deferred")) as import_candidates,
            patch.object(intake, "_source_summary", return_value={
                "structured_questions": 0, "math_validation_status_counts": {}, "content_eligible": 0, "blocked": 0,
            }),
        ):
            report, exit_code = intake.run(self.arguments("--all", "--confirm-all"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(register.call_args.kwargs["mode"], "baseline")
        self.assertIsNone(register.call_args.kwargs["explicit_paths"])
        extract.assert_called_once()
        import_candidates.assert_called_once()
        self.assertEqual(report["summary"]["requested_files"], 2)
        self.assertEqual(report["summary"]["unsupported_files"], 1)
        self.assertEqual(report["files"][1]["relative_path"], "notes.txt")
        self.assertEqual(report["files"][1]["extraction_status"], "not_applicable")


    def test_all_reuses_existing_logical_registration_event_with_new_actor(self) -> None:
        event = {
            "event_type": "import_run_started",
            "entity_type": "content_import_runs",
            "entity_id": "content-import-run:all",
            "operation": "create",
            "reason": "create explicit source registration batch",
            "import_run_id": None,
            "transaction_id": "transaction:all-registration",
            "after_state": {"mode": "baseline", "relative_paths": ["one.doc"]},
            "after_hash": "E" * 64,
        }
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("""
                CREATE TABLE content_change_ledger (
                    id TEXT PRIMARY KEY NOT NULL,
                    event_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    before_state_json TEXT,
                    after_state_json TEXT,
                    before_hash TEXT,
                    after_hash TEXT,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    tool_version TEXT NOT NULL,
                    import_run_id TEXT,
                    transaction_id TEXT NOT NULL,
                    git_revision TEXT
                )
            """)
            event_id, created = source_intake._insert_event(
                connection, **event, actor="prior-actor", tool_id="prior-tool",
                tool_version="1.0.0", git_revision="A" * 40,
            )
            self.assertTrue(created)
            connection.commit()
        finally:
            connection.close()

        def reuse_existing_registration(connection: sqlite3.Connection, **kwargs):
            self.assertEqual(kwargs["actor"], "manual-batch-intake")
            reused_id, created = source_intake._insert_event(
                connection, **event, actor=kwargs["actor"], tool_id="retry-tool",
                tool_version="2.0.0", git_revision="B" * 40,
            )
            self.assertEqual(reused_id, event_id)
            self.assertFalse(created)
            return SimpleNamespace(import_run_id="registration:all")

        with (
            patch.object(intake, "register_sources", side_effect=reuse_existing_registration) as register,
            patch.object(intake, "_relative_paths_from_registration", return_value=(Path("one.doc"),)),
            patch.object(intake, "_source_version_for_path", return_value=intake.SourceVersion("source-version:one", "doc", True)),
            patch.object(intake, "import_current_word_source", return_value=SimpleNamespace(status="extracted")) as extract,
            patch.object(intake, "import_question_candidates", return_value=SimpleNamespace(status="deferred")) as import_candidates,
            patch.object(intake, "_source_summary", return_value={
                "structured_questions": 0, "math_validation_status_counts": {}, "content_eligible": 0, "blocked": 0,
            }),
        ):
            report, exit_code = intake.run(
                self.arguments("--all", "--confirm-all", "--actor", "manual-batch-intake")
            )

        self.assertEqual(exit_code, 0)
        self.assertIsNone(report["fatal_error"])
        self.assertEqual(report["summary"]["processed_files"], 1)
        register.assert_called_once()
        extract.assert_called_once()
        import_candidates.assert_called_once()
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM content_change_ledger WHERE id=?", (event_id,)).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT actor,tool_id,tool_version,git_revision FROM content_change_ledger WHERE id=?",
                    (event_id,),
                ).fetchone(),
                ("prior-actor", "prior-tool", "1.0.0", "A" * 40),
            )
        finally:
            connection.close()

    def test_source_summary_keeps_pending_eligible_and_blocked_separate(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript("""
                CREATE TABLE content_items (id TEXT PRIMARY KEY, source_version_id TEXT NOT NULL);
                CREATE TABLE content_item_math_validation_evidence (content_item_id TEXT NOT NULL, validation_status TEXT NOT NULL);
                CREATE TABLE content_item_question_links (content_item_id TEXT PRIMARY KEY, question_id TEXT NOT NULL);
                CREATE TABLE questions (id TEXT PRIMARY KEY, quality_status TEXT NOT NULL);
            """)
            connection.executemany(
                "INSERT INTO content_items(id,source_version_id) VALUES(?,?)",
                [("item-pass", "version"), ("item-unsupported", "version"), ("item-blocked", "version")],
            )
            connection.executemany(
                "INSERT INTO content_item_math_validation_evidence(content_item_id,validation_status) VALUES(?,?)",
                [("item-pass", "pass"), ("item-unsupported", "unsupported"), ("item-blocked", "fail")],
            )
            connection.executemany(
                "INSERT INTO content_item_question_links(content_item_id,question_id) VALUES(?,?)",
                [("item-pass", "question-pass"), ("item-unsupported", "question-unsupported"), ("item-blocked", "question-blocked")],
            )
            connection.executemany(
                "INSERT INTO questions(id,quality_status) VALUES(?,?)",
                [("question-pass", "pending"), ("question-unsupported", "blocked"), ("question-blocked", "blocked")],
            )
            summary = intake._source_summary(connection, "version")
        finally:
            connection.close()
        self.assertEqual(summary["structured_questions"], 3)
        self.assertEqual(summary["math_validation_status_counts"], {"fail": 1, "pass": 1, "unsupported": 1})
        self.assertEqual(summary["content_eligible"], 1)
        self.assertEqual(summary["blocked"], 2)

    def test_default_failure_stops_and_continue_on_error_is_explicit(self) -> None:
        registration = SimpleNamespace(import_run_id="registration:failure")
        def source_version(*_args, relative_path: Path, **_kwargs):
            return intake.SourceVersion(f"source-version:{relative_path.stem}", "doc", True)

        common = (
            patch.object(intake, "register_sources", return_value=registration),
            patch.object(intake, "_source_version_for_path", side_effect=source_version),
            patch.object(intake, "import_current_word_source", return_value=SimpleNamespace(status="extracted")),
            patch.object(intake, "import_question_candidates", side_effect=RuntimeError("candidate import failed")),
        )
        with common[0], common[1], common[2], common[3]:
            report, exit_code = intake.run(self.arguments("--file", "one.doc", "--file", "two.docx"))
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["summary"]["processed_files"], 1)
        self.assertEqual(report["files"][0]["error"]["stage"], "import")
        self.assertEqual(report["files"][0]["source_version_id"], "source-version:one")
        self.assertEqual(report["files"][0]["registration_status"], "registered")
        self.assertEqual(report["files"][0]["extraction_status"], "extracted")
        self.assertTrue(report["files"][0]["error"]["retry_recommended"])

        with (
            patch.object(intake, "register_sources", return_value=registration),
            patch.object(intake, "_source_version_for_path", side_effect=source_version),
            patch.object(intake, "import_current_word_source", return_value=SimpleNamespace(status="extracted")),
            patch.object(intake, "import_question_candidates", side_effect=RuntimeError("candidate import failed")),
        ):
            continued, continued_code = intake.run(
                self.arguments("--file", "one.doc", "--file", "two.docx", "--continue-on-error")
            )
        self.assertEqual(continued_code, 1)
        self.assertEqual(continued["summary"]["processed_files"], 2)
        self.assertEqual(continued["summary"]["failed_files"], 2)


if __name__ == "__main__":
    unittest.main()
