"""Tests for explicit, append-only source registration."""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apply_schema_v2_30 import apply as apply_v30
from apply_schema_v2_34 import apply as apply_v34
from apply_schema_v2_35 import apply as apply_v35
import content_question_candidate_import as candidate_import
import source_library_intake as source_intake
from source_library_intake import ContentSourceRegistrationError, register_sources

ROOT = Path(__file__).parent
BASELINE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)


class SourceLibraryIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "content.db"
        self.source_root = self.root / "sources"
        self.source_root.mkdir()
        (self.source_root / "one.doc").write_bytes(b"one")
        nested = self.source_root / "nested"
        nested.mkdir()
        (nested / "two.docx").write_bytes(b"two")
        (nested / "bundle.zip").write_bytes(b"zip")
        shutil.copy2(BASELINE, self.database)
        apply_v30(self.database)
        apply_v34(self.database)
        apply_v35(self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def test_existing_ledger_event_reuses_tuple_and_row_results_while_ignoring_runtime_provenance(self) -> None:
        connection = self.connection()
        try:
            shared = {
                "event_type": "source_registered",
                "entity_type": "source_documents",
                "entity_id": f"source-document:{self.root.name}",
                "operation": "create",
                "reason": "register fixture source",
                "import_run_id": f"content-import-run:{self.root.name}",
                "transaction_id": f"transaction:{self.root.name}",
                "before_state": {"status": "pending"},
                "after_state": {"status": "registered"},
                "before_hash": "C" * 64,
                "after_hash": "D" * 64,
            }
            event_id, created = source_intake._insert_event(
                connection, **shared, actor="first-actor", tool_id="first-tool",
                tool_version="1.0.0", git_revision="A" * 40,
            )
            self.assertTrue(created)
            tuple_reused_id, tuple_reused = source_intake._insert_event(
                connection, **shared, actor="tuple-retry-actor", tool_id="tuple-retry-tool",
                tool_version="1.5.0", git_revision="B" * 40,
            )
            self.assertEqual(tuple_reused_id, event_id)
            self.assertFalse(tuple_reused)
            connection.row_factory = sqlite3.Row
            reused_id, reused = source_intake._insert_event(
                connection, **shared, actor="row-retry-actor", tool_id="row-retry-tool",
                tool_version="2.0.0", git_revision="C" * 40,
            )
            self.assertEqual(reused_id, event_id)
            self.assertFalse(reused)
            connection.row_factory = lambda _cursor, row: list(row)
            sequence_reused_id, sequence_reused = source_intake._insert_event(
                connection, **shared, actor="sequence-retry-actor", tool_id="sequence-retry-tool",
                tool_version="3.0.0", git_revision="D" * 40,
            )
            self.assertEqual(sequence_reused_id, event_id)
            self.assertFalse(sequence_reused)
            connection.row_factory = None
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM content_change_ledger WHERE id=?", (event_id,)).fetchone()[0],
                1,
            )
            self.assertEqual(
                tuple(connection.execute(
                    "SELECT actor,tool_id,tool_version,git_revision FROM content_change_ledger WHERE id=?",
                    (event_id,),
                ).fetchone()),
                ("first-actor", "first-tool", "1.0.0", "A" * 40),
            )
        finally:
            connection.close()

    def test_reregistering_same_sources_reuses_events_when_actor_and_git_revision_change(self) -> None:
        connection = self.connection()
        try:
            with patch.object(source_intake, "_git_revision", return_value="A" * 40):
                first = register_sources(
                    connection, source_root=self.source_root, mode="baseline", explicit_paths=None,
                    workspace=ROOT, actor="first-actor",
                )
            with patch.object(source_intake, "_git_revision", return_value="B" * 40):
                rerun = register_sources(
                    connection, source_root=self.source_root, mode="baseline", explicit_paths=None,
                    workspace=ROOT, actor="retry-actor",
                )
            self.assertEqual(rerun.import_run_id, first.import_run_id)
            self.assertEqual(rerun.registered_source_records, 0)
            self.assertEqual(rerun.existing_source_records, 3)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM content_change_ledger "
                    "WHERE event_type='import_run_started' AND entity_type='content_import_runs' AND entity_id=?",
                    (first.import_run_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT actor,git_revision FROM content_change_ledger "
                    "WHERE event_type='import_run_started' AND entity_type='content_import_runs' AND entity_id=?",
                    (first.import_run_id,),
                ).fetchone(),
                ("first-actor", "A" * 40),
            )
        finally:
            connection.close()

    def test_existing_ledger_event_with_changed_business_semantics_fails_closed(self) -> None:
        connection = self.connection()
        try:
            shared = {
                "event_type": "source_registered",
                "entity_type": "source_documents",
                "entity_id": f"source-document:{self.root.name}",
                "operation": "create",
                "reason": "register fixture source",
                "actor": "actor",
                "import_run_id": f"content-import-run:{self.root.name}",
                "before_state": {"status": "pending"},
                "after_state": {"status": "registered"},
                "before_hash": "C" * 64,
                "after_hash": "D" * 64,
            }
            changed_values = {
                "before_state": {"status": "other"},
                "after_state": {"status": "other"},
                "reason": "different reason",
                "import_run_id": f"content-import-run:other:{self.root.name}",
                "transaction_id": f"transaction:other:{self.root.name}",
            }
            for field, changed_value in changed_values.items():
                with self.subTest(field=field):
                    event_id = f"event:{self.root.name}:collision:{field}"
                    base = {**shared, "transaction_id": f"transaction:{self.root.name}:collision:{field}"}
                    with patch.object(source_intake, "_event_id", return_value=event_id):
                        source_intake._insert_event(connection, **base)
                        changed = {**base, field: changed_value}
                        with self.assertRaisesRegex(ContentSourceRegistrationError, "change_ledger_id_collision"):
                            source_intake._insert_event(connection, **changed)
        finally:
            connection.close()

    def test_record_deferred_reuses_existing_ledger_event_when_row_factory_returns_row(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript("""
                CREATE TABLE content_change_ledger (
                    id TEXT PRIMARY KEY,
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
                );
                CREATE TABLE content_source_lifecycle_events (
                    id TEXT PRIMARY KEY,
                    source_version_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    retry_eligible INTEGER NOT NULL,
                    change_event_id TEXT NOT NULL
                );
            """)
            deferred = {
                "source_version_id": f"source-version:{self.root.name}",
                "extraction_run_id": f"extraction-run:{self.root.name}",
                "reason": "question_candidate_segmentation_deferred:duplicate_option_label",
                "workspace": ROOT,
            }
            with patch.object(candidate_import, "_git_revision", return_value="A" * 40):
                first = candidate_import._record_deferred(connection, **deferred, actor="first-actor")
            self.assertEqual(first.status, "deferred")
            self.assertEqual(first.change_events_created, 1)
            connection.row_factory = sqlite3.Row
            with patch.object(candidate_import, "_git_revision", return_value="B" * 40):
                retry = candidate_import._record_deferred(connection, **deferred, actor="retry-actor")
            self.assertEqual(retry.status, "deferred")
            self.assertEqual(retry.change_events_created, 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_change_ledger").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_source_lifecycle_events").fetchone()[0], 1)
        finally:
            connection.close()

    def test_baseline_is_explicit_idempotent_and_records_every_registered_revision(self) -> None:
        connection = self.connection()
        try:
            result = register_sources(
                connection, source_root=self.source_root, mode="baseline", explicit_paths=None,
                workspace=ROOT, actor="test",
            )
            self.assertEqual(result.requested_files, 3)
            self.assertEqual(result.registered_source_records, 3)
            self.assertEqual(result.registered_versions, 2)
            self.assertEqual(result.unsupported_files, 1)
            self.assertEqual(result.source_documents_created, 2)
            self.assertEqual(result.source_heads_advanced, 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_source_intake_records").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_source_versions").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_source_intake_lifecycle_events WHERE status='unsupported'").fetchone()[0], 1)
            completed = connection.execute(
                "SELECT totals_json FROM content_import_run_events WHERE import_run_id=? AND status='completed'",
                (result.import_run_id,),
            ).fetchone()[0]
            self.assertEqual(json.loads(completed)["registered_versions"], 2)
            rerun = register_sources(
                connection, source_root=self.source_root, mode="baseline", explicit_paths=None,
                workspace=ROOT, actor="test",
            )
            self.assertEqual(rerun.registered_source_records, 0)
            self.assertEqual(rerun.existing_source_records, 3)
            self.assertEqual(rerun.registered_versions, 0)
            self.assertEqual(rerun.existing_versions, 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_source_versions").fetchone()[0], 2)
        finally:
            connection.close()

    def test_delta_registers_only_explicit_changed_path_and_advances_version_head(self) -> None:
        connection = self.connection()
        try:
            baseline = register_sources(
                connection, source_root=self.source_root, mode="baseline", explicit_paths=None,
                workspace=ROOT, actor="test",
            )
            (self.source_root / "one.doc").write_bytes(b"one changed")
            delta = register_sources(
                connection, source_root=self.source_root, mode="delta", explicit_paths=["one.doc"],
                workspace=ROOT, actor="test",
            )
            self.assertEqual(delta.requested_files, 1)
            self.assertEqual(delta.registered_versions, 1)
            self.assertEqual(delta.source_documents_created, 0)
            self.assertEqual(delta.source_heads_advanced, 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_source_intake_records").fetchone()[0], 4)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_source_versions").fetchone()[0], 3)
            head = connection.execute(
                """SELECT v.original_relative_path, v.original_sha256
                     FROM content_source_version_heads h
                     JOIN content_source_versions v ON v.id=h.current_source_version_id
                     JOIN source_documents d ON d.id=h.source_document_id
                    WHERE d.relative_path='one.doc'"""
            ).fetchone()
            self.assertEqual(head[0], "one.doc")
            self.assertNotEqual(head[1], connection.execute(
                "SELECT original_sha256 FROM content_source_versions WHERE registered_import_run_id=? AND original_relative_path='one.doc'",
                (baseline.import_run_id,),
            ).fetchone()[0])
            statuses = connection.execute(
                "SELECT COUNT(*) FROM content_change_ledger WHERE entity_type='content_source_version_heads'"
            ).fetchone()[0]
            self.assertEqual(statuses, 3)
        finally:
            connection.close()

    def test_incremental_mode_requires_explicit_path_and_rejects_outside_root(self) -> None:
        connection = self.connection()
        try:
            with self.assertRaisesRegex(ContentSourceRegistrationError, "incremental_paths_required"):
                register_sources(
                    connection, source_root=self.source_root, mode="delta", explicit_paths=None,
                    workspace=ROOT,
                )
            outside = self.root / "outside.doc"
            outside.write_bytes(b"outside")
            with self.assertRaisesRegex(ContentSourceRegistrationError, "explicit_source_path_invalid"):
                register_sources(
                    connection, source_root=self.source_root, mode="delta", explicit_paths=[outside],
                    workspace=ROOT,
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
