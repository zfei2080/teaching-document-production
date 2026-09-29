"""Approve a controlled question-mapping batch only through source-bound audit evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
DEFAULT_DB = ROOT / "data" / "dev" / "teaching_docs_dev.db"
AUDIT_METHOD = "controlled-question-mapping-audit-v1"


class QuestionMappingAuditError(ValueError):
    """The mapping-audit evidence is incomplete, drifting, or not reproducible."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_audit_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuestionMappingAuditError(f"audit manifest must be UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise QuestionMappingAuditError("audit manifest root must be an object")
    return value, sha256_bytes(raw)


def required(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise QuestionMappingAuditError(f"missing or invalid audit field: {key}")
    return result.strip()


def _validate_findings(value: dict[str, Any]) -> list[dict[str, Any]]:
    findings = value.get("findings")
    if not isinstance(findings, list) or not findings:
        raise QuestionMappingAuditError("findings must be a non-empty list")
    for finding in findings:
        if not isinstance(finding, dict):
            raise QuestionMappingAuditError("each finding must be an object")
        if finding.get("status") not in {"pass", "fail"}:
            raise QuestionMappingAuditError("each finding status must be pass or fail")
        required(finding, "check")
        required(finding, "evidence")
    if any(item["status"] != "pass" for item in findings):
        raise QuestionMappingAuditError("a question mapping audit may be recorded only when every finding passes")
    return findings


def record_question_mapping_audit(conn: sqlite3.Connection, audit_manifest_path: Path) -> dict[str, str | int]:
    manifest, manifest_hash = load_audit_manifest(audit_manifest_path)
    if manifest.get("schema_version") != "controlled-question-mapping-audit-v1":
        raise QuestionMappingAuditError("unsupported schema_version; expected controlled-question-mapping-audit-v1")
    import_id = required(manifest, "import_run_id")
    mapping_hash = required(manifest, "mapping_hash")
    source_reference = required(manifest, "source_reference")
    source_file = required(manifest, "source_file")
    source_hash = required(manifest, "source_sha256")
    auditor_id = required(manifest, "auditor_id")
    findings = _validate_findings(manifest)

    imported = conn.execute(
        """SELECT import_kind, source_reference, source_hash, status
           FROM controlled_import_runs WHERE id=?""", (import_id,)
    ).fetchone()
    if imported is None:
        raise QuestionMappingAuditError("controlled question mapping import does not exist")
    if imported[0] != "question_mapping" or imported[3] != "validated":
        raise QuestionMappingAuditError("audit requires a validated question_mapping import run")
    if (imported[1], imported[2]) != (source_reference, source_hash):
        raise QuestionMappingAuditError("audit source identity must exactly match the controlled import")

    findings_by_check = {item["check"]: item for item in findings}
    source_identity = findings_by_check.get("source-identity")
    if source_identity is None:
        raise QuestionMappingAuditError("audit findings must include source-identity evidence")
    if source_file not in source_identity["evidence"]:
        raise QuestionMappingAuditError("audit source-identity evidence must name the audited source file")

    rows = conn.execute(
        """SELECT qti.question_id, qti.textbook_id, qti.curriculum_node_id
           FROM question_textbook_imports qti
           JOIN question_textbooks qt
             ON qt.question_id=qti.question_id
            AND qt.textbook_id=qti.textbook_id
            AND qt.curriculum_node_id=qti.curriculum_node_id
           JOIN curriculum_nodes n ON n.id=qt.curriculum_node_id
           JOIN textbooks t ON t.id=qt.textbook_id
           JOIN catalog_releases cr ON cr.textbook_id=t.id AND cr.catalog_version=t.catalog_version
           JOIN question_knowledge_points qkp ON qkp.question_id=qt.question_id
           JOIN question_knowledge_point_imports qkpi
             ON qkpi.question_id=qkp.question_id
            AND qkpi.knowledge_point_id=qkp.knowledge_point_id
            AND qkpi.import_run_id=qti.import_run_id
            AND qkpi.mapping_hash=qti.mapping_hash
           JOIN knowledge_points kp ON kp.id=qkp.knowledge_point_id AND kp.review_status='approved'
           JOIN curriculum_knowledge_points ckp
             ON ckp.knowledge_point_id=qkp.knowledge_point_id
            AND ckp.curriculum_node_id=qt.curriculum_node_id
           WHERE qti.import_run_id=? AND qti.mapping_hash=?
             AND qt.fit_status='pending' AND n.status='active' AND cr.status='approved'
           GROUP BY qti.question_id, qti.textbook_id, qti.curriculum_node_id""",
        (import_id, mapping_hash),
    ).fetchall()
    expected = conn.execute(
        "SELECT COUNT(*) FROM question_textbook_imports WHERE import_run_id=? AND mapping_hash=?",
        (import_id, mapping_hash),
    ).fetchone()[0]
    if expected == 0 or len(rows) != expected:
        raise QuestionMappingAuditError("mapping batch is incomplete, drifted, or lacks approved node knowledge-point evidence")

    audit_id = f"question-mapping-audit:{uuid.uuid4()}"
    with conn:
        conn.execute(
            """INSERT INTO question_mapping_audits
               (id, import_run_id, mapping_hash, audit_manifest_hash, auditor_id, audit_method,
                source_reference, source_hash, status, findings_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)""",
            (audit_id, import_id, mapping_hash, manifest_hash, auditor_id, AUDIT_METHOD,
             source_reference, source_hash, json.dumps(findings, ensure_ascii=False, sort_keys=True)),
        )
        conn.execute("UPDATE controlled_import_runs SET status='approved' WHERE id=?", (import_id,))
        conn.execute(
            """UPDATE question_textbooks SET fit_status='approved'
               WHERE (question_id, textbook_id, curriculum_node_id) IN
                 (SELECT question_id, textbook_id, curriculum_node_id
                    FROM question_textbook_imports WHERE import_run_id=? AND mapping_hash=?)""",
            (import_id, mapping_hash),
        )
    return {"audit_id": audit_id, "import_run_id": import_id, "approved_mappings": expected, "status": "approved"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Record an approved source-bound question-mapping audit")
    parser.add_argument("audit_manifest", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        print(json.dumps(record_question_mapping_audit(conn, args.audit_manifest), ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
