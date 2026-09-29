"""Read-only readiness audit for the first student lecture pilot.

The audit deliberately distinguishes a textbook mapping approval from a
question admission and from a student-document delivery.  It is a decision
tool for the P1-4 implementation step and makes no database writes.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import sqlite3

from automatic_gate import evaluate_automatic_admission
from textbook_scope_validator import validate as validate_textbook_scope


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
SCHEMA = "p1-4-first-student-lecture-readiness-audit-v1"
TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
CURRENT_NODE_ID = "bsd-math-8x-2026-node-01-section-02"
PRIOR_NODE_ID = "bsd-math-8x-2026-node-01-section-01"
QUESTION_ID = "golden-q013"
P13C_ADMISSION_MIGRATION = "v2.30-p14-current-p13c-question-admission-path"
REQUIRED_CONTENT_TYPES = {
    ("knowledge_explanation", "public_core"),
    ("consolidation_practice", "basic_reinforcement"),
}
REQUIRED_QUESTION_ROLES = (
    "activation_diagnostic",
    "public_core",
    "basic_reinforcement",
    "standard_extension",
    "challenge_extension",
    "transfer",
)
QUESTION_ROLE_EVIDENCE_TABLE = "question_pedagogical_role_evidence"
QUESTION_ROLE_EVIDENCE_VIEW = "current_question_pedagogical_role_evidence"
DELIVERY_TABLES = (
    "teaching_documents",
    "document_questions",
    "quality_reports",
    "question_usage",
)


class P14ReadinessAuditError(RuntimeError):
    """Raised when the audit cannot inspect the minimum pilot contracts."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?", (table,)
    ).fetchone() is not None


def _file_current(path: object, expected_hash: object) -> dict[str, object]:
    candidate = Path(str(path or ""))
    result: dict[str, object] = {
        "path": str(candidate),
        "exists": candidate.is_file(),
        "readable": False,
        "matches_expected": False,
        "sha256": None,
    }
    if not candidate.is_file():
        return result
    try:
        actual = _sha256(candidate)
    except OSError:
        return result
    result["readable"] = True
    result["sha256"] = actual
    result["matches_expected"] = isinstance(expected_hash, str) and actual.casefold() == expected_hash.casefold()
    return result


def _connect_readonly(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise P14ReadinessAuditError("development_database_missing")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _controlled_content(connection: sqlite3.Connection) -> dict[str, object]:
    required_tables = (
        "current_controlled_content_segments",
        "current_controlled_content_source_revisions",
        "controlled_content_sources",
    )
    if not all(_table_exists(connection, table) for table in required_tables):
        return {"ready": False, "rows": [], "reason": "controlled_content_revision_contract_missing"}
    rows = connection.execute(
        """SELECT s.id AS segment_row_id, s.content_type, s.layer, s.textbook_id,
                  s.curriculum_node_id, src.original_path, src.original_sha256,
                  src.archive_path, src.archive_sha256, src.converted_path,
                  src.converted_sha256, r.id AS source_revision_id,
                  r.revision_hash, h.head_revision
             FROM current_controlled_content_segments s
             JOIN controlled_content_sources src ON src.id=s.source_id
             JOIN current_controlled_content_source_revisions r ON r.segment_row_id=s.id
             JOIN controlled_content_source_revision_heads h ON h.current_revision_id=r.id
            WHERE s.textbook_id=? AND s.curriculum_node_id=?
            ORDER BY s.content_type, s.layer, s.id""",
        (TEXTBOOK_ID, CURRENT_NODE_ID),
    ).fetchall()
    result_rows: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["original"] = _file_current(row["original_path"], row["original_sha256"])
        item["archive"] = _file_current(row["archive_path"], row["archive_sha256"])
        item["converted"] = _file_current(row["converted_path"], row["converted_sha256"])
        result_rows.append(item)
    identities = {(str(row["content_type"]), str(row["layer"])) for row in result_rows}
    ready = identities == REQUIRED_CONTENT_TYPES and all(
        all(bool(item[name]["matches_expected"]) for name in ("original", "archive", "converted"))
        for item in result_rows
    )
    return {"ready": ready, "rows": result_rows, "reason": None if ready else "controlled_content_not_current_or_incomplete"}


def _current_p1_3c_admission_path(
    connection: sqlite3.Connection,
    *,
    scope_status: str,
    scope_evidence: object,
) -> dict[str, object]:
    """Describe whether the P1-3c mapping chain can replace body evidence.

    ``textbook_scope_validator`` already recomputes the v2 approval hash and
    checks the current source revision, approval evidence and automatic audit.
    This audit only identifies that proven route and whether v2.30 has enabled
    it at the database approval trigger; it does not recreate that predicate.
    """
    migration_applied = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (P13C_ADMISSION_MIGRATION,)
    ).fetchone() is not None
    p1_3c_passes: list[dict[str, object]] = []
    if isinstance(scope_evidence, dict):
        raw_passes = scope_evidence.get("passes")
        if isinstance(raw_passes, list):
            p1_3c_passes = [
                item for item in raw_passes
                if isinstance(item, dict) and isinstance(item.get("p1_3c"), dict)
                and item["p1_3c"].get("status") == "pass"
            ]
    current = scope_status == "pass" and bool(p1_3c_passes)
    return {
        "current": current,
        "migration_applied": migration_applied,
        "usable_for_question_admission": current and migration_applied,
        "reason": (
            None if current and migration_applied
            else "p1_3c_current_mapping_evidence_missing"
            if not current
            else "p1_4_p13c_admission_migration_not_applied"
        ),
        "mapping_count": len(p1_3c_passes),
        "mapping_evidence": [item["p1_3c"] for item in p1_3c_passes],
    }


