"""P1-3c: q013-only revised-source mapping approval exercise.

The module approves at most one *textbook mapping*.  It does not promote the
question, create a selection plan, or produce a teaching document.  The
approval boundary is the v2 snapshot introduced by schema v2.28: semantic
inputs are bound and rechecked, while the fit_status output is intentionally
excluded so approval does not invalidate its own evidence.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import sqlite3

from asset_semantics_validator import (
    VALIDATOR_ID as ASSET_VALIDATOR_ID,
    VALIDATOR_VERSION as ASSET_VALIDATOR_VERSION,
    validate as validate_asset_semantics,
)
from automatic_verification_runner import (
    ROOT as PROJECT_ROOT,
    SourceDocumentResolver,
    _canonical_fragment_id,
    _fidelity_coverage,
    _provenance,
    _source_document_row,
    normalize_answer,
)
from candidate_consistency import all_pass as consistency_all_pass
from candidate_consistency import check_candidate_consistency
from fidelity_audit import all_pass as fidelity_all_pass
from fidelity_audit import check_question_fields
from independent_math_validators import validate as validate_math
from input_snapshot import (
    build_approval_input_payload_v2,
    compute_approval_input_hash_v2,
    refresh_approval_snapshot_v2,
    stable_hash,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
DEFAULT_EVIDENCE_DIR = ROOT / "data" / "dev" / "p1-3c-mapping"
DEFAULT_REPORT = ROOT / "output" / "audits" / "p1-3c_q013_mapping_approval.json"

QUESTION_ID = "golden-q013"
TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
NODE_ID = "bsd-math-8x-2026-node-01-section-02"
EXPECTED_KNOWLEDGE = (
    ("kp-bsd8x-isosceles-triangle-properties-v1", "primary"),
    ("kp-triangle-side-inequality-v1", "secondary"),
)
REQUIRED_CONTENT = (
    ("knowledge_explanation", "public_core"),
    ("consolidation_practice", "basic_reinforcement"),
)
REQUIRED_VALIDATORS = (
    "source_fidelity",
    "structural_consistency",
    "mathematical_independent",
    "asset_semantics",
)
DELIVERY_TABLES = (
    "teaching_documents",
    "document_questions",
    "quality_reports",
    "question_usage",
)
MIGRATION = "v2.28-p13c-approval-boundary-and-mapping-revisions"


class P13CBlockedError(RuntimeError):
    """Raised for a fail-closed P1-3c precondition failure."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _hash_matches(actual: str, expected: object) -> bool:
    return isinstance(expected, str) and actual.casefold() == expected.casefold()


def _write_json_if_absent(path: Path, value: dict[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise P13CBlockedError(f"immutable_evidence_artifact_collision:{path.name}")
        return
    path.write_bytes(raw)


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _table_hash(connection: sqlite3.Connection, table: str) -> str:
    cursor = connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
    return _digest_bytes(_canonical_json(_rows(cursor)).encode("utf-8"))


def _protected_snapshot(connection: sqlite3.Connection) -> dict[str, str]:
    tables = (
        "questions",
        "question_textbooks",
        "question_knowledge_points",
        "question_knowledge_point_imports",
        "question_verifications",
        "question_input_snapshots",
        "question_curriculum_mapping_evidence",
        "question_auto_mapping_audit_logs",
        *DELIVERY_TABLES,
    )
    return {table: _table_hash(connection, table) for table in tables}


def _delivery_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in DELIVERY_TABLES
    }


def _require_migration(connection: sqlite3.Connection) -> None:
    present = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)
    ).fetchone()
    if present is None:
        raise P13CBlockedError("p1_3c_schema_v28_not_applied")


