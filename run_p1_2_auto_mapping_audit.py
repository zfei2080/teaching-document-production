"""P1-2: controlled append-only audit exercise for draft auto mapping candidates.

This runner reads existing v2.22 draft evidence plus current verification rows,
and writes only question_auto_mapping_audit_logs plus an audit JSON report.
It never mutates questions, approved mappings, knowledge bindings, verification
records, documents, delivery, or usage history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from question_auto_mapping_audit import AutoMappingAuditRequest, record_auto_mapping_audit

RUNNER_ID = "p1-2-auto-mapping-audit"
RUNNER_VERSION = "1.0.0"

SQLITE_OK = 0
SQLITE_DENY = 1
SQLITE_INSERT = 18
SQLITE_UPDATE = 23
SQLITE_TRANSACTION = 22
SQLITE_READ = 20
SQLITE_SELECT = 21
SQLITE_FUNCTION = 31
SQLITE_PRAGMA = 19
SQLITE_SAVEPOINT = 32

PROTECTED_TABLES = (
    "questions",
    "question_textbooks",
    "question_knowledge_points",
    "question_verifications",
    "teaching_documents",
    "quality_reports",
    "question_usage",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def capture_db_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    approved_count = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE quality_status='approved' OR review_status='approved'"
    ).fetchone()[0]
    return {
        "questions": tuple(conn.execute(
            "SELECT id, quality_status, review_status, content_hash, source_document_id FROM questions ORDER BY id"
        ).fetchall()),
        "question_textbooks": tuple(conn.execute(
            "SELECT question_id, textbook_id, curriculum_node_id, fit_status FROM question_textbooks ORDER BY question_id, textbook_id, curriculum_node_id"
        ).fetchall()),
        "question_knowledge_points": tuple(conn.execute(
            "SELECT question_id, knowledge_point_id, relation_type FROM question_knowledge_points ORDER BY question_id, knowledge_point_id"
        ).fetchall()),
        "question_verifications": tuple(conn.execute(
            "SELECT question_id, verification_type, status, input_hash FROM question_verifications ORDER BY question_id, verification_type, verified_at, rowid"
        ).fetchall()),
        "question_auto_mapping_audit_logs": tuple(conn.execute(
            "SELECT id, evidence_id, question_id, audit_status, decision_basis, audit_hash FROM question_auto_mapping_audit_logs ORDER BY created_at, id"
        ).fetchall()),
        "approved_count": approved_count,
        "teaching_documents": tuple(conn.execute(
            "SELECT id, request_id, selection_plan_id, status, output_path, content_hash FROM teaching_documents ORDER BY id"
        ).fetchall()),
        "quality_reports": tuple(conn.execute(
            "SELECT id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, status FROM quality_reports ORDER BY id"
        ).fetchall()),
        "question_usage": tuple(conn.execute(
            "SELECT id, question_id, class_id, document_id, delivered, reuse_allowed FROM question_usage ORDER BY id"
        ).fetchall()),
    }


def assert_protected_snapshot_unchanged(before: dict[str, Any], after: dict[str, Any]) -> None:
    for key in PROTECTED_TABLES:
        if before[key] != after[key]:
            raise RuntimeError(f"protected_snapshot_changed:{key}")
    if before["approved_count"] != after["approved_count"]:
        raise RuntimeError("approved_count_changed")


def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger_name: str | None) -> int:
    if action == SQLITE_INSERT:
        if arg1 == "question_auto_mapping_audit_logs":
            return SQLITE_OK
        return SQLITE_DENY
    if action == SQLITE_UPDATE:
        return SQLITE_DENY
    if action in (SQLITE_READ, SQLITE_SELECT, SQLITE_TRANSACTION, SQLITE_FUNCTION, SQLITE_PRAGMA, SQLITE_SAVEPOINT):
        return SQLITE_OK
    return SQLITE_DENY


@contextmanager
def guarded_connection(db_path: Path) -> Iterable[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.set_authorizer(_authorizer)
    try:
        yield conn
    finally:
        conn.close()


def _stage_report(output_path: Path, report: dict[str, Any]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_path.parent, delete=False) as handle:
        temp_path = Path(handle.name)
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return temp_path


def _publish_staged_report(staged_path: Path, output_path: Path) -> None:
    staged_path.replace(output_path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _capture_migrations(conn: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    return tuple(
        (row["version"], row["applied_at"])
        for row in conn.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY rowid"
        ).fetchall()
    )


def _create_backup(db_path: Path, backup_dir: Path) -> dict[str, Any]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (
        f"{db_path.stem}.before-p1-2-audit."
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}."
        f"{uuid.uuid4().hex}.db"
    )
    source = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
        destination.commit()
        quick_check = destination.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        destination.close()
        source.close()
    if quick_check != "ok":
        raise RuntimeError(f"backup_integrity_check_failed:{quick_check}")
    return {
        "path": str(backup_path.resolve()),
        "sha256": _sha256(backup_path),
        "quick_check": quick_check,
    }


def run(db_path: Path, output_path: Path, *, backup_dir: Path | None = None) -> dict[str, Any]:
    db_path = db_path.resolve()
    before_db_hash = _sha256(db_path)
    backup = _create_backup(db_path, backup_dir) if backup_dir is not None else None
    with guarded_connection(db_path) as conn:
        before_snapshot = capture_db_snapshot(conn)
        committed = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            migrations_before = _capture_migrations(conn)
            evidence_rows = conn.execute(
                """
                SELECT id, question_id
                FROM current_question_curriculum_mapping_evidence
                ORDER BY question_id, created_at, id
                """
            ).fetchall()
            results: list[dict[str, Any]] = []
            for row in evidence_rows:
                result = record_auto_mapping_audit(
                    conn,
                    AutoMappingAuditRequest(evidence_id=row["id"], reuse_existing=True),
                )
                results.append(
                    {
                        "evidence_id": row["id"],
                        "question_id": row["question_id"],
                        "audit_id": result.audit_id,
                        "audit_status": result.audit_status,
                        "decision_basis": result.decision_basis,
                        "reused_existing": result.reused_existing,
                        "findings": result.findings,
                        "audit": result.audit_json,
                    }
                )
            after_snapshot = capture_db_snapshot(conn)
            assert_protected_snapshot_unchanged(before_snapshot, after_snapshot)
            migrations_after = _capture_migrations(conn)
            report = {
                "exercise": "P1-2",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "runner_id": RUNNER_ID,
                "runner_version": RUNNER_VERSION,
                "write_scope": ["INSERT question_auto_mapping_audit_logs"],
                "prohibited_writes": list(PROTECTED_TABLES),
                "database": {
                    "path": str(db_path),
                    "sha256_before": before_db_hash,
                    "sha256_after": None,
                    "migrations_before": migrations_before,
                    "migrations_after": migrations_after,
                },
                "backup": backup,
                "summary": {
                    "audit_count": len(results),
                    "created_count": sum(not item["reused_existing"] for item in results),
                    "reused_count": sum(item["reused_existing"] for item in results),
                    "statuses": dict(Counter(item["audit_status"] for item in results)),
                    "approved_count": after_snapshot["approved_count"],
                    "current_pass_count": conn.execute("SELECT COUNT(*) FROM current_question_auto_mapping_audits").fetchone()[0],
                },
                "protected_snapshot_unchanged": {
                    "questions": before_snapshot["questions"] == after_snapshot["questions"],
                    "question_textbooks": before_snapshot["question_textbooks"] == after_snapshot["question_textbooks"],
                    "question_knowledge_points": before_snapshot["question_knowledge_points"] == after_snapshot["question_knowledge_points"],
                    "question_verifications": before_snapshot["question_verifications"] == after_snapshot["question_verifications"],
                    "teaching_documents": before_snapshot["teaching_documents"] == after_snapshot["teaching_documents"],
                    "quality_reports": before_snapshot["quality_reports"] == after_snapshot["quality_reports"],
                    "question_usage": before_snapshot["question_usage"] == after_snapshot["question_usage"],
                },
                "results": results,
            }
            conn.commit()
            committed = True
            report["database"]["sha256_after"] = _sha256(db_path)
            staged_report_path = _stage_report(output_path, report)
            _publish_staged_report(staged_report_path, output_path)
            return report
        except Exception:
            if not committed:
                conn.rollback()
            raise


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=root / "data" / "dev" / "teaching_docs_dev.db")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "output" / "audits" / "p1-2_auto_mapping_audit.json",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=root / "data" / "dev" / "backups" / "p1-2",
        help="Directory for a consistent pre-audit development-database backup.",
    )
    args = parser.parse_args()
    report = run(args.db, args.output, backup_dir=args.backup_dir)
    print(_json({"output": str(args.output), "summary": report["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
