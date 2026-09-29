"""P1-3b fail-closed approval of the single q013 textbook mapping.

This module deliberately approves a *mapping*, not a question.  It captures the
current evidence bundle required by the handoff contract, writes the normal
source-bound mapping audit only after every check passes, and revokes the one
mapping back to ``pending`` if a later check detects drift.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import sqlite3

from input_snapshot import compute_current_input_hash
from question_mapping_audit import record_question_mapping_audit

ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
DEFAULT_SOURCE = ROOT / "data" / "dev" / "p1-3a-knowledge" / "q013_mapping_source.json"
DEFAULT_REPORT = ROOT / "output" / "audits" / "p1-3b_q013_mapping_approval.json"

QUESTION_ID = "golden-q013"
TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
NODE_ID = "bsd-math-8x-2026-node-01-section-02"
EXPECTED_KNOWLEDGE_POINT_IDS = (
    "kp-bsd8x-isosceles-triangle-properties-v1",
    "kp-triangle-side-inequality-v1",
)
REQUIRED_CONTENT = (
    ("knowledge_explanation", "public_core"),
    ("consolidation_practice", "basic_reinforcement"),
)
DELIVERY_TABLES = (
    "teaching_documents",
    "document_questions",
    "quality_reports",
    "question_usage",
)


class P13BBlockedError(RuntimeError):
    """Raised only for an unrecoverable P1-3b runner failure."""


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _protected_snapshot(connection: sqlite3.Connection) -> dict[str, str]:
    """Hash every table that P1-3b must not change."""
    tables = (
        "questions",
        "question_knowledge_points",
        "question_knowledge_point_imports",
        "question_verifications",
        "question_input_snapshots",
        "question_curriculum_mapping_evidence",
        "question_auto_mapping_audit_logs",
        *DELIVERY_TABLES,
    )
    result: dict[str, str] = {}
    for table in tables:
        cursor = connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        result[table] = sha256(_canonical_json(_rows(cursor)).encode("utf-8")).hexdigest()
    return result


def _delivery_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {table: _table_count(connection, table) for table in DELIVERY_TABLES}


def _mapping_row(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT qti.import_run_id, qti.mapping_hash, cir.source_reference,
                  cir.source_hash, cir.status AS import_status, qt.fit_status
             FROM question_textbook_imports qti
             JOIN controlled_import_runs cir ON cir.id=qti.import_run_id
             JOIN question_textbooks qt
               ON qt.question_id=qti.question_id
              AND qt.textbook_id=qti.textbook_id
              AND qt.curriculum_node_id=qti.curriculum_node_id
            WHERE qti.question_id=? AND qti.textbook_id=? AND qti.curriculum_node_id=?""",
        (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
    ).fetchone()


def _mapping_batch_is_exact(connection: sqlite3.Connection, mapping: sqlite3.Row) -> bool:
    rows = connection.execute(
        """SELECT question_id, textbook_id, curriculum_node_id
             FROM question_textbook_imports
            WHERE import_run_id=? AND mapping_hash=?
            ORDER BY question_id, textbook_id, curriculum_node_id""",
        (mapping["import_run_id"], mapping["mapping_hash"]),
    ).fetchall()
    return [tuple(row) for row in rows] == [(QUESTION_ID, TEXTBOOK_ID, NODE_ID)]