def _q013_mapping_and_admission(connection: sqlite3.Connection) -> dict[str, object]:
    row = connection.execute(
        """SELECT q.id, q.quality_status, q.review_status, qt.fit_status
             FROM questions q
             LEFT JOIN question_textbooks qt
               ON qt.question_id=q.id AND qt.textbook_id=? AND qt.curriculum_node_id=?
            WHERE q.id=?""",
        (TEXTBOOK_ID, CURRENT_NODE_ID, QUESTION_ID),
    ).fetchone()
    if row is None:
        return {"exists": False, "mapping": None, "admission": None, "body_evidence": None}
    mapping: dict[str, object] = dict(row)
    scope = validate_textbook_scope(connection, QUESTION_ID)
    mapping["textbook_scope"] = {"status": scope.status, "evidence": scope.evidence}
    p1_3c_path = _current_p1_3c_admission_path(
        connection,
        scope_status=scope.status,
        scope_evidence=scope.evidence,
    )
    try:
        admission = asdict(evaluate_automatic_admission(connection, QUESTION_ID))
    except (sqlite3.Error, ValueError) as exc:
        admission = {"eligible": False, "missing_or_nonpassing": [], "blockers": [f"admission_unavailable:{exc}"]}
    body_evidence: dict[str, object]
    if all(_table_exists(connection, table) for table in ("textbook_sources", "textbook_body_units", "question_body_unit_mappings")):
        body_evidence = {
            "active_source_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM textbook_sources WHERE textbook_id=? AND status='active'",
                    (TEXTBOOK_ID,),
                ).fetchone()[0]
            ),
            "active_question_mapping_count": int(
                connection.execute(
                    """SELECT COUNT(*)
                         FROM question_body_unit_mappings qbm
                         JOIN textbook_body_unit_snapshots s ON s.body_unit_id=qbm.body_unit_id
                         JOIN textbook_body_units bu ON bu.id=qbm.body_unit_id
                         JOIN textbook_sources ts ON ts.id=bu.textbook_source_id
                        WHERE qbm.question_id=? AND qbm.status='active'
                          AND s.invalidated_at IS NULL AND ts.status='active'
                          AND qbm.source_file_hash=ts.source_file_hash""",
                    (QUESTION_ID,),
                ).fetchone()[0]
            ),
        }
    else:
        body_evidence = {"active_source_count": 0, "active_question_mapping_count": 0}
    if body_evidence["active_question_mapping_count"]:
        admission_evidence_path = "active_textbook_body_evidence"
    elif p1_3c_path["usable_for_question_admission"]:
        admission_evidence_path = "current_p1_3c_mapping_evidence"
    else:
        admission_evidence_path = "none"
    return {
        "exists": True,
        "mapping": mapping,
        "admission": admission,
        "body_evidence": body_evidence,
        "p1_3c_admission_path": p1_3c_path,
        "admission_evidence_path": admission_evidence_path,
    }


