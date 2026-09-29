"""Append-only importer for a revalidated P1-2b controlled-content chain."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile
from contextlib import nullcontext
import json
import sqlite3

from controlled_content_temp_import import (
    ControlledContentImportBlockedError,
    build_manifest as build_p1_2c_manifest,
)


SCHEMA = "p1-2f-controlled-content-revision-manifest-v1"
IMPORTER_ID = "p1-2f-controlled-content-revision-import"
IMPORTER_VERSION = "1.0.0"
REQUIRED_DOCX_PARTS = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}


class ControlledContentRevisionImportBlockedError(RuntimeError):
    """Raised when a revalidated content revision is incomplete or unsafe."""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_bytes(value: bytes) -> str:
    return sha256(value).hexdigest().upper()


def _hash_file(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _require_readable_docx(path: Path) -> None:
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, BadZipFile) as exc:
        raise ControlledContentRevisionImportBlockedError(
            f"converted_docx_not_readable:{type(exc).__name__}"
        ) from exc
    if not REQUIRED_DOCX_PARTS.issubset(names):
        raise ControlledContentRevisionImportBlockedError("converted_docx_required_parts_missing")


def _require_v29(connection: sqlite3.Connection) -> None:
    required = {
        "controlled_content_source_revisions",
        "controlled_content_source_revision_heads",
        "controlled_content_source_revision_head_events",
    }
    available = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    if not required.issubset(available):
        raise ControlledContentRevisionImportBlockedError("v2_29_content_revision_schema_required")


def build_revision_manifest(audit_path: str | Path, *, workspace: str | Path) -> dict[str, Any]:
    """Validate P1-2b evidence and derive unique append-only revision IDs."""
    try:
        base = build_p1_2c_manifest(audit_path, workspace=workspace)
    except ControlledContentImportBlockedError as exc:
        raise ControlledContentRevisionImportBlockedError(str(exc)) from exc
    audit = Path(audit_path).resolve()
    workspace_path = Path(workspace).resolve()
    sources: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for source in base["sources"]:
        converted = Path(source["converted"]["path"])
        _require_readable_docx(converted)
        source_payload = {
            "schema": SCHEMA,
            "p1_2b_audit_sha256": base["p1_2b_audit_sha256"],
            "original": source["original"],
            "archive": source["archive"],
            "converted": source["converted"],
            "converter_fingerprint": source["converter_fingerprint"],
            "source_profile_sha256": source["source_profile_sha256"],
        }
        source_hash = _hash_bytes(_canonical(source_payload).encode("utf-8"))
        sources.append({**source, "id": f"ccsrev:{source_hash[:24]}", "source_revision_hash": source_hash})
    sources_by_original = {item["original"]["sha256"]: item for item in sources}
    for segment in base["segments"]:
        source = sources_by_original.get(
            next(
                item["original"]["sha256"]
                for item in base["sources"] if item["id"] == segment["source_id"]
            )
        )
        if source is None:
            raise ControlledContentRevisionImportBlockedError("source_segment_binding_missing")
        contract = {
            "source_id": source["id"],
            "segment_id": segment["segment_id"],
            "content_type": segment["content_type"],
            "layer": segment["layer"],
            "textbook_id": segment["textbook_id"],
            "curriculum_node_id": segment["curriculum_node_id"],
            "allowed_node_ids": segment["allowed_node_ids"],
            "required_knowledge": segment["required_knowledge"],
            "source_character_range": segment["source_character_range"],
            "normalized_text_sha256": segment["normalized_text_sha256"],
        }
        contract_hash = _hash_bytes(_canonical(contract).encode("utf-8"))
        revision_payload = {
            "schema": SCHEMA,
            "source_revision_hash": source["source_revision_hash"],
            "p1_2b_audit_sha256": base["p1_2b_audit_sha256"],
            "segment_contract_hash": contract_hash,
        }
        revision_hash = _hash_bytes(_canonical(revision_payload).encode("utf-8"))
        segments.append(
            {
                **segment,
                "id": f"ccsegrev:{revision_hash[:24]}",
                "source_id": source["id"],
                "segment_contract_hash": contract_hash,
                "revision_hash": revision_hash,
            }
        )
    return {
        "schema": SCHEMA,
        "p1_2b_audit_path": str(audit),
        "p1_2b_audit_sha256": base["p1_2b_audit_sha256"],
        "workspace": str(workspace_path),
        "base_manifest_hash": _hash_bytes(_canonical(base).encode("utf-8")),
        "sources": sources,
        "segments": segments,
    }


def import_revision_manifest(connection: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, Any]:
    """Append a validated source revision and advance its content-type heads."""
    _require_v29(connection)
    if manifest.get("schema") != SCHEMA:
        raise ControlledContentRevisionImportBlockedError("revision_manifest_schema_invalid")
    sources = manifest.get("sources")
    segments = manifest.get("segments")
    if not isinstance(sources, list) or not isinstance(segments, list) or len(sources) != 2 or len(segments) != 2:
        raise ControlledContentRevisionImportBlockedError("representative_content_revision_manifest_invalid")
    types = {item.get("content_type") for item in segments if isinstance(item, dict)}
    if types != {"knowledge_explanation", "consolidation_practice"}:
        raise ControlledContentRevisionImportBlockedError("content_revision_types_invalid")
    manifest_hash = _hash_bytes(_canonical(manifest).encode("utf-8"))
    existing = connection.execute(
        "SELECT id FROM controlled_content_import_runs WHERE manifest_sha256=?", (manifest_hash,)
    ).fetchone()
    if existing is not None:
        return {
            "status": "reused",
            "run_id": existing[0],
            "manifest_sha256": manifest_hash,
            "sources": 0,
            "segments": 0,
        }
    run_id = f"ccirrev:{manifest_hash[:24]}"
    sources_by_id = {item["id"]: item for item in sources if isinstance(item, dict)}
    if len(sources_by_id) != len(sources):
        raise ControlledContentRevisionImportBlockedError("duplicate_content_revision_source_id")
    for source in sources:
        for key in ("original", "archive", "converted"):
            detail = source.get(key)
            if not isinstance(detail, dict):
                raise ControlledContentRevisionImportBlockedError("content_revision_source_detail_missing")
            path = Path(str(detail.get("path") or ""))
            expected = detail.get("sha256")
            if not path.is_file() or not isinstance(expected, str):
                raise ControlledContentRevisionImportBlockedError("content_revision_source_file_missing")
            try:
                actual = _hash_file(path)
            except OSError as exc:
                raise ControlledContentRevisionImportBlockedError("content_revision_source_file_unreadable") from exc
            if actual.casefold() != expected.casefold():
                raise ControlledContentRevisionImportBlockedError("content_revision_source_file_hash_mismatch")
        if source["original"]["sha256"].casefold() != source["archive"]["sha256"].casefold():
            raise ControlledContentRevisionImportBlockedError("content_revision_original_archive_hash_mismatch")
        _require_readable_docx(Path(source["converted"]["path"]))
    # A higher-level runner may need one atomic unit covering this append-only
    # import and its post-write current-chain checks.  Direct callers retain the
    # original transactional behavior.
    transaction = nullcontext() if connection.in_transaction else connection
    with transaction:
        connection.execute(
            """INSERT INTO controlled_content_import_runs
               (id, manifest_sha256, p1_2b_audit_sha256, importer_id, importer_version, status)
               VALUES (?, ?, ?, ?, ?, 'validated')""",
            (run_id, manifest_hash, manifest["p1_2b_audit_sha256"], IMPORTER_ID, IMPORTER_VERSION),
        )
        for source in sources:
            connection.execute(
                """INSERT INTO controlled_content_sources
                   (id, import_run_id, original_path, original_sha256, archive_path, archive_sha256,
                    converted_path, converted_sha256, converter_fingerprint, fidelity_status,
                    source_profile_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'passed', ?)""",
                (
                    source["id"],
                    run_id,
                    source["original"]["path"],
                    source["original"]["sha256"],
                    source["archive"]["path"],
                    source["archive"]["sha256"],
                    source["converted"]["path"],
                    source["converted"]["sha256"],
                    source["converter_fingerprint"],
                    source["source_profile_sha256"],
                ),
            )
        for segment in segments:
            source = sources_by_id.get(segment.get("source_id"))
            if source is None:
                raise ControlledContentRevisionImportBlockedError("content_revision_segment_source_unknown")
            connection.execute(
                """INSERT INTO controlled_content_segments
                   (id, source_id, segment_id, content_type, layer, textbook_id, curriculum_node_id,
                    allowed_node_ids_json, required_knowledge_json, source_character_range_json,
                    normalized_text_sha256, scope_status, invalidated_at, invalidation_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate_only', NULL, NULL)""",
                (
                    segment["id"],
                    segment["source_id"],
                    segment["segment_id"],
                    segment["content_type"],
                    segment["layer"],
                    segment["textbook_id"],
                    segment["curriculum_node_id"],
                    _canonical(segment["allowed_node_ids"]),
                    _canonical(segment["required_knowledge"]),
                    _canonical(segment["source_character_range"]),
                    segment["normalized_text_sha256"],
                ),
            )
            revision_id = f"ccsr_{segment['revision_hash']}"
            connection.execute(
                """INSERT INTO controlled_content_source_revisions
                   (id, source_id, segment_row_id, content_type, textbook_id, curriculum_node_id,
                    source_segment_id, validation_manifest_sha256, segment_contract_hash, revision_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    revision_id,
                    segment["source_id"],
                    segment["id"],
                    segment["content_type"],
                    segment["textbook_id"],
                    segment["curriculum_node_id"],
                    segment["segment_id"],
                    manifest["p1_2b_audit_sha256"],
                    segment["segment_contract_hash"],
                    segment["revision_hash"],
                ),
            )
            head = connection.execute(
                """SELECT current_revision_id, head_revision
                     FROM controlled_content_source_revision_heads
                    WHERE content_type=? AND textbook_id=? AND curriculum_node_id=?""",
                (segment["content_type"], segment["textbook_id"], segment["curriculum_node_id"]),
            ).fetchone()
            previous = None if head is None else head[0]
            expected_head_revision = 0 if head is None else int(head[1])
            event_payload = {
                "schema": "p1-2f-content-source-revision-head-event-v1",
                "content_type": segment["content_type"],
                "textbook_id": segment["textbook_id"],
                "curriculum_node_id": segment["curriculum_node_id"],
                "previous_revision_id": previous,
                "replacement_revision_id": revision_id,
                "expected_head_revision": expected_head_revision,
                "reason": "p1-2f-revalidated-controlled-content-source-revision",
            }
            event_hash = _hash_bytes(_canonical(event_payload).encode("utf-8"))
            connection.execute(
                """INSERT INTO controlled_content_source_revision_head_events
                   (id, content_type, textbook_id, curriculum_node_id, previous_revision_id,
                    replacement_revision_id, expected_head_revision, reason, event_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"ccsrhe_{event_hash}",
                    segment["content_type"],
                    segment["textbook_id"],
                    segment["curriculum_node_id"],
                    previous,
                    revision_id,
                    expected_head_revision,
                    event_payload["reason"],
                    event_hash,
                ),
            )
    return {
        "status": "validated",
        "run_id": run_id,
        "manifest_sha256": manifest_hash,
        "sources": len(sources),
        "segments": len(segments),
    }
