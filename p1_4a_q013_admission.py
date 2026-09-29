"""P1-4a controlled q013 admission rehearsal and development-DB runner.

The runner has one deliberately narrow authority: it can promote q013 after a
current P1-3c mapping approval and five fresh automatic verifier passes.  It
does not create a class, plan, document, quality report, delivery, or usage
history.  All writes are first performed on a SQLite backup copy; the real
development database is replaced only after post-write checks pass.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

from automatic_gate import REQUIRED_VERIFICATION_TYPES, evaluate_automatic_admission
from input_snapshot import compute_current_input_hash
from p1_3c_mapping_approval import NODE_ID, QUESTION_ID, TEXTBOOK_ID
from textbook_scope_validator import validate as validate_textbook_scope


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
DEFAULT_REPORT = ROOT / "output" / "audits" / "p1-4a_q013_admission.json"
DEFAULT_BACKUP_DIR = ROOT / "data" / "dev" / "backups" / "p1-4a"
MIGRATION = "v2.30-p14-current-p13c-question-admission-path"
REQUIRED_PRIOR_MIGRATIONS = (
    "v2.28-p13c-approval-boundary-and-mapping-revisions",
    "v2.29-p12f-append-only-controlled-content-source-revisions",
)
DELIVERY_TABLES = (
    "teaching_documents",
    "document_questions",
    "quality_reports",
    "question_usage",
)
PROTECTED_TABLES = (
    "source_documents",
    "question_textbooks",
    "question_knowledge_points",
    "question_knowledge_point_imports",
    "question_mapping_source_revisions",
    "question_mapping_source_revision_heads",
    "question_mapping_approval_evidence_v2",
    "question_mapping_auto_audits_v2",
    "question_mapping_approval_audits_v2",
    "controlled_content_import_runs",
    "controlled_content_sources",
    "controlled_content_segments",
    "controlled_content_source_revisions",
    "controlled_content_source_revision_heads",
    "classes",
    "class_progress_controls",
    "class_progress_allowed_nodes",
    *DELIVERY_TABLES,
)


class P14AAdmissionBlockedError(RuntimeError):
    """Raised when the only permitted P1-4a admission cannot be proven."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _table_rows_hash(connection: sqlite3.Connection, table: str, where: str = "", values: tuple[object, ...] = ()) -> str:
    cursor = connection.execute(f"SELECT * FROM {table} {where} ORDER BY rowid", values)
    names = [item[0] for item in cursor.description]
    rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    return sha256(_canonical(rows).encode("utf-8")).hexdigest().upper()


def _question_row(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM questions WHERE id=?", (QUESTION_ID,)).fetchone()
    if row is None:
        raise P14AAdmissionBlockedError("q013_missing")
    return dict(row)


def _delivery_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in DELIVERY_TABLES}


def _latest_verification_statuses(connection: sqlite3.Connection) -> tuple[str | None, dict[str, str]]:
    snapshot = connection.execute(
        "SELECT input_hash, invalidated_at FROM question_input_snapshots WHERE question_id=?", (QUESTION_ID,)
    ).fetchone()
    if snapshot is None or snapshot[0] is None or snapshot[1] is not None:
        return None, {}
    input_hash = str(snapshot[0])
    latest: dict[str, str] = {}
    for row in connection.execute(
        """SELECT verification_type, status FROM question_verifications
             WHERE question_id=? AND input_hash=?
             ORDER BY verified_at ASC, rowid ASC""",
        (QUESTION_ID, input_hash),
    ):
        latest[str(row[0])] = str(row[1])
    return input_hash, latest


def _p1_3c_scope_is_current(connection: sqlite3.Connection) -> tuple[bool, dict[str, object]]:
    scope = validate_textbook_scope(connection, QUESTION_ID)
    evidence = scope.evidence if isinstance(scope.evidence, dict) else {}
    passes = evidence.get("passes") if isinstance(evidence, dict) else []
    has_p1_3c = any(
        isinstance(item, dict)
        and isinstance(item.get("p1_3c"), dict)
        and item["p1_3c"].get("status") == "pass"
        and item.get("textbook_id") == TEXTBOOK_ID
        and item.get("curriculum_node_id") == NODE_ID
        for item in passes if isinstance(passes, list)
    )
    return scope.status == "pass" and has_p1_3c, {
        "status": scope.status,
        "evidence": scope.evidence,
        "has_current_p1_3c_path": has_p1_3c,
    }