def _pilot_progress_contracts(connection: sqlite3.Connection) -> dict[str, object]:
    required_tables = ("classes", "class_progress_controls", "class_progress_allowed_nodes")
    if not all(_table_exists(connection, table) for table in required_tables):
        return {"ready": False, "contracts": [], "reason": "pilot_progress_storage_contract_missing"}
    rows = connection.execute(
        """SELECT c.id AS class_id, c.name, c.student_profile_json, c.default_lesson_minutes,
                  p.id AS progress_id, p.current_curriculum_node_id, p.allowed_nodes_json
             FROM classes c
             JOIN class_progress_controls p ON p.class_id=c.id AND p.status='active'
            WHERE c.status='active' AND c.textbook_id=?
            ORDER BY c.id, p.id""",
        (TEXTBOOK_ID,),
    ).fetchall()
    contracts: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        try:
            profile = json.loads(str(row["student_profile_json"] or "{}"))
        except json.JSONDecodeError:
            profile = {}
        try:
            allowed = tuple(str(value) for value in json.loads(str(row["allowed_nodes_json"])))
        except (TypeError, json.JSONDecodeError):
            allowed = ()
        item["is_virtual"] = isinstance(profile, dict) and profile.get("is_virtual") is True
        item["allowed_nodes"] = list(allowed)
        item["matches_pilot"] = (
            item["is_virtual"]
            and row["current_curriculum_node_id"] == CURRENT_NODE_ID
            and {PRIOR_NODE_ID, CURRENT_NODE_ID}.issubset(set(allowed))
            and int(row["default_lesson_minutes"] or 0) == 120
        )
        contracts.append(item)
    ready = any(bool(item["matches_pilot"]) for item in contracts)
    return {"ready": ready, "contracts": contracts, "reason": None if ready else "virtual_pilot_class_and_progress_contract_missing"}


def _approved_question_supply(connection: sqlite3.Connection) -> dict[str, object]:
    rows = connection.execute(
        """SELECT q.id, q.question_type, q.difficulty, qt.curriculum_node_id
             FROM questions q
             JOIN question_textbooks qt ON qt.question_id=q.id
            WHERE q.quality_status='approved' AND q.review_status='approved'
              AND qt.fit_status='approved' AND qt.textbook_id=?
              AND qt.curriculum_node_id IN (?, ?)
            ORDER BY q.id""",
        (TEXTBOOK_ID, PRIOR_NODE_ID, CURRENT_NODE_ID),
    ).fetchall()
    eligible: list[dict[str, object]] = []
    ineligible: list[dict[str, object]] = []
    for row in rows:
        question_id = str(row["id"])
        admission = evaluate_automatic_admission(connection, question_id)
        item = {
            "question_id": question_id,
            "question_type": row["question_type"],
            "difficulty": row["difficulty"],
            "curriculum_node_id": row["curriculum_node_id"],
        }
        if admission.eligible:
            eligible.append(item)
        else:
            ineligible.append({
                **item,
                "missing_or_nonpassing": list(admission.missing_or_nonpassing),
                "blockers": list(admission.blockers),
            })
    return {
        "count": len(eligible),
        "question_ids": [str(item["question_id"]) for item in eligible],
        "eligible_questions": eligible,
        "ineligible_questions": ineligible,
    }


def _pedagogical_role_supply(
    connection: sqlite3.Connection,
    approved_supply: dict[str, object],
) -> dict[str, object]:
    """Inspect explicit question-role evidence; labels are never inferred.

    The existing source ``difficulty`` field and filenames are intentionally
    not used as pedagogical roles.  A later import may provide the named table,
    but until then the report must expose the missing contract rather than
    silently allocating approved questions to G1/G4/G5/G6.
    """
    eligible_ids = tuple(str(value) for value in approved_supply["question_ids"])
    if not _table_exists(connection, QUESTION_ROLE_EVIDENCE_TABLE):
        return {
            "contract_available": False,
            "required_roles": list(REQUIRED_QUESTION_ROLES),
            "assignments": {role: [] for role in REQUIRED_QUESTION_ROLES},
            "reason": "question_pedagogical_role_evidence_contract_missing",
            "unallocated_eligible_question_ids": list(eligible_ids),
        }
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({QUESTION_ROLE_EVIDENCE_TABLE})")}
    required_columns = {
        "question_id", "role", "status", "source_document_id", "source_fragment_id",
        "source_hash", "input_hash", "expected_minutes", "evidence_hash",
    }
    if not required_columns.issubset(columns):
        return {
            "contract_available": False,
            "required_roles": list(REQUIRED_QUESTION_ROLES),
            "assignments": {role: [] for role in REQUIRED_QUESTION_ROLES},
            "reason": "question_pedagogical_role_evidence_columns_invalid",
            "unallocated_eligible_question_ids": list(eligible_ids),
        }
    if not _table_exists(connection, QUESTION_ROLE_EVIDENCE_VIEW):
        return {
            "contract_available": False,
            "required_roles": list(REQUIRED_QUESTION_ROLES),
            "assignments": {role: [] for role in REQUIRED_QUESTION_ROLES},
            "reason": "question_pedagogical_role_evidence_current_view_missing",
            "unallocated_eligible_question_ids": list(eligible_ids),
        }
    placeholders = ",".join("?" for _ in eligible_ids)
    rows = [] if not eligible_ids else connection.execute(
        f"""SELECT question_id, role
              FROM {QUESTION_ROLE_EVIDENCE_VIEW}
             WHERE question_id IN ({placeholders})
             ORDER BY role, question_id""",
        eligible_ids,
    ).fetchall()
    assignments = {role: [] for role in REQUIRED_QUESTION_ROLES}
    for row in rows:
        role = str(row["role"])
        if role in assignments:
            assignments[role].append(str(row["question_id"]))
    assigned = {question_id for values in assignments.values() for question_id in values}
    return {
        "contract_available": True,
        "required_roles": list(REQUIRED_QUESTION_ROLES),
        "assignments": assignments,
        "reason": None,
        "unallocated_eligible_question_ids": [item for item in eligible_ids if item not in assigned],
    }


