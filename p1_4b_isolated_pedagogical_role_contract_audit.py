"""Audit the P1-4b pedagogical-role contract on a disposable database only."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import json
import shutil
import sqlite3

from apply_schema_v2_30 import apply as apply_v30
from apply_schema_v2_31 import apply as apply_v31
from apply_schema_v2_32 import MIGRATION, apply as apply_v32
from p1_4_pilot_readiness_audit import _pedagogical_role_supply


ROOT = Path(__file__).resolve().parent
LIVE_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
P1_2F_BASELINE_DATABASE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)
DEFAULT_REPORT = ROOT / "output" / "audits" / "p1-4b_isolated_pedagogical_role_contract_audit.json"
SCHEMA = "p1-4b-isolated-pedagogical-role-contract-audit-v1"
PROTECTED_TABLES = (
    "questions",
    "question_textbooks",
    "question_knowledge_points",
    "selection_plans",
    "teaching_documents",
    "document_questions",
    "quality_reports",
    "question_usage",
)
LIVE_TABLES = ("questions", "teaching_documents", "document_questions", "quality_reports", "question_usage")


class P14BRoleContractAuditError(RuntimeError):
    """Raised when the isolated zero-supply role-contract audit is unsafe."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _counts(connection: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}


def _live_state(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            "sha256": _sha256(path),
            "counts": _counts(connection, LIVE_TABLES),
            "v2_32_applied": connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)
            ).fetchone()[0]
            == 1,
        }
    finally:
        connection.close()


def run_isolated_pedagogical_role_contract_audit(
    *,
    report_path: str | Path = DEFAULT_REPORT,
    live_database: str | Path = LIVE_DATABASE,
    baseline_database: str | Path = P1_2F_BASELINE_DATABASE,
) -> dict[str, object]:
    """Prove the empty role supply remains blocked without a live DB migration."""
    live = Path(live_database).resolve()
    baseline = Path(baseline_database).resolve()
    if not live.is_file() or not baseline.is_file():
        raise P14BRoleContractAuditError("required_database_missing")
    live_before = _live_state(live)
    with TemporaryDirectory(prefix="p1-4b-role-contract-") as directory:
        isolated = Path(directory) / "development-copy.db"
        shutil.copy2(baseline, isolated)
        apply_v30(isolated)
        apply_v31(isolated)
        apply_v32(isolated)
        connection = sqlite3.connect(isolated)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            before = _counts(connection, PROTECTED_TABLES)
            supply = _pedagogical_role_supply(connection, {"question_ids": ["golden-q013"]})
            evidence_count = connection.execute(
                "SELECT COUNT(*) FROM question_pedagogical_role_evidence"
            ).fetchone()[0]
            current_evidence_count = connection.execute(
                "SELECT COUNT(*) FROM current_question_pedagogical_role_evidence"
            ).fetchone()[0]
            after = _counts(connection, PROTECTED_TABLES)
            migration_applied = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=?", (MIGRATION,)
            ).fetchone()[0] == 1
        finally:
            connection.close()
    live_after = _live_state(live)
    if live_before != live_after:
        raise P14BRoleContractAuditError("isolated_role_contract_audit_changed_live_database")
    if not migration_applied or before != after:
        raise P14BRoleContractAuditError("isolated_role_contract_audit_changed_protected_tables")
    if not bool(supply["contract_available"]) or evidence_count or current_evidence_count:
        raise P14BRoleContractAuditError("isolated_role_contract_empty_supply_invariant_failed")
    assignments = supply["assignments"]
    if any(assignments[role] for role in assignments) or supply["unallocated_eligible_question_ids"] != ["golden-q013"]:
        raise P14BRoleContractAuditError("isolated_role_contract_allocated_question_without_evidence")
    report: dict[str, object] = {
        "schema": SCHEMA,
        "purpose": "verify_current_pedagogical_role_contract_without_role_import_or_student_document_generation",
        "decision": "validated_empty_pedagogical_role_supply_still_blocks_layered_student_lecture",
        "live_database": {
            "path": str(live),
            "sha256_before": live_before["sha256"],
            "sha256_after": live_after["sha256"],
            "unchanged": True,
            "v2_32_applied": live_after["v2_32_applied"],
            "counts": live_after["counts"],
        },
        "isolated_database": {
            "baseline_path": str(baseline),
            "v2_32_applied": migration_applied,
            "protected_counts_before": before,
            "protected_counts_after": after,
            "protected_counts_unchanged": before == after,
        },
        "role_contract_available": supply["contract_available"],
        "role_assignments": assignments,
        "unallocated_eligible_question_ids": supply["unallocated_eligible_question_ids"],
        "role_evidence_count": evidence_count,
        "current_role_evidence_count": current_evidence_count,
        "question_import_authorized": False,
        "student_document_generation_authorized": False,
    }
    destination = Path(report_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report