def _preflight(connection: sqlite3.Connection) -> dict[str, object]:
    present = {
        str(row[0]) for row in connection.execute(
            "SELECT version FROM schema_migrations WHERE version IN (?, ?)", REQUIRED_PRIOR_MIGRATIONS
        )
    }
    missing = [item for item in REQUIRED_PRIOR_MIGRATIONS if item not in present]
    if missing:
        raise P14AAdmissionBlockedError("required_prior_migration_missing:" + ",".join(missing))
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise P14AAdmissionBlockedError("sqlite_integrity_check_failed")
    question = _question_row(connection)
    if (question["quality_status"], question["review_status"]) not in {
        ("blocked", "pending"),
        ("approved", "approved"),
    }:
        raise P14AAdmissionBlockedError("q013_status_not_blocked_pending_or_approved")
    mapping = connection.execute(
        """SELECT fit_status FROM question_textbooks
             WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
        (QUESTION_ID, TEXTBOOK_ID, NODE_ID),
    ).fetchone()
    if mapping is None or mapping[0] != "approved":
        raise P14AAdmissionBlockedError("q013_target_mapping_not_currently_approved")
    scope_current, scope = _p1_3c_scope_is_current(connection)
    if not scope_current:
        raise P14AAdmissionBlockedError("q013_current_p1_3c_scope_evidence_missing")
    content = connection.execute(
        """SELECT DISTINCT s.content_type, s.layer
             FROM current_controlled_content_segments s
             WHERE s.textbook_id=? AND s.curriculum_node_id=?""",
        (TEXTBOOK_ID, NODE_ID),
    ).fetchall()
    if {tuple(row) for row in content} != {
        ("knowledge_explanation", "public_core"),
        ("consolidation_practice", "basic_reinforcement"),
    }:
        raise P14AAdmissionBlockedError("required_current_controlled_content_missing_or_drifted")
    delivery_counts = _delivery_counts(connection)
    if any(delivery_counts.values()):
        raise P14AAdmissionBlockedError("delivery_related_tables_not_empty")
    return {
        "question": question,
        "mapping_fit_status": str(mapping[0]),
        "p1_3c_scope": scope,
        "delivery_counts": delivery_counts,
        "v2_30_already_applied": connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)
        ).fetchone() is not None,
    }


def _mutation_snapshot(connection: sqlite3.Connection) -> dict[str, object]:
    return {
        "protected_tables": {table: _table_rows_hash(connection, table) for table in PROTECTED_TABLES},
        "questions_except_q013": _table_rows_hash(connection, "questions", "WHERE id<>?", (QUESTION_ID,)),
        "q013": _question_row(connection),
        "snapshots_except_q013": _table_rows_hash(
            connection, "question_input_snapshots", "WHERE question_id<>?", (QUESTION_ID,)
        ),
        "q013_snapshot": _table_rows_hash(
            connection, "question_input_snapshots", "WHERE question_id=?", (QUESTION_ID,)
        ),
        "verifications_except_q013": _table_rows_hash(
            connection, "question_verifications", "WHERE question_id<>?", (QUESTION_ID,)
        ),
        "q013_verification_count": int(connection.execute(
            "SELECT COUNT(*) FROM question_verifications WHERE question_id=?", (QUESTION_ID,)
        ).fetchone()[0]),
        "q013_verifications_before": _table_rows_hash(
            connection, "question_verifications", "WHERE question_id=?", (QUESTION_ID,)
        ),
        "migrations": _table_rows_hash(connection, "schema_migrations"),
    }


def _assert_mutation_scope(connection: sqlite3.Connection, before: dict[str, object]) -> dict[str, object]:
    changed = [
        table for table, before_hash in dict(before["protected_tables"]).items()
        if _table_rows_hash(connection, table) != before_hash
    ]
    if changed:
        raise P14AAdmissionBlockedError("protected_tables_changed:" + ",".join(changed))
    if _table_rows_hash(connection, "questions", "WHERE id<>?", (QUESTION_ID,)) != before["questions_except_q013"]:
        raise P14AAdmissionBlockedError("non_q013_questions_changed")
    previous = dict(before["q013"])
    current = _question_row(connection)
    for key, value in previous.items():
        if key not in {"quality_status", "review_status"} and current.get(key) != value:
            raise P14AAdmissionBlockedError("q013_non_admission_field_changed:" + key)
    if (current["quality_status"], current["review_status"]) != ("approved", "approved"):
        raise P14AAdmissionBlockedError("q013_not_approved_after_verification")
    if _table_rows_hash(connection, "question_input_snapshots", "WHERE question_id<>?", (QUESTION_ID,)) != before["snapshots_except_q013"]:
        raise P14AAdmissionBlockedError("non_q013_input_snapshots_changed")
    if _table_rows_hash(connection, "question_verifications", "WHERE question_id<>?", (QUESTION_ID,)) != before["verifications_except_q013"]:
        raise P14AAdmissionBlockedError("non_q013_verifications_changed")
    original_count = int(before["q013_verification_count"])
    prior_hash = _table_rows_hash(
        connection,
        "question_verifications",
        "WHERE question_id=? AND rowid IN (SELECT rowid FROM question_verifications WHERE question_id=? ORDER BY rowid LIMIT ?)",
        (QUESTION_ID, QUESTION_ID, original_count),
    )
    if prior_hash != before["q013_verifications_before"]:
        raise P14AAdmissionBlockedError("existing_q013_verifications_changed")
    migration_rows = connection.execute(
        "SELECT version FROM schema_migrations WHERE version=?", (MIGRATION,)
    ).fetchall()
    if len(migration_rows) != 1:
        raise P14AAdmissionBlockedError("v2_30_migration_not_exactly_once")
    return {
        "question": current,
        "current_input_hash": _latest_verification_statuses(connection)[0],
        "current_verifications": _latest_verification_statuses(connection)[1],
    }


def _assert_final_admission(connection: sqlite3.Connection) -> dict[str, object]:
    scope_current, scope = _p1_3c_scope_is_current(connection)
    if not scope_current:
        raise P14AAdmissionBlockedError("p1_3c_scope_not_current_after_verification")
    input_hash, statuses = _latest_verification_statuses(connection)
    if not input_hash or any(statuses.get(item) != "pass" for item in REQUIRED_VERIFICATION_TYPES):
        raise P14AAdmissionBlockedError("five_current_verification_passes_missing")
    try:
        current_hash = compute_current_input_hash(connection, QUESTION_ID)
    except (ValueError, sqlite3.Error) as exc:
        raise P14AAdmissionBlockedError("q013_current_input_hash_unavailable") from exc
    if input_hash != current_hash:
        raise P14AAdmissionBlockedError("q013_current_input_snapshot_stale")
    admission = evaluate_automatic_admission(connection, QUESTION_ID)
    if not admission.eligible:
        raise P14AAdmissionBlockedError("q013_automatic_admission_not_current:" + ",".join(admission.blockers))
    delivery_counts = _delivery_counts(connection)
    if any(delivery_counts.values()):
        raise P14AAdmissionBlockedError("delivery_related_tables_not_empty_after_admission")
    return {
        "scope": scope,
        "input_hash": input_hash,
        "verification_statuses": statuses,
        "admission": {
            "eligible": admission.eligible,
            "missing_or_nonpassing": list(admission.missing_or_nonpassing),
            "blockers": list(admission.blockers),
        },
        "delivery_counts": delivery_counts,
    }


def _sqlite_backup(source_path: Path, destination: Path) -> dict[str, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.commit()
        integrity = str(target.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        target.close()
        source.close()
    if integrity != "ok":
        raise P14AAdmissionBlockedError("sqlite_backup_integrity_check_failed:" + integrity)
    return {"path": str(destination.resolve()), "sha256": _sha256(destination), "integrity_check": integrity}


def _run_verifier(database: Path) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "automatic_verification_runner.py"), "--db", str(database), "--question-id", QUESTION_ID],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    result = {
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
    if completed.returncode != 0:
        raise P14AAdmissionBlockedError("automatic_verification_runner_failed:" + _canonical(result))
    return result


def _write_report(path: Path, report: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        staged = Path(handle.name)
        json.dump(report, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    staged.replace(path)
    return path


def run_q013_admission(
    database: str | Path = DEFAULT_DATABASE,
    *,
    report_path: str | Path = DEFAULT_REPORT,
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
) -> dict[str, object]:
    """Perform the P1-4a development-DB admission only after full rehearsal.

    A precondition or stage verification failure returns a blocked report and
    leaves ``database`` byte-for-byte unchanged.  Successful mutation is an
    atomic replacement from the fully validated staging copy.
    """
    database_path = Path(database).resolve()
    destination = Path(report_path).resolve()
    backups = Path(backup_dir).resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"development_database_missing:{database_path}")
    before_hash = _sha256(database_path)
    report: dict[str, object] = {
        "schema": "p1-4a-q013-controlled-automatic-admission-v1",
        "purpose": "q013_only_development_database_admission_without_delivery",
        "database": {"path": str(database_path), "sha256_before": before_hash, "sha256_after": None},
        "question_id": QUESTION_ID,
        "target": {"textbook_id": TEXTBOOK_ID, "curriculum_node_id": NODE_ID},
        "delivery_tables": {},
        "prohibited_actions_performed": [],
    }
    try:
        source = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            preflight = _preflight(source)
            report["preflight"] = preflight
            if tuple(preflight["question"].get(key) for key in ("quality_status", "review_status")) == ("approved", "approved"):
                final = _assert_final_admission(source)
                report.update({
                    "action": "reused_existing_q013_admission",
                    "database_changes_committed": False,
                    "final": final,
                    "delivery_tables": final["delivery_counts"],
                })
                report["database"]["sha256_after"] = _sha256(database_path)
                _write_report(destination, report)
                return report
            before_snapshot = _mutation_snapshot(source)
        finally:
            source.close()

        backup = _sqlite_backup(database_path, backups / f"{database_path.stem}.before-p1-4a.{before_hash[:16]}.db")
        report["backup"] = backup
        with tempfile.TemporaryDirectory(prefix="p1-4a-stage-", dir=database_path.parent) as stage_root:
            stage = Path(stage_root) / database_path.name
            _sqlite_backup(database_path, stage)
            applied = subprocess.run(
                [sys.executable, str(ROOT / "apply_schema_v2_30.py"), str(stage)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            report["migration"] = {
                "returncode": applied.returncode,
                "stdout": applied.stdout.strip(),
                "stderr": applied.stderr.strip(),
            }
            if applied.returncode != 0:
                raise P14AAdmissionBlockedError("v2_30_migration_failed:" + _canonical(report["migration"]))
            report["verification_runner"] = _run_verifier(stage)
            staged_connection = sqlite3.connect(stage)
            staged_connection.row_factory = sqlite3.Row
            try:
                mutation = _assert_mutation_scope(staged_connection, before_snapshot)
                final = _assert_final_admission(staged_connection)
            finally:
                staged_connection.close()
            stage_hash = _sha256(stage)
            if _sha256(database_path) != before_hash:
                raise P14AAdmissionBlockedError("development_database_changed_during_staging")
            os.replace(stage, database_path)
        after_hash = _sha256(database_path)
        if after_hash != stage_hash:
            raise P14AAdmissionBlockedError("atomic_stage_replacement_hash_mismatch")
        report.update({
            "action": "approved_q013_after_rehearsed_automatic_verification",
            "database_changes_committed": True,
            "mutation": mutation,
            "final": final,
            "delivery_tables": final["delivery_counts"],
        })
        report["database"]["sha256_after"] = after_hash
    except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired, P14AAdmissionBlockedError) as exc:
        report.update({
            "action": "blocked_q013_admission",
            "database_changes_committed": False,
            "blockers": [f"{type(exc).__name__}:{exc}"],
        })
        report["database"]["sha256_after"] = _sha256(database_path)
        report["database"]["unchanged_after_block"] = report["database"]["sha256_after"] == before_hash
    _write_report(destination, report)
    return report

