"""P1-3c regression coverage using development-database copies only."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from zipfile import ZipFile

from apply_schema_v2_28 import apply as apply_v28
from apply_schema_v2_29 import apply as apply_v29
from controlled_content_revision_import import import_revision_manifest
from input_snapshot import compute_approval_input_hash_v2
from p1_3c_mapping_approval import (
    NODE_ID,
    QUESTION_ID,
    TEXTBOOK_ID,
    run_p1_3c,
)
from textbook_scope_validator import validate


ROOT = Path(__file__).parent
# P1-3c starts from the explicitly preserved pre-P1-2f development state.
# The live development database may legitimately already contain the mapping
# approval that this test is intended to exercise.
P1_2F_BASELINE_DATABASE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)


class P13CMappingApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "development-copy.db"
        self.report = self.root / "report.json"
        self.workspace_fixture = ROOT / "data" / "dev" / f"p1-3c-test-{uuid.uuid4().hex}"
        self.workspace_fixture.mkdir(parents=True)
        shutil.copy2(P1_2F_BASELINE_DATABASE, self.database)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace_fixture, ignore_errors=True)
        self.tempdir.cleanup()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _make_controlled_content_readable_for_state_machine_only(self) -> None:
        """Append explicit test carriers through the v29 content revision path.

        These bytes are a valid DOCX fixture, not a claim that they preserve the
        source .doc teaching content.  They are used only to exercise the
        mapping approval state machine in an isolated database copy.
        """
        apply_v28(self.database)
        apply_v29(self.database)
        connection = self._connection()
        try:
            result = import_revision_manifest(
                connection, self._build_content_revision_manifest(connection)
            )
            self.assertEqual(result["status"], "validated")
        finally:
            connection.close()

    def _build_content_revision_manifest(self, connection: sqlite3.Connection) -> dict:
        rows = connection.execute(
            """SELECT s.segment_id, s.content_type, s.layer, s.textbook_id,
                      s.curriculum_node_id, s.allowed_node_ids_json, s.required_knowledge_json,
                      s.source_character_range_json, s.normalized_text_sha256,
                      src.original_path, src.original_sha256, src.archive_path, src.archive_sha256,
                      src.converter_fingerprint, src.source_profile_sha256
                 FROM current_controlled_content_segments s
                 JOIN controlled_content_sources src ON src.id=s.source_id
                WHERE s.textbook_id=? AND s.curriculum_node_id=?
                ORDER BY s.content_type""",
            (TEXTBOOK_ID, NODE_ID),
        ).fetchall()
        self.assertEqual(len(rows), 2)
        sources: list[dict] = []
        segments: list[dict] = []
        for row in rows:
            carrier = self.workspace_fixture / f"{row['content_type']}.state-machine-fixture.docx"
            with ZipFile(carrier, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("_rels/.rels", "<Relationships/>")
                archive.writestr("word/document.xml", f"<document>{row['content_type']}</document>")
            source_id = f"test-content-source:{uuid.uuid4().hex}"
            converted_hash = hashlib.sha256(carrier.read_bytes()).hexdigest().upper()
            sources.append(
                {
                    "id": source_id,
                    "original": {"path": row["original_path"], "sha256": row["original_sha256"]},
                    "archive": {"path": row["archive_path"], "sha256": row["archive_sha256"]},
                    "converted": {"path": str(carrier.resolve()), "sha256": converted_hash},
                    "converter_fingerprint": row["converter_fingerprint"],
                    "source_profile_sha256": row["source_profile_sha256"],
                }
            )
            contract_payload = {
                "source_id": source_id,
                "segment_id": row["segment_id"],
                "content_type": row["content_type"],
                "textbook_id": row["textbook_id"],
                "curriculum_node_id": row["curriculum_node_id"],
            }
            contract_hash = hashlib.sha256(
                json.dumps(contract_payload, sort_keys=True).encode("utf-8")
            ).hexdigest().upper()
            revision_hash = hashlib.sha256(
                (source_id + contract_hash).encode("utf-8")
            ).hexdigest().upper()
            segments.append(
                {
                    "id": f"test-content-segment:{uuid.uuid4().hex}",
                    "source_id": source_id,
                    "segment_id": row["segment_id"],
                    "content_type": row["content_type"],
                    "layer": row["layer"],
                    "textbook_id": row["textbook_id"],
                    "curriculum_node_id": row["curriculum_node_id"],
                    "allowed_node_ids": json.loads(row["allowed_node_ids_json"]),
                    "required_knowledge": json.loads(row["required_knowledge_json"]),
                    "source_character_range": json.loads(row["source_character_range_json"]),
                    "normalized_text_sha256": row["normalized_text_sha256"],
                    "segment_contract_hash": contract_hash,
                    "revision_hash": revision_hash,
                }
            )
        return {
            "schema": "p1-2f-controlled-content-revision-manifest-v1",
            "p1_2b_audit_sha256": "A" * 64,
            "sources": sources,
            "segments": segments,
        }

    def _run_positive_path(self) -> dict:
        self._make_controlled_content_readable_for_state_machine_only()
        return run_p1_3c(
            self.database,
            evidence_dir=self.workspace_fixture,
            report_path=self.report,
        )

    def test_missing_current_content_revision_rolls_back_every_database_write(self) -> None:
        self._make_controlled_content_readable_for_state_machine_only()
        connection = self._connection()
        try:
            converted_path = Path(
                connection.execute(
                    """SELECT src.converted_path
                         FROM current_controlled_content_segments s
                         JOIN controlled_content_sources src ON src.id=s.source_id
                        WHERE s.textbook_id=? AND s.curriculum_node_id=?
                        ORDER BY s.content_type
                        LIMIT 1""",
                    (TEXTBOOK_ID, NODE_ID),
                ).fetchone()[0]
            )
        finally:
            connection.close()
        converted_path.unlink()

        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        report = run_p1_3c(
            self.database,
            evidence_dir=self.workspace_fixture,
            report_path=self.report,
        )
        after = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.assertEqual(report["action"], "blocked_zero_mapping_approval")
        self.assertEqual(report["approved_mapping_count"], 0)
        self.assertTrue(any("controlled_content_converted_path_missing" in item for item in report["blockers_before_action"]))
        self.assertEqual(before, after)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT fit_status FROM question_textbooks WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?",
                    (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
                ).fetchone()[0],
                "pending",
            )
            for table in (
                "question_mapping_source_revisions",
                "question_mapping_approval_evidence_v2",
                "question_mapping_auto_audits_v2",
                "question_mapping_approval_audits_v2",
            ):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        finally:
            connection.close()

    def test_approval_is_snapshot_stable_and_scope_validator_consumes_v2_evidence(self) -> None:
        report = self._run_positive_path()
        self.assertEqual(report["action"], "approved_one_mapping")
        self.assertEqual(report["approved_mapping_count"], 1)
        self.assertFalse(report["question_approved"])
        self.assertTrue(report["delivery_tables_empty"])
        self.assertTrue(report["protected_tables_unchanged_except_target_mapping_and_legacy_snapshot"])
        connection = self._connection()
        try:
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT quality_status, review_status FROM questions WHERE id=?", (QUESTION_ID,)
                    ).fetchone()
                ),
                ("blocked", "pending"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT fit_status FROM question_textbooks WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?",
                    (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
                ).fetchone()[0],
                "approved",
            )
            snapshot = connection.execute(
                "SELECT input_hash, revision, invalidated_at FROM question_approval_input_snapshots_v2 WHERE question_id=?",
                (QUESTION_ID,),
            ).fetchone()
            self.assertIsNotNone(snapshot)
            self.assertIsNone(snapshot[2])
            self.assertEqual(snapshot[0], compute_approval_input_hash_v2(connection, QUESTION_ID))
            scope = validate(connection, QUESTION_ID)
            self.assertEqual(scope.status, "pass")
            self.assertEqual(scope.evidence["passes"][0]["p1_3c"]["source_revision_id"], report["final"]["revision"]["id"])
        finally:
            connection.close()
        replay = run_p1_3c(
            self.database,
            evidence_dir=self.workspace_fixture,
            report_path=self.report,
        )
        self.assertEqual(replay["action"], "reused_current_approved_mapping")
        self.assertEqual(replay["approved_mapping_count"], 1)

    def test_answer_or_revision_artifact_tampering_revokes_the_mapping(self) -> None:
        report = self._run_positive_path()
        source_path = Path(report["revision_import"]["source_path"])
        source_path.write_text("tampered\n", encoding="utf-8")
        revoked = run_p1_3c(
            self.database,
            evidence_dir=self.workspace_fixture,
            report_path=self.report,
        )
        self.assertEqual(revoked["action"], "revoked_mapping_to_pending")
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT fit_status FROM question_textbooks WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?",
                    (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
                ).fetchone()[0],
                "pending",
            )
            self.assertEqual(validate(connection, QUESTION_ID).status, "unsupported")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
