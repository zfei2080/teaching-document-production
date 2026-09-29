"""P1-4b isolated pedagogical-role evidence contract coverage."""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from apply_schema_v2_30 import apply as apply_v30
from apply_schema_v2_31 import apply as apply_v31
from apply_schema_v2_32 import MIGRATION, apply
from p1_4_pilot_readiness_audit import _pedagogical_role_supply


ROOT = Path(__file__).parent
LIVE_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
P1_2F_BASELINE_DATABASE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)
TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
NODE_ID = "bsd-math-8x-2026-node-01-section-02"


class SchemaV232Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "development-copy.db"
        shutil.copy2(P1_2F_BASELINE_DATABASE, self.database)
        apply_v30(self.database)
        apply_v31(self.database)
        apply(self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_migration_is_idempotent_and_contract_is_current_aware(self) -> None:
        apply(self.database)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone()[0],
                1,
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(question_pedagogical_role_evidence)")
            }
            self.assertTrue({
                "question_id", "role", "status", "source_document_id", "source_fragment_id",
                "source_hash", "input_hash", "expected_minutes", "evidence_hash",
            }.issubset(columns))
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='view' AND name='current_question_pedagogical_role_evidence'"
                ).fetchone()
            )
            supply = _pedagogical_role_supply(connection, {"question_ids": ["golden-q013"]})
            self.assertTrue(supply["contract_available"])
            self.assertEqual(supply["assignments"]["public_core"], [])
            self.assertEqual(supply["unallocated_eligible_question_ids"], ["golden-q013"])
        finally:
            connection.close()

    def test_direct_role_assignment_without_validated_source_bound_import_is_zero_write(self) -> None:
        before = self._hash(self.database)
        connection = self._connection()
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "validated evidence import run"):
                connection.execute(
                    """INSERT INTO question_pedagogical_role_evidence
                       (id, import_run_id, question_id, textbook_id, curriculum_node_id, role,
                        source_document_id, source_document_hash, source_fragment_id, source_hash,
                        role_marker, expected_minutes, input_hash, status, evidence_json, evidence_hash)
                       VALUES ('untrusted-role', 'untrusted-run', 'golden-q013', ?, ?, 'public_core',
                               'untrusted-document', ?, 'untrusted-fragment', ?, '显式角色标记', 5, ?,
                               'approved', '{}', ?)""",
                    (TEXTBOOK_ID, NODE_ID, "A" * 64, "B" * 64, "C" * 64, "D" * 64),
                )
        finally:
            connection.close()
        self.assertEqual(before, self._hash(self.database))

    def test_current_view_excludes_evidence_after_question_snapshot_invalidation(self) -> None:
        database = Path(self.tempdir.name) / "live-contract-copy.db"
        shutil.copy2(LIVE_DATABASE, database)
        apply_v31(database)
        apply(database)
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            snapshot = connection.execute(
                "SELECT input_hash FROM question_input_snapshots WHERE question_id='golden-q013'"
            ).fetchone()[0]
            fixture = uuid.uuid4().hex
            document_id = f"role-fixture-document:{fixture}"
            fragment_id = f"role-fixture-fragment:{fixture}"
            run_id = f"role-fixture-run:{fixture}"
            document_hash = "A" * 64
            fragment_hash = "B" * 64
            evidence_hash = "C" * 64
            connection.execute(
                """INSERT INTO source_documents
                   (id, relative_path, file_hash, file_type, source_label, copyright_status, parse_status)
                   VALUES (?, ?, ?, 'docx', 'isolated-role-contract-fixture', 'authorized', 'parsed')""",
                (document_id, f"fixtures/{fixture}.docx", document_hash),
            )
            marker = "[PUBLIC_CORE_EXPLICIT_ROLE_MARKER]"
            connection.execute(
                """INSERT INTO source_fragments
                   (id, source_document_id, location_type, raw_text, raw_hash)
                   VALUES (?, ?, 'paragraph', ?, ?)""",
                (fragment_id, document_id, f"教学角色：{marker}", fragment_hash),
            )
            connection.execute(
                """INSERT INTO controlled_pedagogical_role_import_runs
                   (id, manifest_sha256, importer_id, importer_version, status)
                   VALUES (?, ?, 'isolated-role-contract-fixture', '1.0.0', 'validated_evidence')""",
                (run_id, "D" * 64),
            )
            connection.execute(
                """INSERT INTO question_pedagogical_role_evidence
                   (id, import_run_id, question_id, textbook_id, curriculum_node_id, role,
                    source_document_id, source_document_hash, source_fragment_id, source_hash,
                    role_marker, expected_minutes, input_hash, status, evidence_json, evidence_hash)
                   VALUES (?, ?, 'golden-q013', ?, ?, 'public_core', ?, ?, ?, ?, ?, 5, ?,
                           'approved', '{"fixture":true}', ?)""",
                (
                    f"role-fixture-evidence:{fixture}", run_id, TEXTBOOK_ID, NODE_ID,
                    document_id, document_hash, fragment_id, fragment_hash, marker, snapshot, evidence_hash,
                ),
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM current_question_pedagogical_role_evidence").fetchone()[0],
                1,
            )
            connection.execute(
                """UPDATE question_input_snapshots
                       SET input_hash=NULL, invalidated_at=CURRENT_TIMESTAMP,
                           invalidation_reason='isolated-role-evidence-test'
                     WHERE question_id='golden-q013'"""
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM current_question_pedagogical_role_evidence").fetchone()[0],
                0,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
