"""Run the P1-4b answer-evidence derivation only on a disposable database."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import json
import shutil
import sqlite3

from apply_schema_v2_31 import MIGRATION, apply
from controlled_question_source_derivation import import_discovery
from p1_4b_controlled_source_discovery import build_p1_4b_source_discovery


ROOT = Path(__file__).resolve().parent
LIVE_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
P1_2F_BASELINE_DATABASE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)
DEFAULT_REPORT = ROOT / "output" / "audits" / "p1-4b_isolated_source_derivation_audit.json"
SCHEMA = "p1-4b-isolated-question-source-derivation-audit-v1"
NO_DELIVERY_TABLES = (
    "questions",
    "question_textbooks",
    "question_knowledge_points",
    "selection_plans",
    "teaching_documents",
    "document_questions",
    "quality_reports",
    "question_usage",
)
LIVE_COUNTS = (
    "questions",
    "teaching_documents",
    "document_questions",
    "quality_reports",
    "question_usage",
)


class P14BIsolatedAuditError(RuntimeError):
    """Raised when the isolated derivation fails a safety invariant."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _counts(connection: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}


def _live_state(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            "sha256": _sha256(path),
            "counts": _counts(connection, LIVE_COUNTS),
            "v2_31_applied": connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)
            ).fetchone()[0]
            == 1,
        }
    finally:
        connection.close()


def _ranges_are_disjoint(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        """SELECT e.source_character_range_json, s.source_character_range_json
             FROM current_controlled_question_answer_evidence e
             JOIN current_controlled_content_segments s
               ON s.source_id=e.controlled_source_id
              AND s.textbook_id=e.textbook_id
              AND s.curriculum_node_id=e.curriculum_node_id"""
    ).fetchall()
    for answer_range_json, student_range_json in rows:
        answer_start, answer_end = json.loads(answer_range_json)
        student_start, student_end = json.loads(student_range_json)
        if answer_start < student_end and answer_end > student_start:
            return False
    return True


def run_isolated_source_derivation(
    *, report_path: str | Path = DEFAULT_REPORT,
    live_database: str | Path = LIVE_DATABASE,
    baseline_database: str | Path = P1_2F_BASELINE_DATABASE,
) -> dict[str, object]:
    """Create audit evidence without applying v2.31 to the development database."""
    live = Path(live_database).resolve()
    baseline = Path(baseline_database).resolve()
    if not live.is_file() or not baseline.is_file():
        raise P14BIsolatedAuditError("required_database_missing")
    live_before = _live_state(live)
    with TemporaryDirectory(prefix="p1-4b-isolated-") as directory:
        isolated = Path(directory) / "development-copy.db"
        shutil.copy2(baseline, isolated)
        apply(isolated)
        connection = sqlite3.connect(isolated)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            before = _counts(connection, NO_DELIVERY_TABLES)
            discovery = build_p1_4b_source_discovery(isolated)
            result = import_discovery(connection, discovery)
            after = _counts(connection, NO_DELIVERY_TABLES)
            evidence = [
                dict(row)
                for row in connection.execute(
                    """SELECT source_question_no, field_name, source_character_range_json,
                              normalized_text_sha256, internal_only, evidence_hash
                         FROM current_controlled_question_answer_evidence
                        ORDER BY field_name"""
                )
            ]
            derivations = [
                dict(row)
                for row in connection.execute(
                    """SELECT source_question_no, student_prompt_range_json, student_prompt_sha256,
                              derivation_hash
                         FROM current_controlled_question_source_derivations"""
                )
            ]
            if result["status"] != "validated_internal_evidence_only":
                raise P14BIsolatedAuditError("isolated_import_not_validated_internal_evidence_only")
            if before != after:
                raise P14BIsolatedAuditError("isolated_import_changed_question_or_delivery_tables")
            if len(evidence) != 2 or {item["field_name"] for item in evidence} != {"answer", "analysis"}:
                raise P14BIsolatedAuditError("isolated_answer_evidence_incomplete")
            if len(derivations) != 1 or not _ranges_are_disjoint(connection):
                raise P14BIsolatedAuditError("isolated_student_answer_separation_not_proven")
            migration_applied = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)
            ).fetchone()[0] == 1
        finally:
            connection.close()
        isolated_after = _sha256(isolated)
    live_after = _live_state(live)
    if live_before != live_after:
        raise P14BIsolatedAuditError("isolated_audit_changed_live_database")
    report: dict[str, object] = {
        "schema": SCHEMA,
        "purpose": "verify_internal_answer_evidence_separation_on_disposable_database_only",
        "decision": "validated_internal_evidence_only_still_not_question_or_student_document_authorization",
        "live_database": {
            "path": str(live),
            "sha256_before": live_before["sha256"],
            "sha256_after": live_after["sha256"],
            "unchanged": True,
            "v2_31_applied": live_after["v2_31_applied"],
            "counts": live_after["counts"],
        },
        "isolated_database": {
            "baseline_path": str(baseline),
            "migration_applied": migration_applied,
            "sha256_changed_by_isolated_migration_and_evidence_import": _sha256(baseline) != isolated_after,
            "protected_counts_before": before,
            "protected_counts_after": after,
            "protected_counts_unchanged": before == after,
        },
        "internal_answer_evidence": evidence,
        "derivations": derivations,
        "student_answer_separation_proven": True,
        "question_import_authorized": False,
        "student_document_generation_authorized": False,
    }
    destination = Path(report_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report
