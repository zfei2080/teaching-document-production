"""P1-4 question-admission coverage for the current P1-3c evidence path."""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from zipfile import ZipFile

from apply_schema_v2_30 import MIGRATION, apply as apply_v30
from automatic_gate import apply_automatic_admission
from controlled_content_revision_import import import_revision_manifest
from input_snapshot import refresh_current_snapshot
from p1_4_pilot_readiness_audit import build_p1_4_readiness_audit
from p1_3c_mapping_approval import NODE_ID, QUESTION_ID, TEXTBOOK_ID, run_p1_3c


ROOT = Path(__file__).parent
P1_2F_BASELINE_DATABASE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)
REQUIRED_TYPES = (
    "source_fidelity",
    "structural_consistency",
    "mathematical_independent",
    "textbook_scope",
    "asset_semantics",
)


class SchemaV230Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "development-copy.db"
        # P1-3c stores immutable source-artifact paths relative to the project
        # root, so its controlled evidence fixture must live inside that root.
        self.evidence_dir = ROOT / "data" / "dev" / f"p1-4-test-{uuid.uuid4().hex}"
        self.evidence_dir.mkdir(parents=True)
        self.report = self.root / "p1-3c.json"
        shutil.copy2(P1_2F_BASELINE_DATABASE, self.database)
        apply_v30(self.database)

    def tearDown(self) -> None:
        shutil.rmtree(self.evidence_dir, ignore_errors=True)
        self.tempdir.cleanup()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

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
            carrier = self.evidence_dir / f"{row['content_type']}.{uuid.uuid4().hex}.docx"
            with ZipFile(carrier, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("_rels/.rels", "<Relationships/>")
                archive.writestr("word/document.xml", f"<document>{row['content_type']}</document>")
            source_id = f"p14-source:{uuid.uuid4().hex}"
            converted_hash = __import__("hashlib").sha256(carrier.read_bytes()).hexdigest().upper()
            contract = {
                "source_id": source_id,
                "segment_id": row["segment_id"],
                "content_type": row["content_type"],
                "textbook_id": row["textbook_id"],
                "curriculum_node_id": row["curriculum_node_id"],
            }
            contract_hash = __import__("hashlib").sha256(
                json.dumps(contract, sort_keys=True).encode("utf-8")
            ).hexdigest().upper()
            revision_hash = __import__("hashlib").sha256(
                (source_id + contract_hash).encode("utf-8")
            ).hexdigest().upper()
            sources.append(
                {
                    "id": source_id,
                    "original": {"path": row["original_path"], "sha256": row["original_sha256"]},
                    "archive": {"path": row["archive_path"], "sha256": row["archive_sha256"]},
                    "converted": {"path": str(carrier), "sha256": converted_hash},
                    "converter_fingerprint": row["converter_fingerprint"],
                    "source_profile_sha256": row["source_profile_sha256"],
                }
            )
            segments.append(
                {
                    "id": f"p14-segment:{uuid.uuid4().hex}",
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
            "p1_2b_audit_sha256": "B" * 64,
            "sources": sources,
            "segments": segments,
        }

    def _prepare_current_p1_3c_mapping(self) -> dict:
        connection = self._connection()
        try:
            result = import_revision_manifest(connection, self._build_content_revision_manifest(connection))
            self.assertEqual(result["status"], "validated")
        finally:
            connection.close()
        report = run_p1_3c(self.database, evidence_dir=self.evidence_dir, report_path=self.report)
        self.assertEqual(report["action"], "approved_one_mapping")
        return report

    def _record_current_legacy_passes(self) -> None:
        connection = self._connection()
        try:
            with connection:
                snapshot = refresh_current_snapshot(connection, QUESTION_ID)
                for verification_type in REQUIRED_TYPES:
                    connection.execute(
                        """INSERT INTO question_verifications
                           (id, question_id, verification_type, validator_id, validator_version,
                            status, evidence_json, input_hash)
                           VALUES (?, ?, ?, 'p1-4-test', '1.0.0', 'pass', ?, ?)""",
                        (
                            f"p14-{verification_type}",
                            QUESTION_ID,
                            verification_type,
                            json.dumps({"fixture": verification_type}, ensure_ascii=False),
                            snapshot,
                        ),
                    )
        finally:
            connection.close()

    def test_legacy_body_evidence_remains_required_without_current_p1_3c_path(self) -> None:
        self._record_current_legacy_passes()
        connection = self._connection()
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "body evidence or current P1-3c"):
                connection.execute(
                    "UPDATE questions SET quality_status='approved', review_status='approved' WHERE id=?",
                    (QUESTION_ID,),
                )
        finally:
            connection.close()

    def test_current_p1_3c_mapping_path_allows_only_fully_current_question_admission(self) -> None:
        self._prepare_current_p1_3c_mapping()
        self._record_current_legacy_passes()
        connection = self._connection()
        try:
            result = apply_automatic_admission(connection, QUESTION_ID)
            self.assertTrue(result.eligible)
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT quality_status, review_status FROM questions WHERE id=?", (QUESTION_ID,)
                    ).fetchone()
                ),
                ("approved", "approved"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM textbook_sources WHERE textbook_id=?", (TEXTBOOK_ID,)
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()
        readiness = build_p1_4_readiness_audit(self.database)
        self.assertEqual(readiness["q013"]["admission_evidence_path"], "current_p1_3c_mapping_evidence")
        self.assertTrue(readiness["q013"]["p1_3c_admission_path"]["usable_for_question_admission"])
        self.assertNotIn("q013_active_textbook_body_evidence_missing", readiness["blockers"])

    def test_p1_3c_source_drift_blocks_automatic_admission_even_with_current_legacy_passes(self) -> None:
        report = self._prepare_current_p1_3c_mapping()
        self._record_current_legacy_passes()
        Path(report["revision_import"]["source_path"]).write_text("tampered\n", encoding="utf-8")
        connection = self._connection()
        try:
            result = apply_automatic_admission(connection, QUESTION_ID)
            self.assertFalse(result.eligible)
            self.assertIn("textbook_scope", result.missing_or_nonpassing)
            self.assertTrue(any(item.startswith("textbook_scope_live:") for item in result.blockers))
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT quality_status, review_status FROM questions WHERE id=?", (QUESTION_ID,)
                    ).fetchone()
                ),
                ("blocked", "pending"),
            )
        finally:
            connection.close()

    def test_migration_is_idempotent(self) -> None:
        apply_v30(self.database)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
