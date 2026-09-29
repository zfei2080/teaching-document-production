"""Read-only, fail-closed P1-3 approval precheck for the first student-lecture pilot.

The precheck cannot approve a question.  It can only prove that all required
current evidence is present, or emit a zero-approval gap report.  The live
SQLite database is opened in immutable/read-only mode and its SHA-256 is
checked before and after the inspection.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import sqlite3

from input_snapshot import compute_approval_input_hash_v2, compute_current_input_hash
from textbook_scope_validator import validate as validate_textbook_scope


SCHEMA = "p1-3-first-pilot-approval-precheck-v1"
PILOT_TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
PILOT_CURRENT_NODE_ID = "bsd-math-8x-2026-node-01-section-02"
PILOT_THEME = "\u7b49\u8170\u4e09\u89d2\u5f62"
REQUIRED_VERIFICATIONS = ("source_fidelity", "structural_consistency", "mathematical_independent", "textbook_scope", "asset_semantics")


class P13PrecheckBlockedError(RuntimeError):
    """Raised when the read-only precheck cannot inspect the required evidence."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise P13PrecheckBlockedError("development_database_missing")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)
    ).fetchone() is not None


def _p1_3c_tables_present(connection: sqlite3.Connection) -> bool:
    return all(
        _table_exists(connection, table)
        for table in (
            "question_mapping_source_revision_heads",
            "current_question_mapping_source_revisions",
            "question_approval_input_snapshots_v2",
            "current_question_mapping_approval_evidence_v2",
            "current_question_mapping_auto_audits_v2",
            "question_mapping_approval_audits_v2",
        )
    )


