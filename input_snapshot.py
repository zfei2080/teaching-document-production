"""Canonical, fail-closed input snapshots for automatic question admission."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent

def stable_hash(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _rows(conn: sqlite3.Connection, sql: str, values: tuple[object, ...]) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, values)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def build_input_payload(conn: sqlite3.Connection, question_id: str) -> dict[str, object]:
    """Return every database-held input that can affect admission evidence.

    The snapshot is intentionally database-derived and canonical.  It does not
    trust a caller-supplied payload or verifier evidence.
    """
    question_rows = _rows(
        conn,
        """SELECT id, stem, options_json, answer, analysis, question_type, difficulty,
                  stage, grade_level, source_document_id, source_fragment_id,
                  source_question_no, source_page, content_hash, extraction_status
           FROM questions WHERE id=?""",
        (question_id,),
    )
    if not question_rows:
        raise ValueError(f"question does not exist: {question_id}")
    question = question_rows[0]
    source_documents = _rows(
        conn,
        """SELECT sd.id, sd.relative_path, sd.file_hash, sd.file_type, sd.source_label,
                  sd.copyright_status, sd.parse_status
           FROM source_documents sd JOIN questions q ON q.source_document_id=sd.id
           WHERE q.id=?""",
        (question_id,),
    )
    provenance = _rows(
        conn,
        """SELECT qsf.field_name, qsf.source_fragment_id, qsf.source_hash,
                  sf.source_document_id, sf.location_type, sf.page_number,
                  sf.paragraph_index, sf.question_number, sf.raw_hash
           FROM question_source_fragments qsf
           JOIN source_fragments sf ON sf.id=qsf.source_fragment_id
           WHERE qsf.question_id=?
           ORDER BY qsf.field_name, qsf.source_fragment_id""",
        (question_id,),
    )
    assets = _rows(
        conn,
        """SELECT qa.id, qa.asset_type, qa.relative_path, qa.source_fragment_id,
                  qa.position, qa.checksum, qa.status, sf.raw_hash AS source_fragment_hash
           FROM question_assets qa
           LEFT JOIN source_fragments sf ON sf.id=qa.source_fragment_id
           WHERE qa.question_id=? ORDER BY qa.id""",
        (question_id,),
    )
    textbook_mappings = _rows(
        conn,
        """SELECT qt.textbook_id, qt.curriculum_node_id, qt.fit_status,
                  t.name AS textbook_name, t.subject AS textbook_subject,
                  t.catalog_version AS textbook_catalog_version, t.status AS textbook_status,
                  n.parent_id AS node_parent_id, n.stage AS node_stage,
                  n.grade_level AS node_grade_level, n.node_type, n.name AS node_name,
                  n.sequence AS node_sequence, n.catalog_version AS node_catalog_version,
                  n.status AS node_status
           FROM question_textbooks qt
           JOIN textbooks t ON t.id=qt.textbook_id
           LEFT JOIN curriculum_nodes n ON n.id=qt.curriculum_node_id
           WHERE qt.question_id=? ORDER BY qt.textbook_id, qt.curriculum_node_id""",
        (question_id,),
    )
    knowledge_mappings = _rows(
        conn,
        """SELECT qkp.knowledge_point_id, qkp.relation_type, kp.canonical_name,
                  kp.knowledge_type, kp.stage_scope, kp.definition_text, kp.formulas_json,
                  kp.properties_json, kp.conditions_json, kp.common_errors_json,
                  kp.version, kp.review_status
           FROM question_knowledge_points qkp
           JOIN knowledge_points kp ON kp.id=qkp.knowledge_point_id
           WHERE qkp.question_id=? ORDER BY qkp.knowledge_point_id""",
        (question_id,),
    )
    return {
        "snapshot_schema": "question-admission-input-v1",
        "question": question,
        "source_documents": source_documents,
        "source_provenance": provenance,
        "assets": assets,
        "textbook_mappings": textbook_mappings,
        "knowledge_mappings": knowledge_mappings,
    }


def compute_current_input_hash(conn: sqlite3.Connection, question_id: str) -> str:
    return stable_hash(build_input_payload(conn, question_id))


def refresh_current_snapshot(conn: sqlite3.Connection, question_id: str) -> str:
    """Persist the recomputed current hash after an invalidation or first import."""
    input_hash = compute_current_input_hash(conn, question_id)
    row = conn.execute(
        "SELECT question_id FROM question_input_snapshots WHERE question_id=?", (question_id,)
    ).fetchone()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if row is None:
        conn.execute(
            "INSERT INTO question_input_snapshots (question_id, input_hash, revision, calculated_at) VALUES (?, ?, 1, ?)",
            (question_id, input_hash, now),
        )
    else:
        conn.execute(
            """UPDATE question_input_snapshots
               SET input_hash=?, calculated_at=?, invalidated_at=NULL, invalidation_reason=NULL
               WHERE question_id=?""",
            (input_hash, now, question_id),
        )
    return input_hash


def _sha256_file(path: Path, label: str) -> str:
    if not path.is_file():
        raise ValueError(f"{label}_missing:{path}")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"{label}_unreadable:{path}") from exc


def _hash_matches(actual: str, expected: object) -> bool:
    return isinstance(expected, str) and actual.casefold() == expected.casefold()


def _require_readable_docx(path: Path, label: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"{label}_not_a_readable_docx:{path}") from exc
    required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
    if not required.issubset(names):
        raise ValueError(f"{label}_docx_parts_missing:{path}")


def _workspace_path(value: str, label: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"{label}_outside_workspace:{resolved}") from exc
    return resolved


def _approval_source_documents_v2(
    conn: sqlite3.Connection, question_id: str
) -> list[dict[str, Any]]:
    """Bind v2 approval evidence to the actual trusted source bytes.

    The v1 admission snapshot records the declared source hash.  P1-3c also
    hashes the on-disk trusted document so a filesystem-only change cannot keep
    a mapping approval current until some unrelated database write occurs.
    """
    result = _rows(
        conn,
        """SELECT sd.id, sd.relative_path, sd.file_hash, sd.file_type, sd.source_label,
                  sd.copyright_status, sd.parse_status, sd.original_relative_path,
                  sd.original_file_hash, sd.trusted_source, sd.intake_manifest_json
             FROM source_documents sd
             JOIN questions q ON q.source_document_id=sd.id
            WHERE q.id=?""",
        (question_id,),
    )
    for source in result:
        if source.get("trusted_source") != 1:
            raise ValueError("question_source_not_trusted")
        try:
            manifest = json.loads(str(source["intake_manifest_json"] or "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("question_source_intake_manifest_invalid") from exc
        if manifest.get("hashes_match") is not True:
            raise ValueError("question_source_intake_hashes_not_matched")
        extractor_path = _workspace_path(str(source["relative_path"]), "question_source")
        actual_hash = _sha256_file(extractor_path, "question_source")
        expected_hash = source.get("file_hash")
        if not _hash_matches(actual_hash, expected_hash):
            raise ValueError("question_source_file_hash_mismatch")
        original_path = _workspace_path(str(source["original_relative_path"]), "question_original_source")
        if not _hash_matches(
            _sha256_file(original_path, "question_original_source"), source.get("original_file_hash")
        ):
            raise ValueError("question_original_source_file_hash_mismatch")
        history_archive = manifest.get("history_archive")
        if not isinstance(history_archive, dict):
            raise ValueError("question_source_history_archive_missing")
        archive_path = _workspace_path(str(history_archive.get("relative_path") or ""), "question_source_archive")
        archive_hash = _sha256_file(archive_path, "question_source_archive")
        if not _hash_matches(archive_hash, history_archive.get("sha256")) or not _hash_matches(
            archive_hash, expected_hash
        ):
            raise ValueError("question_source_archive_file_hash_mismatch")
        source["actual_file_sha256"] = actual_hash
        source["actual_original_file_sha256"] = _sha256_file(original_path, "question_original_source")
        source["actual_archive_file_sha256"] = archive_hash
    return result


def _approval_mapping_revisions_v2(
    conn: sqlite3.Connection, question_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _rows(
        conn,
        """SELECT r.id, r.question_id, r.textbook_id, r.curriculum_node_id,
                  r.import_run_id, r.mapping_hash, r.source_reference, r.source_hash,
                  r.source_file_path, r.knowledge_points_json, r.revision_hash,
                  r.head_revision, cir.manifest_hash
             FROM current_question_mapping_source_revisions r
             JOIN controlled_import_runs cir ON cir.id=r.import_run_id
            WHERE r.question_id=?
            ORDER BY r.textbook_id, r.curriculum_node_id""",
        (question_id,),
    )
    revision_points = _rows(
        conn,
        """SELECT rkp.source_revision_id, rkp.knowledge_point_id, rkp.relation_type,
                  kp.canonical_name, kp.knowledge_type, kp.stage_scope, kp.version,
                  kp.review_status, ckp.relation_type AS curriculum_relation_type
             FROM question_mapping_source_revision_knowledge_points rkp
             JOIN current_question_mapping_source_revisions r ON r.id=rkp.source_revision_id
             JOIN knowledge_points kp ON kp.id=rkp.knowledge_point_id
             JOIN curriculum_knowledge_points ckp
               ON ckp.knowledge_point_id=kp.id AND ckp.curriculum_node_id=r.curriculum_node_id
            WHERE r.question_id=?
            ORDER BY rkp.source_revision_id, rkp.knowledge_point_id""",
        (question_id,),
    )
    for row in rows:
        actual_hash = _sha256_file(
            _workspace_path(str(row["source_file_path"]), "mapping_source_revision"),
            "mapping_source_revision",
        )
        if not _hash_matches(actual_hash, row["source_hash"]):
            raise ValueError("mapping_source_revision_file_hash_mismatch")
        try:
            listed_points = json.loads(str(row["knowledge_points_json"]))
        except json.JSONDecodeError as exc:
            raise ValueError("mapping_source_revision_knowledge_contract_invalid") from exc
        if not isinstance(listed_points, list) or not listed_points:
            raise ValueError("mapping_source_revision_knowledge_contract_invalid")
        row["actual_source_file_sha256"] = actual_hash
    return rows, revision_points


def _approval_controlled_content_v2(
    conn: sqlite3.Connection, question_id: str
) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT s.id, s.content_type, s.layer, s.textbook_id, s.curriculum_node_id,
                  s.allowed_node_ids_json, s.required_knowledge_json,
                  s.source_character_range_json, s.normalized_text_sha256, s.scope_status,
                  src.id AS source_id, src.original_path, src.original_sha256,
                  src.archive_path, src.archive_sha256, src.converted_path,
                  src.converted_sha256, src.converter_fingerprint, src.fidelity_status,
                  src.source_profile_sha256, run.id AS content_import_run_id,
                  run.manifest_sha256, run.p1_2b_audit_sha256
             FROM current_controlled_content_segments s
             JOIN controlled_content_sources src ON src.id=s.source_id
             JOIN controlled_content_import_runs run ON run.id=src.import_run_id
             JOIN question_textbooks qt
               ON qt.textbook_id=s.textbook_id AND qt.curriculum_node_id=s.curriculum_node_id
            WHERE qt.question_id=?
            ORDER BY s.textbook_id, s.curriculum_node_id, s.content_type, s.layer, s.id""",
        (question_id,),
    )
    for row in rows:
        for field, expected_field in (
            ("original_path", "original_sha256"),
            ("archive_path", "archive_sha256"),
            ("converted_path", "converted_sha256"),
        ):
            actual_hash = _sha256_file(
                _workspace_path(str(row[field]), f"controlled_content_{field}"),
                f"controlled_content_{field}",
            )
            if not _hash_matches(actual_hash, row[expected_field]):
                raise ValueError(f"controlled_content_{field}_hash_mismatch")
            row[f"actual_{field}_sha256"] = actual_hash
        _require_readable_docx(
            _workspace_path(str(row["converted_path"]), "controlled_content_converted_path"),
            "controlled_content_converted_path",
        )
    return rows