def _current_bundle(connection: sqlite3.Connection, mapping_source: Path) -> tuple[dict[str, Any], list[str]]:
    """Build an audit-ready current bundle and all fail-closed blockers."""
    blockers: list[str] = []
    mapping = _mapping_row(connection)
    if mapping is None:
        return {"mapping": None}, ["target_mapping_missing"]

    if not mapping_source.is_file():
        blockers.append("mapping_source_file_missing")
    elif _digest(mapping_source) != mapping["source_hash"]:
        blockers.append("mapping_source_hash_mismatch")

    if not _mapping_batch_is_exact(connection, mapping):
        blockers.append("mapping_import_batch_not_exactly_q013_target")
    if mapping["fit_status"] not in {"pending", "approved"}:
        blockers.append("target_mapping_status_invalid")
    if mapping["fit_status"] == "pending" and mapping["import_status"] != "validated":
        blockers.append("pending_mapping_import_not_validated")
    if mapping["fit_status"] == "approved" and mapping["import_status"] != "approved":
        blockers.append("approved_mapping_import_not_approved")

    question = connection.execute(
        """SELECT q.id, q.content_hash, q.quality_status, q.review_status,
                  sd.id AS source_document_id, sd.file_hash, sd.trusted_source
             FROM questions q JOIN source_documents sd ON sd.id=q.source_document_id
            WHERE q.id=?""",
        (QUESTION_ID,),
    ).fetchone()
    if question is None:
        blockers.append("q013_missing")
        question_data: dict[str, Any] = {}
    else:
        question_data = dict(question)
        if (question["quality_status"], question["review_status"]) != ("blocked", "pending"):
            blockers.append("question_status_not_blocked_pending")
        if question["trusted_source"] != 1:
            blockers.append("question_source_not_trusted")

    snapshot = connection.execute(
        """SELECT input_hash, revision, invalidated_at
             FROM question_input_snapshots WHERE question_id=?""",
        (QUESTION_ID,),
    ).fetchone()
    current_input_hash: str | None = None
    if question is not None:
        current_input_hash = compute_current_input_hash(connection, QUESTION_ID)
    if snapshot is None or snapshot["invalidated_at"] is not None:
        blockers.append("current_input_snapshot_missing_or_invalidated")
    elif snapshot["input_hash"] != current_input_hash:
        blockers.append("current_input_snapshot_stale")

    evidence = connection.execute(
        """SELECT id, question_input_hash, textbook_catalog_version, node_catalog_version,
                  feature_hash, evidence_hash
             FROM current_question_curriculum_mapping_evidence
            WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?
              AND status='candidate'""",
        (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
    ).fetchone()
    if evidence is None:
        blockers.append("current_candidate_draft_evidence_missing")
        evidence_data: dict[str, Any] = {}
    else:
        evidence_data = dict(evidence)
        if evidence["question_input_hash"] != current_input_hash:
            blockers.append("draft_evidence_input_hash_mismatch")

    auto_audit = connection.execute(
        """SELECT id, question_input_hash, audit_status, audit_hash, validator_results_json
             FROM current_question_auto_mapping_audits
            WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?
              AND audit_status='pass'""",
        (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
    ).fetchone()
    if auto_audit is None:
        blockers.append("current_automatic_mapping_audit_pass_missing")
        auto_audit_data: dict[str, Any] = {}
    else:
        auto_audit_data = dict(auto_audit)
        if auto_audit["question_input_hash"] != current_input_hash:
            blockers.append("automatic_audit_input_hash_mismatch")
        try:
            validators = json.loads(auto_audit["validator_results_json"])
        except (TypeError, json.JSONDecodeError):
            validators = None
        if not isinstance(validators, dict) or set(validators) != {
            "source_fidelity", "structural_consistency", "mathematical_independent", "asset_semantics"
        } or any(not isinstance(value, dict) or value.get("status") != "pass" for value in validators.values()):
            blockers.append("automatic_audit_validator_bundle_invalid")

    knowledge = _rows(connection.execute(
        """SELECT qkp.knowledge_point_id, qkp.relation_type, kp.review_status,
                  ckp.relation_type AS curriculum_relation_type
             FROM question_knowledge_points qkp
             JOIN question_knowledge_point_imports qkpi
               ON qkpi.question_id=qkp.question_id
              AND qkpi.knowledge_point_id=qkp.knowledge_point_id
              AND qkpi.import_run_id=? AND qkpi.mapping_hash=?
             JOIN knowledge_points kp ON kp.id=qkp.knowledge_point_id
             JOIN curriculum_knowledge_points ckp
               ON ckp.knowledge_point_id=qkp.knowledge_point_id
              AND ckp.curriculum_node_id=?
            WHERE qkp.question_id=? ORDER BY qkp.knowledge_point_id""",
        (mapping["import_run_id"], mapping["mapping_hash"], NODE_ID, QUESTION_ID),
    ))
    if tuple(row["knowledge_point_id"] for row in knowledge) != EXPECTED_KNOWLEDGE_POINT_IDS:
        blockers.append("approved_required_knowledge_mapping_missing_or_drifted")
    elif any(row["review_status"] != "approved" for row in knowledge):
        blockers.append("required_knowledge_not_approved")

    catalog = connection.execute(
        """SELECT t.catalog_version AS textbook_catalog_version, t.status AS textbook_status,
                  n.catalog_version AS node_catalog_version, n.status AS node_status,
                  cr.id AS catalog_release_id, cr.status AS catalog_release_status,
                  ca.id AS catalog_audit_id, ca.status AS catalog_audit_status
             FROM textbooks t JOIN curriculum_nodes n ON n.id=? AND n.textbook_id=t.id
             LEFT JOIN catalog_releases cr ON cr.textbook_id=t.id AND cr.catalog_version=t.catalog_version
             LEFT JOIN catalog_audits ca ON ca.catalog_release_id=cr.id AND ca.status='approved'
            WHERE t.id=?""",
        (NODE_ID, TEXTBOOK_ID),
    ).fetchone()
    if catalog is None or catalog["textbook_status"] != "active" or catalog["node_status"] != "active" or catalog["catalog_release_status"] != "approved" or catalog["catalog_audit_status"] != "approved":
        blockers.append("current_approved_catalog_or_node_missing")
    elif evidence is not None and (catalog["textbook_catalog_version"], catalog["node_catalog_version"]) != (evidence["textbook_catalog_version"], evidence["node_catalog_version"]):
        blockers.append("draft_evidence_catalog_version_mismatch")

    content = _rows(connection.execute(
        """SELECT id, content_type, layer, allowed_node_ids_json, required_knowledge_json,
                  normalized_text_sha256
             FROM current_controlled_content_segments
            WHERE textbook_id=? AND curriculum_node_id=?
            ORDER BY content_type, layer, id""",
        (TEXTBOOK_ID, NODE_ID),
    ))
    content_types = {(row["content_type"], row["layer"]) for row in content}
    if content_types != set(REQUIRED_CONTENT):
        blockers.append("required_current_controlled_content_missing_or_drifted")
    else:
        for row in content:
            try:
                allowed = json.loads(row["allowed_node_ids_json"])
                required = json.loads(row["required_knowledge_json"])
            except (TypeError, json.JSONDecodeError):
                blockers.append("controlled_content_contract_invalid")
                break
            if NODE_ID not in allowed:
                blockers.append("progress_boundary_no_longer_allows_target_node")
                break
            if "等腰三角形" not in required:
                blockers.append("controlled_content_required_knowledge_drifted")
                break

    delivery_counts = _delivery_counts(connection)
    if any(delivery_counts.values()):
        blockers.append("delivery_related_tables_not_empty")

    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        blockers.append("sqlite_integrity_check_failed")

    bundle = {
        "question": question_data,
        "current_input_snapshot": dict(snapshot) if snapshot else None,
        "current_input_hash": current_input_hash,
        "mapping": dict(mapping),
        "catalog": dict(catalog) if catalog else None,
        "knowledge": knowledge,
        "draft_evidence": evidence_data,
        "automatic_mapping_audit": auto_audit_data,
        "controlled_content": content,
        "delivery_counts": delivery_counts,
        "sqlite_integrity_check": integrity,
    }
    return bundle, sorted(set(blockers))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _audit_manifest(mapping: dict[str, Any], mapping_source: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "controlled-question-mapping-audit-v1",
        "import_run_id": mapping["import_run_id"],
        "mapping_hash": mapping["mapping_hash"],
        "source_reference": mapping["source_reference"],
        "source_file": mapping_source.name,
        "source_sha256": mapping["source_hash"],
        "auditor_id": "p1-3b-current-evidence-mapping-audit-v2",
        "findings": [
            {"check": "source-identity", "status": "pass", "evidence": f"source file {mapping_source.name} matches the controlled import hash"},
            {"check": "current-input-and-verification", "status": "pass", "evidence": f"current input {bundle['current_input_hash']} is bound to all four passing automatic validators"},
            {"check": "current-draft-and-auto-audit", "status": "pass", "evidence": f"draft evidence {bundle['draft_evidence']['id']} and automatic audit {bundle['automatic_mapping_audit']['id']} are current"},
            {"check": "catalog-knowledge-content-progress", "status": "pass", "evidence": "approved catalog, required approved knowledge, current controlled content, and target-node progress boundary agree"},
            {"check": "approval-boundary", "status": "pass", "evidence": "the imported batch contains only q013 target mapping; question remains blocked/pending and delivery tables are empty"},
        ],
    }


def run_p1_3b(
    database: str | Path = DEFAULT_DATABASE,
    *,
    mapping_source: str | Path = DEFAULT_SOURCE,
    report_path: str | Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    """Approve or revoke the sole P1-3b mapping and publish an evidence report."""
    database = Path(database).resolve()
    mapping_source = Path(mapping_source).resolve()
    report_path = Path(report_path).resolve()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        before_protected = _protected_snapshot(connection)
        before_bundle, blockers = _current_bundle(connection, mapping_source)
        mapping = before_bundle.get("mapping")
        action = "blocked_zero_mapping_approval"
        audit_result: dict[str, Any] | None = None
        if not blockers and mapping is not None and mapping["fit_status"] == "pending" and mapping["import_status"] == "validated":
            manifest_path = report_path.with_name("p1-3b_q013_mapping_audit_manifest.json")
            _write_json(manifest_path, _audit_manifest(mapping, mapping_source, before_bundle))
            audit_result = record_question_mapping_audit(connection, manifest_path)
            action = "approved_one_mapping"
            _, post_approval_blockers = _current_bundle(connection, mapping_source)
            if post_approval_blockers:
                blockers.extend(f"post_approval:{item}" for item in post_approval_blockers)
        elif not blockers and mapping is not None and mapping["fit_status"] == "approved" and mapping["import_status"] == "approved":
            existing = connection.execute(
                """SELECT id FROM question_mapping_audits
                     WHERE import_run_id=? AND mapping_hash=? AND status='approved'""",
                (mapping["import_run_id"], mapping["mapping_hash"]),
            ).fetchone()
            if existing is None:
                blockers.append("approved_mapping_audit_record_missing")
            else:
                action = "reused_current_approved_mapping"
        if blockers:
            mapping = _mapping_row(connection)
            if mapping is not None and mapping["fit_status"] == "approved":
                with connection:
                    connection.execute(
                        """UPDATE question_textbooks SET fit_status='pending'
                             WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
                        (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
                    )
                action = "revoked_mapping_to_pending"
                if audit_result is not None:
                    action = "approval_transition_invalidated_evidence_revoked"

        after_bundle, after_blockers = _current_bundle(connection, mapping_source)
        after_protected = _protected_snapshot(connection)
        final_mapping = _mapping_row(connection)
        final_status = final_mapping["fit_status"] if final_mapping else None
        final_question = connection.execute(
            "SELECT quality_status, review_status FROM questions WHERE id=?", (QUESTION_ID,)
        ).fetchone()
        report = {
            "schema": "p1-3b-q013-mapping-approval-v2",
            "database": str(database),
            "mapping_source": str(mapping_source),
            "action": action,
            "approved_mapping_count": 1 if action == "reused_current_approved_mapping" else 0,
            "audit_result": audit_result,
            "blockers_before_action": blockers,
            "blockers_after_action": after_blockers,
            "before": before_bundle,
            "after": after_bundle,
            "fit_status": final_status,
            "question_status": list(final_question) if final_question else None,
            "question_approved": False,
            "delivery_tables_empty": not any(after_bundle["delivery_counts"].values()),
            "protected_tables_unchanged": before_protected == after_protected,
        }
        _write_json(report_path, report)
        return report
    finally:
        connection.close()
