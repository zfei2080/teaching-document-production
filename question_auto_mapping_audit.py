from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
import re
from typing import Any

from input_snapshot import compute_current_input_hash, stable_hash


AUDITOR_ID = "question-auto-mapping-audit"
AUDITOR_VERSION = "2.0.0"
VALIDATOR_BUNDLE_ID = "question-auto-mapping-audit"
VALIDATOR_BUNDLE_VERSION = "2.0.0"
REQUIRED_VALIDATORS: tuple[str, ...] = (
    "source_fidelity",
    "structural_consistency",
    "mathematical_independent",
    "asset_semantics",
)
REQUIRED_VALIDATOR_SET = frozenset(REQUIRED_VALIDATORS)
AUDIT_STATUSES = frozenset({"pass", "unsupported", "fail"})

QUESTION_SIGNAL_RULES: tuple[dict[str, Any], ...] = (
    {
        "code": "triangle_centers_and_special_lines",
        "core_theme_keywords": ("直角三角形",),
        "signal_groups": (
            ("高线",),
            ("中线",),
            ("角平分线",),
        ),
        "required_knowledge_patterns": (
            ("高线",),
            ("中线",),
            ("角平分线",),
        ),
        "detail": "题干/选项/解析同时出现高线、中线、角平分线等多条特殊线信号，超出仅凭直角三角形基础性质可支撑的知识结构。",
    },
)


@dataclass(frozen=True)
class AutoMappingAuditRequest:
    evidence_id: str
    reuse_existing: bool = False


@dataclass(frozen=True)
class AutoMappingAuditResult:
    audit_id: str
    audit_status: str
    decision_basis: str
    findings: dict[str, Any]
    audit_json: dict[str, Any]
    reused_existing: bool = False


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validator_bundle_is_strict(results: dict[str, dict[str, Any]], input_hash: str) -> bool:
    if set(results.keys()) != REQUIRED_VALIDATOR_SET:
        return False
    for validator in REQUIRED_VALIDATORS:
        item = results.get(validator)
        if not isinstance(item, dict):
            return False
        validator_id = item.get("validator_id")
        validator_version = item.get("validator_version")
        if not isinstance(validator_id, str) or not validator_id.strip():
            return False
        if not isinstance(validator_version, str) or not validator_version.strip():
            return False
        if item.get("status") != "pass":
            return False
        if item.get("input_hash") != input_hash:
            return False
    return True


def _load_evidence(conn: sqlite3.Connection, evidence_id: str) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT e.id, e.question_id, e.textbook_id, e.curriculum_node_id,
               e.question_input_hash, e.question_snapshot_revision,
               e.textbook_catalog_version, e.node_catalog_version,
               e.feature_hash, e.features_json, e.decider_id, e.decider_version,
               e.status, e.confidence, e.reason, e.evidence_hash,
               CASE WHEN cur.id IS NULL THEN 0 ELSE 1 END AS is_current
        FROM question_curriculum_mapping_evidence e
        LEFT JOIN current_question_curriculum_mapping_evidence cur ON cur.id = e.id
        WHERE e.id=?
        """,
        (evidence_id,),
    ).fetchone()
    if row is None:
        raise ValueError("mapping evidence not found")
    return row


def _parse_features(features_json: str) -> dict[str, Any]:
    features = json.loads(features_json)
    if not isinstance(features, dict):
        raise ValueError("candidate features must be a JSON object")
    return features


def _feature_findings(features: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    core_theme = features.get("core_theme")
    required_knowledge = features.get("required_knowledge")
    required_knowledge_evidence = features.get("required_knowledge_evidence")
    if not isinstance(core_theme, str) or not core_theme.strip():
        findings.append("core_theme_missing")
    if not isinstance(required_knowledge, list) or not required_knowledge:
        findings.append("required_knowledge_missing")
    if not isinstance(required_knowledge_evidence, dict) or not required_knowledge_evidence:
        findings.append("required_knowledge_evidence_missing")
        return findings
    if isinstance(required_knowledge, list):
        for item in required_knowledge:
            if not isinstance(item, str) or not item.strip():
                findings.append("required_knowledge_invalid")
                continue
            evidence = required_knowledge_evidence.get(item)
            if not isinstance(evidence, dict):
                findings.append(f"required_knowledge_evidence_missing:{item}")
                continue
            if evidence.get("supported") is not True:
                findings.append(f"required_knowledge_unsupported:{item}")
    extra_keys = set(required_knowledge_evidence.keys()) - {item for item in required_knowledge if isinstance(item, str)}
    if extra_keys:
        findings.append("required_knowledge_evidence_mismatch")
    return findings


def _normalize_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "", value)


def _load_question_snapshot(conn: sqlite3.Connection, question_id: str) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT stem, options_json, answer, analysis, question_type
        FROM questions
        WHERE id=?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        raise ValueError("question not found for audit")
    return row


def _question_text(row: sqlite3.Row) -> str:
    parts = [_normalize_text(row["stem"]), _normalize_text(row["answer"]), _normalize_text(row["analysis"])]
    options_json = row["options_json"]
    if isinstance(options_json, str) and options_json.strip():
        try:
            options = json.loads(options_json)
        except json.JSONDecodeError:
            options = [options_json]
        if isinstance(options, list):
            parts.extend(_normalize_text(item) for item in options if isinstance(item, str))
        elif isinstance(options, str):
            parts.append(_normalize_text(options))
    return "\n".join(part for part in parts if part)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern and pattern in text for pattern in patterns)


def _question_signal_findings(question_text: str, features: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    core_theme = features.get("core_theme")
    required_knowledge = [item for item in features.get("required_knowledge", []) if isinstance(item, str)]
    required_knowledge_evidence = features.get("required_knowledge_evidence")
    for rule in QUESTION_SIGNAL_RULES:
        if isinstance(core_theme, str) and core_theme and not _matches_any(core_theme, tuple(rule["core_theme_keywords"])):
            continue
        if not all(_matches_any(question_text, tuple(group)) for group in rule["signal_groups"]):
            continue
        missing_required = []
        for pattern_group in rule["required_knowledge_patterns"]:
            covered = False
            for knowledge in required_knowledge:
                if _matches_any(knowledge, tuple(pattern_group)):
                    evidence = required_knowledge_evidence.get(knowledge) if isinstance(required_knowledge_evidence, dict) else None
                    if isinstance(evidence, dict) and evidence.get("supported") is True:
                        covered = True
                        break
            if not covered:
                missing_required.append("/".join(pattern_group))
        if missing_required:
            findings.append("required_knowledge_incomplete_against_question_signals")
            findings.append(f"question_signal_rule:{rule['code']}")
            findings.append("question_signal_missing_required:" + ",".join(missing_required))
    return findings


def _load_validator_rows(conn: sqlite3.Connection, question_id: str, input_hash: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT question_id, verification_type, validator_id, validator_version,
               status, computed_answer, evidence_json, input_hash, verified_at, id
        FROM question_verifications
        WHERE question_id=? AND input_hash=?
          AND verification_type IN ('source_fidelity', 'structural_consistency', 'mathematical_independent', 'asset_semantics')
        ORDER BY verification_type, verified_at DESC, id DESC
        """,
        (question_id, input_hash),
    ).fetchall()


