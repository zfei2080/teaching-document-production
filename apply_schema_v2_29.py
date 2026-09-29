"""Apply and seed P1-2f append-only controlled-content source revisions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


MIGRATION = "v2.29-p12f-append-only-controlled-content-source-revisions"


class SchemaV229MigrationError(RuntimeError):
    """Raised when legacy controlled-content rows cannot be seeded safely."""


def _stable_hash(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _seed_legacy_current_revisions(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """SELECT s.*, src.import_run_id, run.manifest_sha256
             FROM controlled_content_segments s
             JOIN controlled_content_sources src ON src.id=s.source_id
             JOIN controlled_content_import_runs run ON run.id=src.import_run_id
            WHERE s.invalidated_at IS NULL
              AND src.fidelity_status='passed'
              AND run.status='validated'
            ORDER BY s.content_type, s.textbook_id, s.curriculum_node_id, s.id"""
    ).fetchall()
    identities: set[tuple[str, str, str]] = set()
    for row in rows:
        identity = (row[3], row[5], row[6])
        if identity in identities:
            raise SchemaV229MigrationError(
                "multiple_active_content_segments_for_identity:" + ":".join(identity)
            )
        identities.add(identity)
    for row in rows:
        segment_contract = {
            "source_id": row[1],
            "segment_id": row[2],
            "content_type": row[3],
            "layer": row[4],
            "textbook_id": row[5],
            "curriculum_node_id": row[6],
            "allowed_node_ids_json": row[7],
            "required_knowledge_json": row[8],
            "source_character_range_json": row[9],
            "normalized_text_sha256": row[10],
            "scope_status": row[11],
        }
        segment_contract_hash = _stable_hash(segment_contract)
        revision_payload = {
            "schema": "p1-2f-controlled-content-source-revision-v1",
            "source_id": row[1],
            "segment_row_id": row[0],
            "content_type": row[3],
            "textbook_id": row[5],
            "curriculum_node_id": row[6],
            "source_segment_id": row[2],
            "validation_manifest_sha256": row[15],
            "segment_contract_hash": segment_contract_hash,
        }
        revision_hash = _stable_hash(revision_payload)
        revision_id = f"ccsr_{revision_hash}"
        existing = connection.execute(
            "SELECT id FROM controlled_content_source_revisions WHERE revision_hash=?", (revision_hash,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO controlled_content_source_revisions
                   (id, source_id, segment_row_id, content_type, textbook_id, curriculum_node_id,
                    source_segment_id, validation_manifest_sha256, segment_contract_hash, revision_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    revision_id,
                    row[1],
                    row[0],
                    row[3],
                    row[5],
                    row[6],
                    row[2],
                    row[15],
                    segment_contract_hash,
                    revision_hash,
                ),
            )
        else:
            revision_id = existing[0]
        head = connection.execute(
            """SELECT current_revision_id, head_revision
                 FROM controlled_content_source_revision_heads
                WHERE content_type=? AND textbook_id=? AND curriculum_node_id=?""",
            (row[3], row[5], row[6]),
        ).fetchone()
        if head is not None:
            if head[0] != revision_id:
                raise SchemaV229MigrationError(
                    "existing_content_revision_head_conflict:" + ":".join((row[3], row[5], row[6]))
                )
            continue
        event_payload = {
            "schema": "p1-2f-controlled-content-source-revision-head-event-v1",
            "content_type": row[3],
            "textbook_id": row[5],
            "curriculum_node_id": row[6],
            "previous_revision_id": None,
            "replacement_revision_id": revision_id,
            "expected_head_revision": 0,
            "reason": "v2_29_legacy_current_content_seed",
        }
        event_hash = _stable_hash(event_payload)
        connection.execute(
            """INSERT INTO controlled_content_source_revision_head_events
               (id, content_type, textbook_id, curriculum_node_id, previous_revision_id,
                replacement_revision_id, expected_head_revision, reason, event_hash)
               VALUES (?, ?, ?, ?, NULL, ?, 0, ?, ?)""",
            (
                f"ccsrhe_{event_hash}",
                row[3],
                row[5],
                row[6],
                revision_id,
                event_payload["reason"],
                event_hash,
            ),
        )


def apply(db_path: str | Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        if connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)
        ).fetchone():
            print(f"MIGRATION_ALREADY_APPLIED={MIGRATION}")
            return
        if connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version='v2.28-p13c-approval-boundary-and-mapping-revisions'"
        ).fetchone() is None:
            raise SchemaV229MigrationError("v2_28_migration_required_before_v2_29")
        script = (Path(__file__).parent / "schema_v2_29.sql").read_text(encoding="utf-8")
        connection.executescript(script)
        try:
            _seed_legacy_current_revisions(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES(?, datetime('now'))",
                (MIGRATION,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        print(f"MIGRATION_APPLIED={MIGRATION}")
    finally:
        connection.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply_schema_v2_29.py <database_path>")
    apply(sys.argv[1])