def _example_and_summary_supply(content: dict[str, object]) -> dict[str, object]:
    rows = content.get("rows") if isinstance(content, dict) else []
    identities = {
        (str(item.get("content_type")), str(item.get("layer")))
        for item in rows if isinstance(item, dict)
    } if isinstance(rows, list) else set()
    return {
        "controlled_example_ready": ("worked_example", "public_core") in identities,
        "source_bound_summary_and_self_assessment_ready": (
            ("summary_self_assessment", "public_core") in identities
        ),
        "available_content_identities": [list(item) for item in sorted(identities)],
    }


def _delivery_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in DELIVERY_TABLES
        if _table_exists(connection, table)
    }


def build_p1_4_readiness_audit(database_path: str | Path) -> dict[str, object]:
    """Read all current pilot facts and report the next blocking conditions."""
    database = Path(database_path).resolve()
    before_hash = _sha256(database)
    connection = _connect_readonly(database)
    try:
        content = _controlled_content(connection)
        q013 = _q013_mapping_and_admission(connection)
        progress = _pilot_progress_contracts(connection)
        supply = _approved_question_supply(connection)
        role_supply = _pedagogical_role_supply(connection, supply)
        auxiliary_content = _example_and_summary_supply(content)
        delivery_counts = _delivery_counts(connection)
        blockers: list[str] = []
        if not content["ready"]:
            blockers.append(str(content["reason"]))
        if not progress["ready"]:
            blockers.append(str(progress["reason"]))
        if supply["count"] == 0:
            blockers.append("approved_question_supply_zero")
        if not q013["exists"]:
            blockers.append("q013_missing")
        else:
            mapping = q013["mapping"]
            admission = q013["admission"]
            body = q013["body_evidence"]
            p1_3c_path = q013["p1_3c_admission_path"]
            if mapping["fit_status"] != "approved":
                blockers.append("q013_target_mapping_not_approved")
            if mapping["textbook_scope"]["status"] != "pass":
                blockers.append("q013_current_textbook_scope_not_pass")
            if not admission["eligible"]:
                blockers.append("q013_current_question_admission_not_pass")
            if not body["active_question_mapping_count"] and not p1_3c_path["usable_for_question_admission"]:
                blockers.append("q013_" + str(p1_3c_path["reason"]))
        role_assignments = role_supply["assignments"]
        prior_node_questions = [
            item for item in supply["eligible_questions"]
            if item["curriculum_node_id"] == PRIOR_NODE_ID
        ]
        section_reasons = {
            "G0_task_information": None if progress["ready"] else str(progress["reason"]),
            "G1_activation_diagnostic": (
                None if role_assignments["activation_diagnostic"]
                else "approved_activation_diagnostic_role_evidence_missing"
            ),
            "G2_knowledge_construction": None if content["ready"] else str(content["reason"]),
            "G3_examples_and_methods": (
                None if auxiliary_content["controlled_example_ready"]
                else "controlled_worked_example_source_missing"
            ),
            "G4_public_core": (
                None if role_assignments["public_core"]
                else "approved_public_core_role_evidence_missing"
            ),
            "G5_basic_reinforcement": (
                None if role_assignments["basic_reinforcement"]
                else "approved_basic_reinforcement_role_evidence_missing"
            ),
            "G5_standard_extension": (
                None if role_assignments["standard_extension"]
                else "approved_standard_extension_role_evidence_missing"
            ),
            "G5_challenge_extension": (
                None if role_assignments["challenge_extension"]
                else "approved_challenge_extension_role_evidence_missing"
            ),
            "G6_transfer": (
                None if role_assignments["transfer"]
                else "approved_transfer_role_evidence_missing"
            ),
            "G7_summary": (
                None if auxiliary_content["source_bound_summary_and_self_assessment_ready"]
                else "source_bound_summary_and_self_assessment_rule_missing"
            ),
        }
        if not role_supply["contract_available"]:
            section_reasons["G1_activation_diagnostic"] = str(role_supply["reason"])
            section_reasons["G4_public_core"] = str(role_supply["reason"])
            section_reasons["G5_basic_reinforcement"] = str(role_supply["reason"])
            section_reasons["G5_standard_extension"] = str(role_supply["reason"])
            section_reasons["G5_challenge_extension"] = str(role_supply["reason"])
            section_reasons["G6_transfer"] = str(role_supply["reason"])
        prior_node_ids = {str(item["question_id"]) for item in prior_node_questions}
        if not prior_node_ids.intersection(role_assignments["activation_diagnostic"]):
            section_reasons["G1_activation_diagnostic"] = "approved_prior_node_activation_supply_zero"
        sections = {
            "G0_task_information": "ready" if progress["ready"] else "blocked",
            "G1_activation_diagnostic": "ready" if section_reasons["G1_activation_diagnostic"] is None else "blocked",
            "G2_knowledge_construction": "ready" if content["ready"] else "blocked",
            "G3_examples_and_methods": "ready" if section_reasons["G3_examples_and_methods"] is None else "blocked",
            "G4_public_core": "ready" if section_reasons["G4_public_core"] is None else "blocked",
            "G5_basic_reinforcement": "ready" if section_reasons["G5_basic_reinforcement"] is None else "blocked",
            "G5_standard_extension": "ready" if section_reasons["G5_standard_extension"] is None else "blocked",
            "G5_challenge_extension": "ready" if section_reasons["G5_challenge_extension"] is None else "hidden_due_to_supply_shortage",
            "G6_transfer": "ready" if section_reasons["G6_transfer"] is None else "omitted_due_to_supply_shortage",
            "G7_summary": "ready" if section_reasons["G7_summary"] is None else "blocked",
        }
        for section in (
            "G1_activation_diagnostic",
            "G3_examples_and_methods",
            "G4_public_core",
            "G5_basic_reinforcement",
            "G5_standard_extension",
            "G7_summary",
        ):
            if sections[section] == "blocked":
                blockers.append(f"{section}:{section_reasons[section]}")
        report = {
            "schema": SCHEMA,
            "purpose": "read_only_p1_4_student_lecture_readiness_decision",
            "pilot": {
                "textbook_id": TEXTBOOK_ID,
                "current_curriculum_node_id": CURRENT_NODE_ID,
                "allowed_curriculum_node_ids": [PRIOR_NODE_ID, CURRENT_NODE_ID],
                "lesson_minutes": 120,
                "question_id_under_admission_review": QUESTION_ID,
            },
            "database": {
                "path": str(database),
                "sha256_before": before_hash,
                "sha256_after": None,
                "unchanged": None,
            },
            "controlled_content": content,
            "q013": q013,
            "virtual_pilot_progress": progress,
            "approved_question_supply": supply,
            "question_pedagogical_role_supply": role_supply,
            "auxiliary_controlled_content_supply": auxiliary_content,
            "section_readiness": sections,
            "section_reasons": section_reasons,
            "delivery_counts": delivery_counts,
            "student_document_generation_authorized": not blockers,
            "decision": "ready_for_student_document_generation" if not blockers else "blocked_p1_4_readiness_gap_report",
            "blockers": sorted(set(blockers)),
        }
    finally:
        connection.close()
    after_hash = _sha256(database)
    if before_hash != after_hash:
        raise P14ReadinessAuditError("read_only_readiness_audit_changed_database")
    report["database"]["sha256_after"] = after_hash
    report["database"]["unchanged"] = True
    return report


def write_p1_4_readiness_audit(report: dict[str, object], path: str | Path) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return destination