def _evaluate_validators(rows: list[sqlite3.Row]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["verification_type"], []).append(row)
    findings: list[str] = []
    results: dict[str, dict[str, Any]] = {}
    for validator in REQUIRED_VALIDATORS:
        items = grouped.get(validator, [])
        if not items:
            findings.append(f"validator_missing:{validator}")
            continue
        statuses = {item["status"] for item in items}
        if statuses == {"pass"}:
            item = items[0]
            evidence_json = json.loads(item["evidence_json"])
            results[validator] = {
                "validator_id": item["validator_id"],
                "validator_version": item["validator_version"],
                "status": item["status"],
                "input_hash": item["input_hash"],
                "evidence": evidence_json,
                "verification_id": item["id"],
                "verified_at": item["verified_at"],
            }
            continue
        if "unsupported" in statuses:
            findings.append(f"validator_unsupported:{validator}")
        elif "fail" in statuses:
            findings.append(f"validator_fail:{validator}")
        else:
            findings.append(f"validator_nonpass:{validator}")
        if len(statuses) > 1:
            findings.append(f"validator_conflicting_status:{validator}")
    return results, findings


def _decision_basis(evidence: sqlite3.Row) -> str:
    if not evidence["is_current"]:
        return "stale_or_missing_current"
    if evidence["status"] == "candidate":
        return "current_candidate"
    return "non_candidate_current"


def _derive_status(evidence: sqlite3.Row, feature_findings: list[str], validator_findings: list[str], conflict_findings: list[str]) -> tuple[str, str]:
    if evidence["status"] != "candidate":
        return "unsupported", "non_candidate_evidence_cannot_pass"
    all_findings = feature_findings + validator_findings + conflict_findings
    if all_findings:
        return "unsupported", ";".join(all_findings)
    return "pass", ""