def _p1_3c_context(connection: sqlite3.Connection, question_id: str) -> dict[str, Any] | None:
    """Return current v2 evidence when a mapping revision owns the target.

    A current revision head prevents fallback to legacy v1 mapping evidence:
    otherwise a stale v2 revision could be bypassed by an older audit row.
    """
    if not _p1_3c_tables_present(connection):
        return None
    head = connection.execute(
        """SELECT current_revision_id FROM question_mapping_source_revision_heads
             WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
        (question_id, PILOT_TEXTBOOK_ID, PILOT_CURRENT_NODE_ID),
    ).fetchone()
    if head is None:
        return None
    revision = connection.execute(
        """SELECT id FROM current_question_mapping_source_revisions
             WHERE id=? AND question_id=? AND textbook_id=? AND curriculum_node_id=?""",
        (head["current_revision_id"], question_id, PILOT_TEXTBOOK_ID, PILOT_CURRENT_NODE_ID),
    ).fetchone()
    if revision is None:
        return {
            "active": True,
            "input_hash": None,
            "snapshot_current": False,
            "verification_statuses": {},
            "has_draft": False,
            "has_auto_audit": False,
            "has_knowledge": False,
        }
    try:
        input_hash = compute_approval_input_hash_v2(connection, question_id)
    except (OSError, ValueError, sqlite3.Error):
        input_hash = None
    snapshot = connection.execute(
        """SELECT input_hash, invalidated_at FROM question_approval_input_snapshots_v2
             WHERE question_id=?""",
        (question_id,),
    ).fetchone()
    snapshot_current = bool(
        input_hash and snapshot is not None and snapshot["invalidated_at"] is None
        and snapshot["input_hash"] == input_hash
    )
    evidence = connection.execute(
        """SELECT 1 FROM current_question_mapping_approval_evidence_v2
             WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?
               AND source_revision_id=?""",
        (question_id, PILOT_TEXTBOOK_ID, PILOT_CURRENT_NODE_ID, revision["id"]),
    ).fetchone() is not None
    auto = connection.execute(
        """SELECT validator_results_json FROM current_question_mapping_auto_audits_v2
             WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?
               AND source_revision_id=?""",
        (question_id, PILOT_TEXTBOOK_ID, PILOT_CURRENT_NODE_ID, revision["id"]),
    ).fetchone()
    statuses: dict[str, str] = {}
    if auto is not None:
        try:
            bundle = json.loads(auto["validator_results_json"])
        except (TypeError, json.JSONDecodeError):
            bundle = {}
        if isinstance(bundle, dict):
            statuses = {
                str(key): str(value.get("status"))
                for key, value in bundle.items() if isinstance(value, dict)
            }
    scope = validate_textbook_scope(connection, question_id)
    statuses["textbook_scope"] = scope.status
    knowledge = connection.execute(
        """SELECT 1
             FROM question_mapping_source_revision_knowledge_points rkp
             JOIN knowledge_points kp ON kp.id=rkp.knowledge_point_id
             JOIN curriculum_knowledge_points ckp
               ON ckp.knowledge_point_id=kp.id AND ckp.curriculum_node_id=?
             WHERE rkp.source_revision_id=? AND kp.review_status='approved'
             LIMIT 1""",
        (PILOT_CURRENT_NODE_ID, revision["id"]),
    ).fetchone() is not None
    return {
        "active": True,
        "input_hash": input_hash,
        "snapshot_current": snapshot_current,
        "verification_statuses": statuses,
        "has_draft": evidence,
        "has_auto_audit": auto is not None,
        "has_knowledge": knowledge,
    }


def _verification_statuses(connection: sqlite3.Connection, question_id: str, input_hash: str | None) -> dict[str, str]:
    if not input_hash or not _table_exists(connection, "question_verifications"):
        return {}
    result: dict[str, str] = {}
    for row in connection.execute(
        """
        SELECT verification_type, status
        FROM question_verifications
        WHERE question_id=? AND input_hash=?
        ORDER BY verified_at DESC, id DESC
        """,
        (question_id, input_hash),
    ):
        result.setdefault(str(row["verification_type"]), str(row["status"]))
    return result


def _current_snapshot_is_recorded(connection: sqlite3.Connection, question_id: str, input_hash: str | None) -> bool:
    if not input_hash or not _table_exists(connection, "question_input_snapshots"):
        return False
    row = connection.execute(
        "SELECT input_hash, invalidated_at FROM question_input_snapshots WHERE question_id=?", (question_id,)
    ).fetchone()
    return row is not None and row["input_hash"] == input_hash and row["invalidated_at"] is None


def _source_is_trusted(connection: sqlite3.Connection, source_document_id: str) -> bool:
    if not source_document_id or not _table_exists(connection, "source_documents"):
        return False
    row = connection.execute(
        "SELECT trusted_source, intake_manifest_json FROM source_documents WHERE id=?", (source_document_id,)
    ).fetchone()
    if row is None or int(row["trusted_source"] or 0) != 1:
        return False
    try:
        manifest = json.loads(row["intake_manifest_json"] or "{}")
    except json.JSONDecodeError:
        return False
    return bool(manifest.get("hashes_match"))


def _has_formal_target_mapping(connection: sqlite3.Connection, question_id: str) -> bool:
    if not _table_exists(connection, "question_textbooks"):
        return False
    row = connection.execute(
        """
        SELECT 1 FROM question_textbooks
        WHERE question_id=? AND textbook_id=? AND curriculum_node_id=? AND fit_status='approved'
        """,
        (question_id, PILOT_TEXTBOOK_ID, PILOT_CURRENT_NODE_ID),
    ).fetchone()
    return row is not None


def _has_target_draft_evidence(connection: sqlite3.Connection, question_id: str, input_hash: str) -> bool:
    if not _table_exists(connection, "question_curriculum_mapping_evidence"):
        return False
    row = connection.execute(
        """
        SELECT 1 FROM question_curriculum_mapping_evidence
        WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?
          AND question_input_hash=? AND invalidated_at IS NULL AND status='candidate'
        """,
        (question_id, PILOT_TEXTBOOK_ID, PILOT_CURRENT_NODE_ID, input_hash),
    ).fetchone()
    return row is not None


def _has_target_mapping_audit(connection: sqlite3.Connection, question_id: str, input_hash: str) -> bool:
    if not _table_exists(connection, "question_auto_mapping_audit_logs"):
        return False
    row = connection.execute(
        """
        SELECT 1 FROM question_auto_mapping_audit_logs
        WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?
          AND question_input_hash=? AND audit_status='pass'
        """,
        (question_id, PILOT_TEXTBOOK_ID, PILOT_CURRENT_NODE_ID, input_hash),
    ).fetchone()
    return row is not None


def _has_knowledge_mapping(connection: sqlite3.Connection, question_id: str) -> bool:
    if not _table_exists(connection, "question_knowledge_points"):
        return False
    return connection.execute(
        "SELECT 1 FROM question_knowledge_points WHERE question_id=? LIMIT 1", (question_id,)
    ).fetchone() is not None


def _formal_controlled_content_is_current(connection: sqlite3.Connection) -> bool:
    if not _table_exists(connection, "current_controlled_content_segments"):
        return False
    rows=connection.execute(
        """SELECT s.content_type, s.textbook_id, s.curriculum_node_id,
                  src.original_path, src.original_sha256, src.archive_path, src.archive_sha256,
                  src.converted_path, src.converted_sha256
           FROM current_controlled_content_segments s
           JOIN controlled_content_sources src ON src.id=s.source_id
           WHERE s.textbook_id=? AND s.curriculum_node_id=?""",
        (PILOT_TEXTBOOK_ID,PILOT_CURRENT_NODE_ID),
    ).fetchall()
    if {str(row["content_type"]) for row in rows} != {"knowledge_explanation","consolidation_practice"}:
        return False
    for row in rows:
        triples=((Path(row["original_path"]),row["original_sha256"]),(Path(row["archive_path"]),row["archive_sha256"]),(Path(row["converted_path"]),row["converted_sha256"]))
        try:
            if any(not path.is_file() or _sha256(path).casefold()!=str(expected).casefold() for path,expected in triples):
                return False
        except OSError:
            return False
    return True


def build_p1_3_precheck(
    database_path: str | Path,
    *,
    p12b_audit_path: str | Path,
) -> dict[str, Any]:
    """Inspect every candidate question without modifying the development database."""
    database = Path(database_path).resolve()
    p12b_audit = Path(p12b_audit_path).resolve()
    before_hash = _sha256(database)
    connection = _connect_readonly(database)
    try:
        if not _table_exists(connection, "questions"):
            raise P13PrecheckBlockedError("questions_table_missing")
        controlled_content_ready = _formal_controlled_content_is_current(connection)
        questions = connection.execute(
            """
            SELECT id, stem, answer, analysis, question_type, source_document_id,
                   content_hash, quality_status, review_status
            FROM questions ORDER BY id
            """
        ).fetchall()
        findings: list[dict[str, Any]] = []
        gaps: Counter[str] = Counter()
        for question in questions:
            question_id = str(question["id"])
            reasons: list[str] = []
            stem = str(question["stem"] or "")
            if PILOT_THEME not in stem:
                reasons.append("theme_not_explicit_in_stem_screen")
            if not _source_is_trusted(connection, str(question["source_document_id"] or "")):
                reasons.append("trusted_source_hash_binding_missing")
            p1_3c = _p1_3c_context(connection, question_id)
            evidence_model = "p1-3c-v2" if p1_3c is not None else "legacy-v1"
            if p1_3c is None:
                try:
                    current_input_hash = compute_current_input_hash(connection, question_id)
                except (sqlite3.Error, ValueError):
                    current_input_hash = None
                    reasons.append("current_input_snapshot_unavailable")
                snapshot_current = _current_snapshot_is_recorded(
                    connection, question_id, current_input_hash
                )
                verification_statuses = _verification_statuses(connection, question_id, current_input_hash)
                has_draft = _has_target_draft_evidence(connection, question_id, current_input_hash or "")
                has_mapping_audit = _has_target_mapping_audit(
                    connection, question_id, current_input_hash or "")
                has_knowledge = _has_knowledge_mapping(connection, question_id)
            else:
                current_input_hash = p1_3c["input_hash"]
                snapshot_current = bool(p1_3c["snapshot_current"])
                verification_statuses = dict(p1_3c["verification_statuses"])
                has_draft = bool(p1_3c["has_draft"])
                has_mapping_audit = bool(p1_3c["has_auto_audit"])
                has_knowledge = bool(p1_3c["has_knowledge"])
            if not snapshot_current:
                reasons.append("current_input_snapshot_missing_or_stale")
            for verification_type in REQUIRED_VERIFICATIONS:
                if verification_statuses.get(verification_type) != "pass":
                    reasons.append(f"verification_not_pass:{verification_type}")
            if not has_draft:
                reasons.append("current_target_draft_mapping_missing")
            if not has_mapping_audit:
                reasons.append("current_target_mapping_audit_pass_missing")
            if not _has_formal_target_mapping(connection, question_id):
                reasons.append("approved_target_textbook_mapping_missing")
            if not has_knowledge:
                reasons.append("required_knowledge_mapping_missing")
            if question["quality_status"] == "approved" or question["review_status"] == "approved":
                reasons.append("unexpected_preapproved_question")
            if not controlled_content_ready:
                reasons.append("controlled_content_not_formally_imported_or_current")
            gaps.update(reasons)
            findings.append(
                {
                    "question_id": question_id,
                    "theme_keyword_screen": PILOT_THEME in stem,
                    "evidence_model": evidence_model,
                    "current_input_hash": current_input_hash,
                    "verification_statuses": verification_statuses,
                    "quality_status": question["quality_status"],
                    "review_status": question["review_status"],
                    "approval_eligible": False,
                    "blockers": sorted(set(reasons)),
                }
            )
    finally:
        connection.close()
    after_hash = _sha256(database)
    if before_hash != after_hash:
        raise P13PrecheckBlockedError("read_only_precheck_changed_database")
    eligible = [item for item in findings if item["approval_eligible"]]
    return {
        "schema": SCHEMA,
        "purpose": "read_only_precheck_zero_approval_when_evidence_is_incomplete",
        "pilot": {
            "textbook_id": PILOT_TEXTBOOK_ID,
            "current_curriculum_node_id": PILOT_CURRENT_NODE_ID,
            "theme": PILOT_THEME,
        },
        "database": {"path": str(database), "sha256_before": before_hash, "sha256_after": after_hash, "unchanged": True},
        "controlled_content": {
            "p1_2b_audit_path": str(p12b_audit),
            "formally_import_ready": controlled_content_ready,
        },
        "question_count": len(findings),
        "eligible_question_count": len(eligible),
        "approval_run_authorized": False,
        "approved_question_count": 0,
        "decision": "blocked_zero_approval_gap_report",
        "gap_summary": dict(sorted(gaps.items())),
        "questions": findings,
    }


def write_precheck_report(report: dict[str, Any], path: str | Path) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return destination