def build_approval_input_payload_v2(conn: sqlite3.Connection, question_id: str) -> dict[str, object]:
    """Return the P1-3c approval-boundary input payload.

    The mapping's final ``fit_status`` is deliberately absent.  It is an output
    of approval, not evidence that can be used to decide the approval.  All
    mapping identity, source-revision, trusted-file, knowledge, catalog and
    controlled-content facts remain in the payload.
    """
    payload = build_input_payload(conn, question_id)
    textbook_mappings = payload["textbook_mappings"]
    if not isinstance(textbook_mappings, list) or not textbook_mappings:
        raise ValueError("approval_input_requires_textbook_mapping")
    payload["source_documents"] = _approval_source_documents_v2(conn, question_id)
    payload["textbook_mappings"] = [
        {key: value for key, value in row.items() if key != "fit_status"}
        for row in textbook_mappings
    ]
    revisions, revision_points = _approval_mapping_revisions_v2(conn, question_id)
    if not revisions:
        raise ValueError("approval_input_requires_current_mapping_source_revision")
    content = _approval_controlled_content_v2(conn, question_id)
    if not content:
        raise ValueError("approval_input_requires_current_controlled_content")
    return {
        "snapshot_schema": "question-approval-input-v2",
        "question": payload["question"],
        "source_documents": payload["source_documents"],
        "source_provenance": payload["source_provenance"],
        "assets": payload["assets"],
        "textbook_mappings": payload["textbook_mappings"],
        "knowledge_mappings": payload["knowledge_mappings"],
        "mapping_source_revisions": revisions,
        "mapping_source_revision_knowledge_points": revision_points,
        "controlled_content": content,
    }


def compute_approval_input_hash_v2(conn: sqlite3.Connection, question_id: str) -> str:
    return stable_hash(build_approval_input_payload_v2(conn, question_id))


def refresh_approval_snapshot_v2(conn: sqlite3.Connection, question_id: str) -> str:
    """Persist a fresh P1-3c approval-boundary snapshot for one question."""
    input_hash = compute_approval_input_hash_v2(conn, question_id)
    row = conn.execute(
        "SELECT question_id FROM question_approval_input_snapshots_v2 WHERE question_id=?",
        (question_id,),
    ).fetchone()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if row is None:
        conn.execute(
            """INSERT INTO question_approval_input_snapshots_v2
               (question_id, input_hash, revision, calculated_at)
               VALUES (?, ?, 1, ?)""",
            (question_id, input_hash, now),
        )
    else:
        conn.execute(
            """UPDATE question_approval_input_snapshots_v2
               SET input_hash=?, calculated_at=?, invalidated_at=NULL, invalidation_reason=NULL
               WHERE question_id=?""",
            (input_hash, now, question_id),
        )
    return input_hash