def _question(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute(
        """SELECT q.id, q.stem, q.options_json, q.answer, q.analysis, q.question_type,
                  q.content_hash, q.quality_status, q.review_status,
                  q.source_question_no, q.source_document_id,
                  sd.relative_path, sd.file_hash, sd.original_relative_path,
                  sd.original_file_hash, sd.trusted_source, sd.intake_manifest_json
             FROM questions q
             JOIN source_documents sd ON sd.id=q.source_document_id
            WHERE q.id=?""",
        (QUESTION_ID,),
    ).fetchone()
    if row is None:
        raise P13CBlockedError("q013_missing")
    return row


def _target_mapping(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute(
        """SELECT question_id, textbook_id, curriculum_node_id, fit_status
             FROM question_textbooks
            WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
        (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
    ).fetchone()
    if row is None:
        raise P13CBlockedError("q013_target_mapping_missing")
    return row


def _catalog_and_knowledge(connection: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    catalog = connection.execute(
        """SELECT t.id AS textbook_id, t.catalog_version AS textbook_catalog_version,
                  t.status AS textbook_status, n.id AS curriculum_node_id,
                  n.catalog_version AS node_catalog_version, n.status AS node_status,
                  cr.id AS catalog_release_id, cr.source_reference, cr.source_hash,
                  cr.status AS catalog_release_status, ca.id AS catalog_audit_id,
                  ca.audit_manifest_hash AS catalog_audit_manifest_hash,
                  ca.status AS catalog_audit_status
             FROM textbooks t
             JOIN curriculum_nodes n ON n.id=? AND n.textbook_id=t.id
             JOIN catalog_releases cr ON cr.textbook_id=t.id AND cr.catalog_version=t.catalog_version
             JOIN catalog_audits ca ON ca.catalog_release_id=cr.id AND ca.status='approved'
            WHERE t.id=?""",
        (NODE_ID, TEXTBOOK_ID),
    ).fetchone()
    if catalog is None or catalog["textbook_status"] != "active" or catalog["node_status"] != "active" or catalog["catalog_release_status"] != "approved":
        raise P13CBlockedError("target_catalog_or_node_not_current")
    points = _rows(connection.execute(
        """SELECT kp.id AS knowledge_point_id, kp.canonical_name, kp.version,
                  kp.review_status, qkp.relation_type, ckp.relation_type AS curriculum_relation_type
             FROM question_knowledge_points qkp
             JOIN knowledge_points kp ON kp.id=qkp.knowledge_point_id
             JOIN curriculum_knowledge_points ckp
               ON ckp.knowledge_point_id=kp.id AND ckp.curriculum_node_id=?
            WHERE qkp.question_id=?
            ORDER BY kp.id""",
        (NODE_ID, QUESTION_ID),
    ))
    actual = tuple((row["knowledge_point_id"], row["relation_type"]) for row in points)
    if actual != EXPECTED_KNOWLEDGE or any(row["review_status"] != "approved" for row in points):
        raise P13CBlockedError("q013_approved_knowledge_mapping_missing_or_drifted")
    return dict(catalog), points


def _source_binding(question: sqlite3.Row) -> dict[str, Any]:
    try:
        manifest = json.loads(str(question["intake_manifest_json"] or "{}"))
    except json.JSONDecodeError as exc:
        raise P13CBlockedError("q013_source_intake_manifest_invalid") from exc
    if question["trusted_source"] != 1 or manifest.get("hashes_match") is not True:
        raise P13CBlockedError("q013_source_not_trusted_or_hash_binding_missing")
    archive = manifest.get("history_archive")
    if not isinstance(archive, dict):
        raise P13CBlockedError("q013_source_history_archive_missing")
    return {
        "source_document_id": question["source_document_id"],
        "extractor_relative_path": question["relative_path"],
        "extractor_sha256": question["file_hash"],
        "original_relative_path": question["original_relative_path"],
        "original_sha256": question["original_file_hash"],
        "history_archive_relative_path": archive.get("relative_path"),
        "history_archive_sha256": archive.get("sha256"),
    }


def build_controlled_mapping_source_payload(connection: sqlite3.Connection) -> dict[str, Any]:
    """Build the deterministic, source-bound q013 mapping source artifact."""
    question = _question(connection)
    mapping = _target_mapping(connection)
    if mapping["fit_status"] not in {"pending", "approved"}:
        raise P13CBlockedError("q013_target_mapping_status_invalid")
    catalog, knowledge = _catalog_and_knowledge(connection)
    return {
        "schema": "p1-3c-controlled-mapping-source-v1",
        "purpose": "q013-only-source-revision-for-textbook-mapping-approval",
        "question": {
            "id": question["id"],
            "content_hash": question["content_hash"],
            "source_question_no": question["source_question_no"],
            "source_binding": _source_binding(question),
        },
        "target": {
            "textbook_id": mapping["textbook_id"],
            "curriculum_node_id": mapping["curriculum_node_id"],
            "catalog": catalog,
        },
        "knowledge_points": knowledge,
        "decision_features": {
            "task_theme": "等腰三角形",
            "exact_theme_anchor": "等腰三角形",
            "required_knowledge": [row["canonical_name"] for row in knowledge],
            "required_knowledge_evidence": {
                row["canonical_name"]: {
                    "knowledge_point_id": row["knowledge_point_id"],
                    "question_relation": row["relation_type"],
                    "curriculum_relation": row["curriculum_relation_type"],
                    "supported": True,
                }
                for row in knowledge
            },
        },
        "non_approval_boundary": {
            "question_quality_status": question["quality_status"],
            "question_review_status": question["review_status"],
            "mapping_fit_status_is_not_input": True,
        },
    }


def _artifact_paths(payload: dict[str, Any], evidence_dir: Path) -> tuple[Path, Path, str]:
    payload_hash = stable_hash(payload)
    source_path = evidence_dir / f"q013_mapping_source_{payload_hash[:24]}.json"
    return source_path, evidence_dir / f"q013_mapping_manifest_{payload_hash[:24]}.json", payload_hash


def _prepare_source_artifacts(
    connection: sqlite3.Connection, evidence_dir: Path
) -> tuple[Path, Path, dict[str, Any], str, str]:
    payload = build_controlled_mapping_source_payload(connection)
    source_path, manifest_path, payload_hash = _artifact_paths(payload, evidence_dir)
    _write_json_if_absent(source_path, payload)
    source_hash = _digest_file(source_path)
    manifest = {
        "schema": "p1-3c-mapping-source-revision-manifest-v1",
        "source": {
            "reference": f"p1-3c:q013-source-revision:{source_hash}",
            "file": source_path.name,
            "sha256": source_hash,
        },
        "mapping": {
            "question_id": QUESTION_ID,
            "textbook_id": TEXTBOOK_ID,
            "curriculum_node_id": NODE_ID,
            "knowledge_points": [
                {"knowledge_point_id": row["knowledge_point_id"], "relation_type": row["relation_type"]}
                for row in payload["knowledge_points"]
            ],
        },
        "payload_hash": payload_hash,
    }
    _write_json_if_absent(manifest_path, manifest)
    return source_path, manifest_path, payload, source_hash, payload_hash


def _load_artifact(path: Path, schema: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P13CBlockedError(f"invalid_p1_3c_artifact:{path.name}") from exc
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise P13CBlockedError(f"invalid_p1_3c_artifact_schema:{path.name}")
    return value


def _import_or_reuse_source_revision(
    connection: sqlite3.Connection, evidence_dir: Path
) -> dict[str, Any]:
    """Create the first q013 revision or reuse its exact immutable revision."""
    source_path, manifest_path, payload, source_hash, payload_hash = _prepare_source_artifacts(
        connection, evidence_dir
    )
    manifest = _load_artifact(manifest_path, "p1-3c-mapping-source-revision-manifest-v1")
    source = _load_artifact(source_path, "p1-3c-controlled-mapping-source-v1")
    if source != payload or manifest.get("payload_hash") != payload_hash:
        raise P13CBlockedError("mapping_source_artifact_does_not_match_current_controlled_inputs")
    source_spec = manifest.get("source")
    mapping_spec = manifest.get("mapping")
    if not isinstance(source_spec, dict) or not isinstance(mapping_spec, dict):
        raise P13CBlockedError("mapping_source_manifest_invalid")
    if source_spec.get("file") != source_path.name or source_spec.get("sha256") != source_hash:
        raise P13CBlockedError("mapping_source_manifest_hash_mismatch")
    if (mapping_spec.get("question_id"), mapping_spec.get("textbook_id"), mapping_spec.get("curriculum_node_id")) != (
        QUESTION_ID,
        TEXTBOOK_ID,
        NODE_ID,
    ):
        raise P13CBlockedError("mapping_source_manifest_outside_q013_boundary")
    points = mapping_spec.get("knowledge_points")
    if not isinstance(points, list) or tuple(
        (item.get("knowledge_point_id"), item.get("relation_type"))
        for item in points if isinstance(item, dict)
    ) != EXPECTED_KNOWLEDGE:
        raise P13CBlockedError("mapping_source_manifest_knowledge_boundary_invalid")

    mapping_hash = stable_hash(
        {
            "schema": "p1-3c-question-mapping-revision-v1",
            "source_reference": source_spec["reference"],
            "source_hash": source_hash,
            "mapping": mapping_spec,
        }
    )
    manifest_hash = _digest_file(manifest_path)
    import_id = f"p1-3c-question-mapping-import:{mapping_hash[:24]}"
    revision_hash = stable_hash(
        {
            "schema": "p1-3c-question-mapping-source-revision-v1",
            "import_id": import_id,
            "mapping_hash": mapping_hash,
            "source_hash": source_hash,
            "payload_hash": payload_hash,
            "knowledge_points": points,
        }
    )
    revision_id = f"qmsr_{revision_hash}"
    existing = connection.execute(
        "SELECT * FROM question_mapping_source_revisions WHERE revision_hash=?", (revision_hash,)
    ).fetchone()
    if existing is not None:
        return {
            "status": "reused",
            "source_path": str(source_path),
            "manifest_path": str(manifest_path),
            "source_hash": source_hash,
            "manifest_hash": manifest_hash,
            "import_id": existing["import_run_id"],
            "mapping_hash": existing["mapping_hash"],
            "revision_id": existing["id"],
            "revision_hash": existing["revision_hash"],
        }

    target = _target_mapping(connection)
    if target["fit_status"] != "pending":
        raise P13CBlockedError("new_mapping_source_revision_requires_pending_mapping")
    existing_head = connection.execute(
        """SELECT current_revision_id, head_revision FROM question_mapping_source_revision_heads
             WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
        (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
    ).fetchone()
    previous = None if existing_head is None else existing_head["current_revision_id"]
    expected_head_revision = 0 if existing_head is None else int(existing_head["head_revision"])
    event_payload = {
        "schema": "p1-3c-mapping-source-revision-head-event-v1",
        "question_id": QUESTION_ID,
        "textbook_id": TEXTBOOK_ID,
        "curriculum_node_id": NODE_ID,
        "previous_revision_id": previous,
        "replacement_revision_id": revision_id,
        "expected_head_revision": expected_head_revision,
        "reason": "p1-3c-controlled-q013-source-revision",
    }
    event_hash = stable_hash(event_payload)
    connection.execute(
        """INSERT INTO controlled_import_runs
           (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status)
           VALUES (?, 'question_mapping', ?, ?, ?, 'p1-3c-controlled-mapping-source-revision', '1.0.0', 'validated')""",
        (import_id, source_spec["reference"], source_hash, manifest_hash),
    )
    connection.execute(
        """INSERT INTO question_mapping_source_revisions
           (id, question_id, textbook_id, curriculum_node_id, import_run_id, mapping_hash,
            source_reference, source_hash, source_file_path, knowledge_points_json, revision_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            revision_id,
            QUESTION_ID,
            TEXTBOOK_ID,
            NODE_ID,
            import_id,
            mapping_hash,
            source_spec["reference"],
            source_hash,
            source_path.relative_to(ROOT).as_posix(),
            _canonical_json(points),
            revision_hash,
        ),
    )
    for point in points:
        connection.execute(
            """INSERT INTO question_mapping_source_revision_knowledge_points
               (source_revision_id, knowledge_point_id, relation_type)
               VALUES (?, ?, ?)""",
            (revision_id, point["knowledge_point_id"], point["relation_type"]),
        )
    connection.execute(
        """INSERT INTO question_mapping_source_revision_head_events
           (id, question_id, textbook_id, curriculum_node_id, previous_revision_id,
            replacement_revision_id, expected_head_revision, reason, event_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"qmsrhe_{event_hash}",
            QUESTION_ID,
            TEXTBOOK_ID,
            NODE_ID,
            previous,
            revision_id,
            expected_head_revision,
            event_payload["reason"],
            event_hash,
        ),
    )
    return {
        "status": "created",
        "source_path": str(source_path),
        "manifest_path": str(manifest_path),
        "source_hash": source_hash,
        "manifest_hash": manifest_hash,
        "import_id": import_id,
        "mapping_hash": mapping_hash,
        "revision_id": revision_id,
        "revision_hash": revision_hash,
    }


def _current_revision(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT * FROM current_question_mapping_source_revisions
             WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
        (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
    ).fetchone()


def _validate_revision_artifact_against_current_inputs(
    connection: sqlite3.Connection, revision: sqlite3.Row
) -> list[str]:
    blockers: list[str] = []
    source_path = ROOT / str(revision["source_file_path"])
    if not source_path.is_file() or not _hash_matches(_digest_file(source_path), revision["source_hash"]):
        return ["current_mapping_source_revision_file_hash_mismatch"]
    try:
        recorded = _load_artifact(source_path, "p1-3c-controlled-mapping-source-v1")
        current = build_controlled_mapping_source_payload(connection)
    except (P13CBlockedError, ValueError):
        return ["current_mapping_source_revision_inputs_unavailable"]
    if recorded != current:
        blockers.append("current_mapping_source_revision_payload_drifted")
    expected_points = [
        {"knowledge_point_id": point_id, "relation_type": relation_type}
        for point_id, relation_type in EXPECTED_KNOWLEDGE
    ]
    try:
        actual_points = json.loads(str(revision["knowledge_points_json"]))
    except json.JSONDecodeError:
        actual_points = None
    if actual_points != expected_points:
        blockers.append("current_mapping_source_revision_knowledge_contract_drifted")
    return blockers


def _validator_result(
    *,
    validator_id: str,
    validator_version: str,
    status: str,
    input_hash: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "validator_id": validator_id,
        "validator_version": validator_version,
        "status": status,
        "input_hash": input_hash,
        "evidence": evidence,
    }


def _run_prerequisite_validators(
    connection: sqlite3.Connection, approval_input_hash: str
) -> dict[str, dict[str, Any]]:
    """Recompute only the four validators required before mapping approval."""
    question = _question(connection)
    resolver = SourceDocumentResolver(PROJECT_ROOT)
    source_row = _source_document_row(connection, QUESTION_ID)
    source_info = resolver.load(
        question["source_document_id"],
        None if source_row is None else source_row["relative_path"],
        None if source_row is None else source_row["file_type"],
        None if source_row is None else source_row["parse_status"],
        None if source_row is None else source_row["file_hash"],
    )
    if source_info["status"] != "loaded":
        source_result = _validator_result(
            validator_id="automatic_verification_runner",
            validator_version="automatic-runner-v3",
            status="unsupported",
            input_hash=approval_input_hash,
            evidence={"reason": source_info["unsupported_reason"], "source_path": source_info["path"]},
        )
    else:
        provenance = {
            field_name: [_canonical_fragment_id(question["source_document_id"], fragment_id) for fragment_id in fragment_ids]
            for field_name, fragment_ids in _provenance(connection, QUESTION_ID).items()
        }
        findings = check_question_fields(
            stem=question["stem"],
            options_json=question["options_json"],
            answer=question["answer"],
            analysis=question["analysis"],
            question_type=question["question_type"],
            fragments=source_info["fragments"],
            provenance=provenance,
        )
        source_result = _validator_result(
            validator_id="automatic_verification_runner",
            validator_version="automatic-runner-v3",
            status="pass" if fidelity_all_pass(findings) else "fail",
            input_hash=approval_input_hash,
            evidence={
                "coverage": _fidelity_coverage(findings),
                "findings": [asdict(item) for item in findings],
                "source_path": source_info["path"],
            },
        )
    asset_paths = [
        str(PROJECT_ROOT / row[0])
        for row in connection.execute(
            "SELECT relative_path FROM question_assets WHERE question_id=? AND asset_type='image'",
            (QUESTION_ID,),
        )
        if row[0]
    ]
    structural = check_candidate_consistency(
        question_type=question["question_type"],
        options_json=question["options_json"],
        answer=question["answer"],
        asset_paths=asset_paths,
    )
    structural_result = _validator_result(
        validator_id="automatic_verification_runner",
        validator_version="automatic-runner-v3",
        status="pass" if consistency_all_pass(structural) else "fail",
        input_hash=approval_input_hash,
        evidence={"findings": [item.__dict__ for item in structural]},
    )
    math = validate_math(
        {
            "source_question_no": question["source_question_no"],
            "stem": question["stem"],
            "options": json.loads(question["options_json"]),
        }
    )
    if math.status == "pass":
        matches = normalize_answer(math.computed_answer) == normalize_answer(question["answer"])
        math_status = "pass" if matches else "fail"
        math_evidence = {
            "computed_answer": math.computed_answer,
            "stored_answer": question["answer"],
            "normalized_match": matches,
            "validator_evidence": math.evidence,
        }
    else:
        math_status = math.status
        math_evidence = {"reason": math.evidence}
    math_result = _validator_result(
        validator_id="automatic_verification_runner",
        validator_version="automatic-runner-v3",
        status=math_status,
        input_hash=approval_input_hash,
        evidence=math_evidence,
    )
    asset_status, asset_evidence = validate_asset_semantics(connection, QUESTION_ID)
    asset_result = _validator_result(
        validator_id=ASSET_VALIDATOR_ID,
        validator_version=ASSET_VALIDATOR_VERSION,
        status=asset_status,
        input_hash=approval_input_hash,
        evidence=asset_evidence,
    )
    return {
        "source_fidelity": source_result,
        "structural_consistency": structural_result,
        "mathematical_independent": math_result,
        "asset_semantics": asset_result,
    }


def _current_content_blockers(connection: sqlite3.Connection) -> list[str]:
    rows = _rows(connection.execute(
        """SELECT content_type, layer, allowed_node_ids_json, required_knowledge_json
             FROM current_controlled_content_segments
            WHERE textbook_id=? AND curriculum_node_id=?
            ORDER BY content_type, layer""",
        (TEXTBOOK_ID, NODE_ID),
    ))
    if {(row["content_type"], row["layer"]) for row in rows} != set(REQUIRED_CONTENT):
        return ["required_current_controlled_content_missing_or_drifted"]
    for row in rows:
        try:
            allowed = json.loads(str(row["allowed_node_ids_json"]))
            required = json.loads(str(row["required_knowledge_json"]))
        except json.JSONDecodeError:
            return ["controlled_content_contract_invalid"]
        if NODE_ID not in allowed:
            return ["progress_boundary_no_longer_allows_target_node"]
        if "等腰三角形" not in required:
            return ["controlled_content_required_knowledge_drifted"]
    return []


def _record_evidence_and_auto_audit(
    connection: sqlite3.Connection, revision: sqlite3.Row
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    approval_hash = refresh_approval_snapshot_v2(connection, QUESTION_ID)
    snapshot = connection.execute(
        """SELECT input_hash, revision, invalidated_at
             FROM question_approval_input_snapshots_v2 WHERE question_id=?""",
        (QUESTION_ID,),
    ).fetchone()
    if snapshot is None or snapshot["invalidated_at"] is not None or snapshot["input_hash"] != approval_hash:
        raise P13CBlockedError("approval_snapshot_v2_not_current_after_refresh")
    payload = build_controlled_mapping_source_payload(connection)
    features = payload["decision_features"]
    feature_hash = stable_hash(features)
    evidence_payload = {
        "schema": "question-mapping-approval-evidence-v2",
        "question_id": QUESTION_ID,
        "textbook_id": TEXTBOOK_ID,
        "curriculum_node_id": NODE_ID,
        "source_revision_id": revision["id"],
        "approval_input_hash": approval_hash,
        "approval_snapshot_revision": snapshot["revision"],
        "feature_hash": feature_hash,
        "features": features,
        "decider_id": "p1-3c-q013-deterministic-mapping",
        "decider_version": "1.0.0",
        "status": "candidate",
        "confidence": 1.0,
        "reason": "当前可信来源、映射来源修订、已批准知识点、目录、受控内容和进度边界均已绑定；仅用于教材映射批准。",
    }
    evidence_hash = stable_hash(evidence_payload)
    evidence_id = f"qmae_v2_{evidence_hash}"
    evidence = connection.execute(
        "SELECT * FROM question_mapping_approval_evidence_v2 WHERE evidence_hash=?", (evidence_hash,)
    ).fetchone()
    if evidence is None:
        connection.execute(
            """INSERT INTO question_mapping_approval_evidence_v2
               (id, question_id, textbook_id, curriculum_node_id, source_revision_id,
                approval_input_hash, approval_snapshot_revision, feature_hash, features_json,
                decider_id, decider_version, status, confidence, reason, evidence_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence_id,
                QUESTION_ID,
                TEXTBOOK_ID,
                NODE_ID,
                revision["id"],
                approval_hash,
                snapshot["revision"],
                feature_hash,
                _canonical_json(features),
                evidence_payload["decider_id"],
                evidence_payload["decider_version"],
                "candidate",
                1.0,
                evidence_payload["reason"],
                evidence_hash,
            ),
        )
        evidence = connection.execute(
            "SELECT * FROM question_mapping_approval_evidence_v2 WHERE id=?", (evidence_id,)
        ).fetchone()
    validators = _run_prerequisite_validators(connection, approval_hash)
    failures = [key for key in REQUIRED_VALIDATORS if validators[key]["status"] != "pass"]
    content_blockers = _current_content_blockers(connection)
    audit_status = "pass" if not failures and not content_blockers else "unsupported"
    findings = {
        "validator_failures": failures,
        "content_blockers": content_blockers,
        "source_revision_id": revision["id"],
        "approval_input_hash": approval_hash,
    }
    auto_payload = {
        "schema": "question-mapping-auto-audit-v2",
        "evidence_id": evidence["id"],
        "source_revision_id": revision["id"],
        "approval_input_hash": approval_hash,
        "approval_snapshot_revision": snapshot["revision"],
        "validator_results": validators,
        "audit_status": audit_status,
        "findings": findings,
    }
    audit_hash = stable_hash(auto_payload)
    audit_id = f"qmaa_v2_{audit_hash}"
    auto_audit = connection.execute(
        "SELECT * FROM question_mapping_auto_audits_v2 WHERE audit_hash=?", (audit_hash,)
    ).fetchone()
    if auto_audit is None:
        connection.execute(
            """INSERT INTO question_mapping_auto_audits_v2
               (id, evidence_id, question_id, textbook_id, curriculum_node_id, source_revision_id,
                approval_input_hash, approval_snapshot_revision, validation_input_hash,
                validator_results_json, audit_status, findings_json, audit_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                audit_id,
                evidence["id"],
                QUESTION_ID,
                TEXTBOOK_ID,
                NODE_ID,
                revision["id"],
                approval_hash,
                snapshot["revision"],
                approval_hash,
                _canonical_json(validators),
                audit_status,
                _canonical_json(findings),
                audit_hash,
            ),
        )
        auto_audit = connection.execute(
            "SELECT * FROM question_mapping_auto_audits_v2 WHERE id=?", (audit_id,)
        ).fetchone()
    return dict(evidence), dict(auto_audit), failures + content_blockers


def _record_approval_and_promote(
    connection: sqlite3.Connection,
    revision: sqlite3.Row,
    evidence: dict[str, Any],
    auto_audit: dict[str, Any],
    evidence_dir: Path,
) -> dict[str, Any]:
    findings = [
        {"check": "source-revision", "status": "pass", "evidence": revision["revision_hash"]},
        {"check": "v2-approval-snapshot", "status": "pass", "evidence": evidence["approval_input_hash"]},
        {"check": "four-prerequisite-validators", "status": "pass", "evidence": auto_audit["audit_hash"]},
        {"check": "approval-boundary", "status": "pass", "evidence": "q013 remains blocked/pending; delivery tables empty"},
    ]
    manifest = {
        "schema": "p1-3c-mapping-approval-audit-manifest-v1",
        "source_revision_id": revision["id"],
        "evidence_id": evidence["id"],
        "auto_audit_id": auto_audit["id"],
        "approval_input_hash": evidence["approval_input_hash"],
        "approval_snapshot_revision": evidence["approval_snapshot_revision"],
        "auditor_id": "p1-3c-q013-mapping-approval-audit",
        "audit_method": "source-revision-bound-approval-v2",
        "findings": findings,
    }
    manifest_key = stable_hash(manifest)
    manifest_path = evidence_dir / f"q013_mapping_approval_audit_{manifest_key[:24]}.json"
    _write_json_if_absent(manifest_path, manifest)
    manifest_hash = _digest_file(manifest_path)
    approval_id = f"qmaaudit_v2_{manifest_hash}"
    existing = connection.execute(
        "SELECT * FROM question_mapping_approval_audits_v2 WHERE source_revision_id=?",
        (revision["id"],),
    ).fetchone()
    if existing is not None:
        return dict(existing)
    question = _question(connection)
    if (question["quality_status"], question["review_status"]) != ("blocked", "pending"):
        raise P13CBlockedError("q013_question_status_not_blocked_pending")
    if any(_delivery_counts(connection).values()):
        raise P13CBlockedError("delivery_related_tables_not_empty")
    connection.execute(
        """INSERT INTO question_mapping_approval_audits_v2
           (id, source_revision_id, evidence_id, auto_audit_id, approval_input_hash,
            approval_snapshot_revision, audit_manifest_hash, auditor_id, audit_method,
            status, findings_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)""",
        (
            approval_id,
            revision["id"],
            evidence["id"],
            auto_audit["id"],
            evidence["approval_input_hash"],
            evidence["approval_snapshot_revision"],
            manifest_hash,
            manifest["auditor_id"],
            manifest["audit_method"],
            _canonical_json(findings),
        ),
    )
    connection.execute(
        "UPDATE controlled_import_runs SET status='approved' WHERE id=?",
        (revision["import_run_id"],),
    )
    connection.execute(
        """UPDATE question_textbooks SET fit_status='approved'
             WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
        (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
    )
    return dict(connection.execute(
        "SELECT * FROM question_mapping_approval_audits_v2 WHERE id=?", (approval_id,)
    ).fetchone())


def _current_v2_blockers(connection: sqlite3.Connection) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    mapping = _target_mapping(connection)
    question = _question(connection)
    revision = _current_revision(connection)
    if revision is None:
        blockers.append("current_mapping_source_revision_missing")
    else:
        blockers.extend(_validate_revision_artifact_against_current_inputs(connection, revision))
    if (question["quality_status"], question["review_status"]) != ("blocked", "pending"):
        blockers.append("question_status_not_blocked_pending")
    if any(_delivery_counts(connection).values()):
        blockers.append("delivery_related_tables_not_empty")
    blockers.extend(_current_content_blockers(connection))
    snapshot = connection.execute(
        "SELECT * FROM question_approval_input_snapshots_v2 WHERE question_id=?", (QUESTION_ID,)
    ).fetchone()
    try:
        computed_hash = compute_approval_input_hash_v2(connection, QUESTION_ID)
    except (ValueError, sqlite3.Error) as exc:
        computed_hash = None
        blockers.append(f"approval_input_recompute_failed:{exc}")
    if snapshot is None or snapshot["invalidated_at"] is not None:
        blockers.append("current_approval_snapshot_v2_missing_or_invalidated")
    elif computed_hash != snapshot["input_hash"]:
        blockers.append("current_approval_snapshot_v2_stale")
    evidence = None
    auto_audit = None
    approval_audit = None
    if revision is not None and snapshot is not None and computed_hash == snapshot["input_hash"]:
        evidence = connection.execute(
            """SELECT * FROM current_question_mapping_approval_evidence_v2
                 WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?
                   AND source_revision_id=?""",
            (QUESTION_ID, TEXTBOOK_ID, NODE_ID, revision["id"]),
        ).fetchone()
        if evidence is None:
            blockers.append("current_mapping_approval_evidence_v2_missing")
        auto_audit = connection.execute(
            """SELECT * FROM current_question_mapping_auto_audits_v2
                 WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?
                   AND source_revision_id=?""",
            (QUESTION_ID, TEXTBOOK_ID, NODE_ID, revision["id"]),
        ).fetchone()
        if auto_audit is None:
            blockers.append("current_mapping_auto_audit_v2_pass_missing")
        approval_audit = connection.execute(
            """SELECT a.* FROM question_mapping_approval_audits_v2 a
                 JOIN current_question_mapping_approval_evidence_v2 e ON e.id=a.evidence_id
                 JOIN current_question_mapping_auto_audits_v2 aa ON aa.id=a.auto_audit_id
                WHERE a.source_revision_id=? AND a.status='approved'""",
            (revision["id"],),
        ).fetchone()
        if mapping["fit_status"] == "approved" and approval_audit is None:
            blockers.append("current_mapping_approval_audit_v2_missing")
    return {
        "mapping": dict(mapping),
        "question": dict(question),
        "revision": dict(revision) if revision else None,
        "snapshot": dict(snapshot) if snapshot else None,
        "computed_approval_input_hash": computed_hash,
        "evidence": dict(evidence) if evidence else None,
        "auto_audit": dict(auto_audit) if auto_audit else None,
        "approval_audit": dict(approval_audit) if approval_audit else None,
        "delivery_counts": _delivery_counts(connection),
    }, sorted(set(blockers))


def run_p1_3c(
    database: str | Path = DEFAULT_DATABASE,
    *,
    evidence_dir: str | Path = DEFAULT_EVIDENCE_DIR,
    report_path: str | Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    """Run the only authorized P1-3c q013 mapping approval exercise."""
    database = Path(database).resolve()
    evidence_dir = Path(evidence_dir).resolve()
    report_path = Path(report_path).resolve()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    before_protected: dict[str, str] | None = None
    try:
        _require_migration(connection)
        before_protected = _protected_snapshot(connection)
        initial_mapping = _target_mapping(connection)
        action = "blocked_zero_mapping_approval"
        revision_result: dict[str, Any] | None = None
        if initial_mapping["fit_status"] == "approved":
            before_bundle, before_blockers = _current_v2_blockers(connection)
            if before_blockers:
                with connection:
                    connection.execute(
                        """UPDATE question_textbooks SET fit_status='pending'
                             WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
                        (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
                    )
                action = "revoked_mapping_to_pending"
            else:
                action = "reused_current_approved_mapping"
        else:
            with connection:
                revision_result = _import_or_reuse_source_revision(connection, evidence_dir)
                revision = _current_revision(connection)
                if revision is None:
                    raise P13CBlockedError("source_revision_not_current_after_import")
                source_blockers = _validate_revision_artifact_against_current_inputs(connection, revision)
                if source_blockers:
                    raise P13CBlockedError(";".join(source_blockers))
                evidence, auto_audit, audit_blockers = _record_evidence_and_auto_audit(connection, revision)
                if audit_blockers or auto_audit["audit_status"] != "pass":
                    raise P13CBlockedError(
                        ";".join(audit_blockers or ["mapping_auto_audit_v2_not_pass"])
                    )
                _record_approval_and_promote(connection, revision, evidence, auto_audit, evidence_dir)
                _, post_blockers = _current_v2_blockers(connection)
                if post_blockers:
                    raise P13CBlockedError(
                        "post_approval:" + ";post_approval:".join(post_blockers)
                    )
            action = "approved_one_mapping"
            before_blockers = []
        final_bundle, final_blockers = _current_v2_blockers(connection)
        after_protected = _protected_snapshot(connection)
        report = {
            "schema": "p1-3c-q013-revised-mapping-approval-v1",
            "database": str(database),
            "action": action,
            "approved_mapping_count": 1 if action in {"approved_one_mapping", "reused_current_approved_mapping"} else 0,
            "revision_import": revision_result,
            "blockers_before_action": sorted(set(before_blockers)),
            "blockers_after_action": final_blockers,
            "final": final_bundle,
            "question_approved": False,
            "delivery_tables_empty": not any(final_bundle["delivery_counts"].values()),
            "protected_tables_unchanged_except_target_mapping_and_legacy_snapshot": all(
                before_protected[key] == after_protected[key]
                for key in before_protected
                if key not in {"question_textbooks", "question_input_snapshots"}
            ),
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        return report
    except (P13CBlockedError, ValueError, sqlite3.Error, OSError) as exc:
        if connection.in_transaction:
            connection.rollback()
        mapping = connection.execute(
            """SELECT fit_status FROM question_textbooks
                 WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
            (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
        ).fetchone()
        question = connection.execute(
            "SELECT quality_status, review_status FROM questions WHERE id=?", (QUESTION_ID,)
        ).fetchone()
        report = {
            "schema": "p1-3c-q013-revised-mapping-approval-v1",
            "database": str(database),
            "action": "blocked_zero_mapping_approval",
            "approved_mapping_count": 0,
            "blockers_before_action": [str(exc)],
            "fit_status": None if mapping is None else mapping["fit_status"],
            "question_status": None if question is None else list(question),
            "question_approved": False,
            "delivery_counts": _delivery_counts(connection),
            "database_changes_committed": False if before_protected is not None else None,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        return report
    finally:
        connection.close()
