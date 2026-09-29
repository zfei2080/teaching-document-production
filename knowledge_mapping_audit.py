"""Approve a knowledge-point import batch only through source-bound audit evidence."""

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
AUDIT_METHOD = "controlled-knowledge-mapping-audit-v1"


class KnowledgeMappingAuditError(ValueError):
    """Knowledge audit evidence is incomplete, drifting, or non-reproducible."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_audit_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeMappingAuditError(f"knowledge audit manifest must be UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise KnowledgeMappingAuditError("knowledge audit manifest root must be an object")
    return value, sha256_bytes(raw)


def required(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise KnowledgeMappingAuditError(f"missing or invalid audit field: {key}")
    return item.strip()


def record_knowledge_mapping_audit(conn: sqlite3.Connection, audit_manifest_path: Path) -> dict[str, str | int]:
    manifest, manifest_hash = load_audit_manifest(audit_manifest_path)
    if manifest.get("schema_version") != "controlled-knowledge-mapping-audit-v1":
        raise KnowledgeMappingAuditError("unsupported schema_version; expected controlled-knowledge-mapping-audit-v1")
    import_id = required(manifest, "import_run_id")
    source_reference = required(manifest, "source_reference")
    source_hash = required(manifest, "source_sha256")
    auditor_id = required(manifest, "auditor_id")
    findings = manifest.get("findings")
    if not isinstance(findings, list) or not findings:
        raise KnowledgeMappingAuditError("findings must be a non-empty list")
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("status") not in {"pass", "fail"}:
            raise KnowledgeMappingAuditError("each finding status must be pass or fail")
        required(finding, "check")
        required(finding, "evidence")
    if any(finding["status"] != "pass" for finding in findings):
        raise KnowledgeMappingAuditError("a knowledge mapping audit may be recorded only when every finding passes")

    imported = conn.execute("SELECT catalog_release_id, source_reference, source_hash, status FROM controlled_knowledge_import_runs WHERE id=?", (import_id,)).fetchone()
    if imported is None or imported[3] != "validated":
        raise KnowledgeMappingAuditError("audit requires a validated controlled knowledge import run")
    if (imported[1], imported[2]) != (source_reference, source_hash):
        raise KnowledgeMappingAuditError("audit source identity must exactly match the controlled knowledge import")
    release = conn.execute("SELECT status FROM catalog_releases WHERE id=?", (imported[0],)).fetchone()
    if release is None or release[0] != "approved":
        raise KnowledgeMappingAuditError("knowledge import catalog release is no longer approved")
    expected_points = conn.execute("SELECT COUNT(*) FROM knowledge_point_imports WHERE import_run_id=?", (import_id,)).fetchone()[0]
    expected_relations = conn.execute("SELECT COUNT(*) FROM curriculum_knowledge_point_imports WHERE import_run_id=?", (import_id,)).fetchone()[0]
    complete_points = conn.execute("SELECT COUNT(*) FROM knowledge_point_imports kpi JOIN knowledge_points kp ON kp.id=kpi.knowledge_point_id WHERE kpi.import_run_id=? AND kp.review_status='pending'", (import_id,)).fetchone()[0]
    complete_relations = conn.execute("SELECT COUNT(*) FROM curriculum_knowledge_point_imports ckpi JOIN curriculum_knowledge_points ckp ON ckp.curriculum_node_id=ckpi.curriculum_node_id AND ckp.knowledge_point_id=ckpi.knowledge_point_id JOIN curriculum_nodes n ON n.id=ckpi.curriculum_node_id WHERE ckpi.import_run_id=? AND n.status='active'", (import_id,)).fetchone()[0]
    if not expected_points or not expected_relations or (expected_points, expected_relations) != (complete_points, complete_relations):
        raise KnowledgeMappingAuditError("knowledge batch is incomplete, drifted, or no longer pending/active")

    audit_id = f"knowledge-mapping-audit:{uuid.uuid4()}"
    with conn:
        conn.execute("INSERT INTO knowledge_mapping_audits (id, import_run_id, audit_manifest_hash, auditor_id, audit_method, source_reference, source_hash, status, findings_json) VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?)", (audit_id, import_id, manifest_hash, auditor_id, AUDIT_METHOD, source_reference, source_hash, json.dumps(findings, ensure_ascii=False, sort_keys=True)))
        conn.execute("UPDATE controlled_knowledge_import_runs SET status='approved' WHERE id=?", (import_id,))
        conn.execute("UPDATE knowledge_points SET review_status='approved' WHERE id IN (SELECT knowledge_point_id FROM knowledge_point_imports WHERE import_run_id=?)", (import_id,))
    return {"audit_id": audit_id, "import_run_id": import_id, "approved_knowledge_points": expected_points, "approved_relations": expected_relations, "status": "approved"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Record an approved source-bound knowledge mapping audit")
    parser.add_argument("audit_manifest", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        print(json.dumps(record_knowledge_mapping_audit(conn, args.audit_manifest), ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
