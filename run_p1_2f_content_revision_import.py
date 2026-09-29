"""Revalidate and append the P1-2f controlled-content source revisions.

This runner is the only production entry for moving a newly Word-COM-verified
P1-2b content chain into the development database.  It preserves the earlier
content rows as history, advances only the two controlled-content heads, and
does not approve questions or create delivery artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from controlled_content_revision_import import (
    build_revision_manifest,
    import_revision_manifest,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
DEFAULT_AUDIT = ROOT / "output" / "audits" / "p1-2b_controlled_content_validation.json"
DEFAULT_REPORT = ROOT / "output" / "audits" / "p1-2f_controlled_content_revision_import.json"
DEFAULT_BACKUP_DIR = ROOT / "data" / "dev" / "backups" / "p1-2f"
P1_2B_RUNNER = ROOT / "run_p1_2b_controlled_content_validation.py"

REQUIRED_MIGRATIONS = (
    "v2.28-p13c-approval-boundary-and-mapping-revisions",
    "v2.29-p12f-append-only-controlled-content-source-revisions",
)
PROTECTED_TABLES = (
    "questions",
    "question_textbooks",
    "question_knowledge_points",
    "question_verifications",
    "teaching_documents",
    "document_questions",
    "quality_reports",
    "question_usage",
)
DELIVERY_TABLES = (
    "teaching_documents",
    "document_questions",
    "quality_reports",
    "question_usage",
)


class P12FRevisionImportBlockedError(RuntimeError):
    """Raised when the recovered content chain cannot be appended safely."""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _hash_file(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _rows_hash(connection: sqlite3.Connection, table: str) -> str:
    cursor = connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
    names = [column[0] for column in cursor.description]
    rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    return _hash_bytes(_canonical(rows).encode("utf-8"))


def _protected_snapshot(connection: sqlite3.Connection) -> dict[str, str]:
    return {table: _rows_hash(connection, table) for table in PROTECTED_TABLES}


def _delivery_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in DELIVERY_TABLES
    }


def _migration_snapshot(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(row[0]), str(row[1]))
        for row in connection.execute("SELECT version, applied_at FROM schema_migrations ORDER BY rowid")
    )


def _require_schema(connection: sqlite3.Connection) -> None:
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT version FROM schema_migrations WHERE version IN (?, ?)", REQUIRED_MIGRATIONS
        )
    }
    missing = [migration for migration in REQUIRED_MIGRATIONS if migration not in present]
    if missing:
        raise P12FRevisionImportBlockedError("required_migration_missing:" + ",".join(missing))


def _create_backup(database: Path, backup_dir: Path) -> dict[str, str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"{database.stem}.before-p1-2f.{suffix}.{uuid.uuid4().hex}.db"
    source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.commit()
        check = str(target.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        target.close()
        source.close()
    if check != "ok":
        raise P12FRevisionImportBlockedError(f"backup_integrity_check_failed:{check}")
    return {
        "path": str(destination.resolve()),
        "sha256": _hash_file(destination),
        "quick_check": check,
    }


def _current_content_rows(
    connection: sqlite3.Connection, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    expected = {
        str(segment["content_type"]): {
            "segment_id": str(segment["id"]),
            "source_id": str(segment["source_id"]),
            "revision_hash": str(segment["revision_hash"]),
        }
        for segment in manifest["segments"]
    }
    rows = connection.execute(
        """SELECT s.id AS segment_row_id, s.content_type, s.layer, s.textbook_id,
                  s.curriculum_node_id, src.id AS source_id, src.import_run_id,
                  r.revision_hash, r.validation_manifest_sha256, h.head_revision
             FROM current_controlled_content_segments s
             JOIN controlled_content_sources src ON src.id=s.source_id
             JOIN current_controlled_content_source_revisions r ON r.segment_row_id=s.id
             JOIN controlled_content_source_revision_heads h
               ON h.current_revision_id=r.id
            WHERE s.textbook_id=? AND s.curriculum_node_id=?
            ORDER BY s.content_type""",
        (
            manifest["segments"][0]["textbook_id"],
            manifest["segments"][0]["curriculum_node_id"],
        ),
    ).fetchall()
    if len(rows) != len(expected):
        raise P12FRevisionImportBlockedError("current_controlled_content_row_count_invalid")
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        requirement = expected.get(str(item["content_type"]))
        if requirement is None:
            raise P12FRevisionImportBlockedError("unexpected_current_content_type")
        if (
            item["segment_row_id"] != requirement["segment_id"]
            or item["source_id"] != requirement["source_id"]
            or item["revision_hash"] != requirement["revision_hash"]
            or item["validation_manifest_sha256"] != manifest["p1_2b_audit_sha256"]
        ):
            raise P12FRevisionImportBlockedError("current_content_revision_does_not_match_revalidated_manifest")
        if int(item["head_revision"]) < 2:
            raise P12FRevisionImportBlockedError("content_revision_head_not_advanced")
        result.append(item)
    return result


@contextmanager
def _connection(database: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
    finally:
        connection.close()


def _stage_report(report_path: Path, report: dict[str, Any]) -> Path:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=report_path.parent, delete=False
    ) as handle:
        staged = Path(handle.name)
        json.dump(report, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return staged


def import_revalidated_content_revision(
    database: str | Path,
    *,
    audit_path: str | Path,
    workspace: str | Path,
    report_path: str | Path,
    backup_dir: str | Path,
) -> dict[str, Any]:
    """Append only the freshly validated P1-2b content revision into a DB."""
    database_path = Path(database).resolve()
    audit = Path(audit_path).resolve()
    workspace_path = Path(workspace).resolve()
    destination = Path(report_path).resolve()
    before_database_hash = _hash_file(database_path)
    backup = _create_backup(database_path, Path(backup_dir).resolve())
    manifest = build_revision_manifest(audit, workspace=workspace_path)

    with _connection(database_path) as connection:
        _require_schema(connection)
        migrations_before = _migration_snapshot(connection)
        protected_before = _protected_snapshot(connection)
        snapshots_before = _rows_hash(connection, "question_approval_input_snapshots_v2")
        heads_before = {
            str(row["content_type"]): int(row["head_revision"])
            for row in connection.execute(
                """SELECT content_type, head_revision
                     FROM controlled_content_source_revision_heads
                    WHERE textbook_id=? AND curriculum_node_id=?""",
                (
                    manifest["segments"][0]["textbook_id"],
                    manifest["segments"][0]["curriculum_node_id"],
                ),
            )
        }
        try:
            connection.execute("BEGIN IMMEDIATE")
            outcome = import_revision_manifest(connection, manifest)
            current_rows = _current_content_rows(connection, manifest)
            protected_after = _protected_snapshot(connection)
            if protected_before != protected_after:
                changed = [
                    table for table in PROTECTED_TABLES
                    if protected_before[table] != protected_after[table]
                ]
                raise P12FRevisionImportBlockedError(
                    "protected_business_tables_changed:" + ",".join(changed)
                )
            delivery_counts = _delivery_counts(connection)
            if any(delivery_counts.values()):
                raise P12FRevisionImportBlockedError("delivery_related_tables_not_empty")
            q013 = connection.execute(
                """SELECT q.quality_status, q.review_status, qt.fit_status
                     FROM questions q
                     JOIN question_textbooks qt ON qt.question_id=q.id
                    WHERE q.id='golden-q013' AND qt.textbook_id=? AND qt.curriculum_node_id=?""",
                (
                    manifest["segments"][0]["textbook_id"],
                    manifest["segments"][0]["curriculum_node_id"],
                ),
            ).fetchone()
            if q013 is None or tuple(q013) != ("blocked", "pending", "pending"):
                raise P12FRevisionImportBlockedError("q013_must_remain_blocked_pending")
            migrations_after = _migration_snapshot(connection)
            if migrations_before != migrations_after:
                raise P12FRevisionImportBlockedError("schema_migrations_changed")
            heads_after = {
                str(row["content_type"]): int(row["head_revision"])
                for row in connection.execute(
                    """SELECT content_type, head_revision
                         FROM controlled_content_source_revision_heads
                        WHERE textbook_id=? AND curriculum_node_id=?""",
                    (
                        manifest["segments"][0]["textbook_id"],
                        manifest["segments"][0]["curriculum_node_id"],
                    ),
                )
            }
            if outcome["status"] == "validated" and any(
                heads_after.get(content_type) != heads_before.get(content_type, 0) + 1
                for content_type in heads_after
            ):
                raise P12FRevisionImportBlockedError("validated_content_head_revision_not_incremented")
            if outcome["status"] == "reused" and heads_after != heads_before:
                raise P12FRevisionImportBlockedError("reused_content_head_revision_changed")
            snapshots_after = _rows_hash(connection, "question_approval_input_snapshots_v2")
            report = {
                "schema": "p1-2f-controlled-content-revision-import-run-v1",
                "purpose": "append_only_revalidated_controlled_content_source_import",
                "database": {
                    "path": str(database_path),
                    "sha256_before": before_database_hash,
                    "sha256_after": None,
                    "migrations_before": migrations_before,
                    "migrations_after": migrations_after,
                },
                "backup": backup,
                "p1_2b_audit": {
                    "path": str(audit),
                    "sha256": manifest["p1_2b_audit_sha256"],
                },
                "outcome": outcome,
                "current_content_rows": current_rows,
                "head_revisions_before": heads_before,
                "head_revisions_after": heads_after,
                "protected_business_tables_unchanged": True,
                "approval_snapshot_v2_changed_by_content_head_event": snapshots_before != snapshots_after,
                "q013_status": {
                    "quality_status": q013[0],
                    "review_status": q013[1],
                    "fit_status": q013[2],
                },
                "delivery_counts": delivery_counts,
                "prohibited_actions_performed": [],
            }
            staged_report = _stage_report(destination, report)
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    report["database"]["sha256_after"] = _hash_file(database_path)
    _stage_report(destination, report).replace(destination)
    staged_report.unlink(missing_ok=True)
    return report


def _rerun_p1_2b_validation() -> None:
    completed = subprocess.run(
        [sys.executable, str(P1_2B_RUNNER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=360,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "p1_2b_validation_failed").strip()
        raise P12FRevisionImportBlockedError(
            f"p1_2b_revalidation_failed:{completed.returncode}:{detail[:1000]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Revalidate Word-COM content and append its P1-2f source revision"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    args = parser.parse_args()
    try:
        _rerun_p1_2b_validation()
        report = import_revalidated_content_revision(
            args.db,
            audit_path=args.audit,
            workspace=ROOT,
            report_path=args.report,
            backup_dir=args.backup_dir,
        )
    except (OSError, ValueError, sqlite3.Error, P12FRevisionImportBlockedError) as exc:
        print(f"p1_2f_controlled_content_revision_import_blocked:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 2
    print(f"report={args.report}")
    print("outcome=" + str(report["outcome"]["status"]))
    print("protected_business_tables_unchanged=" + str(report["protected_business_tables_unchanged"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