def record_auto_mapping_audit(conn: sqlite3.Connection, request: AutoMappingAuditRequest) -> AutoMappingAuditResult:
    evidence = _load_evidence(conn, request.evidence_id)
    basis = _decision_basis(evidence)
    current_input_hash = compute_current_input_hash(conn, evidence["question_id"])
    current_input_stale = current_input_hash != evidence["question_input_hash"]
    features = _parse_features(evidence["features_json"])
    feature_findings = _feature_findings(features)
    question_row = _load_question_snapshot(conn, evidence["question_id"])
    feature_findings.extend(_question_signal_findings(_question_text(question_row), features))
    validator_rows = _load_validator_rows(conn, evidence["question_id"], evidence["question_input_hash"])
    validator_results, validator_findings = _evaluate_validators(validator_rows)
    if validator_results and not _validator_bundle_is_strict(validator_results, evidence["question_input_hash"]):
        validator_findings.append("validator_bundle_invalid")
    candidate_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM current_question_curriculum_mapping_evidence
        WHERE question_id=? AND question_input_hash=? AND status='candidate'
        """,
        (evidence["question_id"], evidence["question_input_hash"]),
    ).fetchone()[0]
    conflict_findings: list[str] = []
    if basis == "current_candidate" and candidate_count != 1:
        conflict_findings.append("candidate_conflict")
    if current_input_stale:
        conflict_findings.append("question_input_hash_stale")
    if basis == "stale_or_missing_current":
        conflict_findings.append("evidence_not_current")
    audit_status, blocked_reason = _derive_status(evidence, feature_findings, validator_findings, conflict_findings)
    if basis != "current_candidate" and audit_status == "pass":
        audit_status = "fail"
        blocked_reason = "invalid_decision_basis_for_pass"
    findings = {
        "feature_findings": feature_findings,
        "validator_findings": validator_findings,
        "conflict_findings": conflict_findings,
        "candidate_evidence_id": evidence["id"],
        "candidate_status": evidence["status"],
        "candidate_is_current": bool(evidence["is_current"]),
        "decider_id": evidence["decider_id"],
        "decider_version": evidence["decider_version"],
        "candidate_reason": evidence["reason"],
    }
    audit_json = {
        "schema": "question-auto-mapping-audit-v2",
        "evidence_id": evidence["id"],
        "question_id": evidence["question_id"],
        "textbook_id": evidence["textbook_id"],
        "curriculum_node_id": evidence["curriculum_node_id"],
        "audit_status": audit_status,
        "decision_basis": basis,
        "question_input_hash": evidence["question_input_hash"],
        "question_snapshot_revision": evidence["question_snapshot_revision"],
        "evidence_hash": evidence["evidence_hash"],
        "textbook_catalog_version": evidence["textbook_catalog_version"],
        "node_catalog_version": evidence["node_catalog_version"],
        "validator_bundle_id": VALIDATOR_BUNDLE_ID,
        "validator_bundle_version": VALIDATOR_BUNDLE_VERSION,
        "validator_results_hash": stable_hash(validator_results),
        "validator_results_json": validator_results,
        "auditor_id": AUDITOR_ID,
        "auditor_version": AUDITOR_VERSION,
        "findings": findings,
    }
    audit_hash = stable_hash(audit_json)
    audit_id = f"qama_{audit_hash}"
    if audit_status not in AUDIT_STATUSES:
        raise ValueError("invalid audit status")
    existing = conn.execute(
        """
        SELECT id, evidence_id, question_id, audit_status, decision_basis, audit_json, audit_hash
        FROM question_auto_mapping_audit_logs
        WHERE audit_hash=?
        """,
        (audit_hash,),
    ).fetchone()
    if existing is not None and request.reuse_existing:
        expected_audit_json = _canonical_json(audit_json)
        if (
            existing["id"] != audit_id
            or existing["evidence_id"] != evidence["id"]
            or existing["question_id"] != evidence["question_id"]
            or existing["audit_status"] != audit_status
            or existing["decision_basis"] != basis
            or existing["audit_hash"] != audit_hash
            or existing["audit_json"] != expected_audit_json
        ):
            raise RuntimeError("existing_audit_hash_conflict")
        return AutoMappingAuditResult(
            audit_id=audit_id,
            audit_status=audit_status,
            decision_basis=basis,
            findings=findings,
            audit_json=audit_json,
            reused_existing=True,
        )
    conn.execute(
        """
        INSERT INTO question_auto_mapping_audit_logs
        (id, evidence_id, question_id, textbook_id, curriculum_node_id, audit_status,
         decision_basis, blocked_reason, question_input_hash, question_snapshot_revision,
         evidence_input_hash, evidence_question_snapshot_revision, evidence_hash,
         textbook_catalog_version, node_catalog_version, validator_bundle_id,
         validator_bundle_version, validator_results_hash, validator_results_json,
         findings_json, auditor_id, auditor_version, audit_json, audit_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            evidence["id"],
            evidence["question_id"],
            evidence["textbook_id"],
            evidence["curriculum_node_id"],
            audit_status,
            basis,
            None if audit_status == "pass" else blocked_reason,
            evidence["question_input_hash"],
            evidence["question_snapshot_revision"],
            evidence["question_input_hash"],
            evidence["question_snapshot_revision"],
            evidence["evidence_hash"],
            evidence["textbook_catalog_version"],
            evidence["node_catalog_version"],
            VALIDATOR_BUNDLE_ID,
            VALIDATOR_BUNDLE_VERSION,
            audit_json["validator_results_hash"],
            _canonical_json(validator_results),
            _canonical_json(findings),
            AUDITOR_ID,
            AUDITOR_VERSION,
            _canonical_json(audit_json),
            audit_hash,
        ),
    )
    return AutoMappingAuditResult(
        audit_id=audit_id,
        audit_status=audit_status,
        decision_basis=basis,
        findings=findings,
        audit_json=audit_json,
        reused_existing=False,
    )
