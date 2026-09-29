"""Fail-closed, draft-only evidence for question-to-curriculum mapping.

This module intentionally has no dependency on question_classifier.py or
scope_progress_decision.py.  It writes only question_curriculum_mapping_evidence
and never promotes questions, mappings, or verification evidence.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

from input_snapshot import compute_current_input_hash, stable_hash


STATUSES = frozenset(("candidate", "unsupported", "rejected"))


@dataclass(frozen=True)
class MappingEvidence:
    """The caller-supplied result of a deterministic draft decision."""

    question_id: str
    textbook_id: str
    curriculum_node_id: str
    features: Mapping[str, Any]
    decider_id: str
    decider_version: str
    status: str
    confidence: float
    reason: str


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _validate(evidence: MappingEvidence) -> None:
    for name in ("question_id", "textbook_id", "curriculum_node_id", "decider_id", "decider_version", "reason"):
        _require_text(getattr(evidence, name), name)
    if evidence.status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    if not isinstance(evidence.features, Mapping):
        raise ValueError("features must be a mapping")
    if not isinstance(evidence.confidence, (int, float)) or isinstance(evidence.confidence, bool):
        raise ValueError("confidence must be a finite number from 0.0 to 1.0")
    if not math.isfinite(float(evidence.confidence)) or not 0.0 <= float(evidence.confidence) <= 1.0:
        raise ValueError("confidence must be a finite number from 0.0 to 1.0")


def _current_binding(conn: sqlite3.Connection, evidence: MappingEvidence) -> tuple[str, int, str, str]:
    """Resolve only a live trusted question and audited approved catalog release.

    The release/audit predicates are repeated in the migration trigger so this
    application boundary is not the sole enforcement point.
    """
    row = conn.execute(
        """SELECT s.input_hash, s.revision, t.catalog_version, n.catalog_version
           FROM question_input_snapshots s
           JOIN questions q ON q.id=s.question_id
           JOIN source_documents sd ON sd.id=q.source_document_id
           JOIN textbooks t ON t.id=?
           JOIN curriculum_nodes n ON n.id=? AND n.textbook_id=t.id
           JOIN catalog_releases cr
             ON cr.textbook_id=t.id AND cr.catalog_version=t.catalog_version
           WHERE s.question_id=?
             AND s.input_hash IS NOT NULL AND s.invalidated_at IS NULL
             AND sd.trusted_source=1
             AND cr.status='approved'
             AND EXISTS (
                 SELECT 1
                 FROM catalog_audits ca
                 JOIN controlled_import_runs cir ON cir.id=ca.import_run_id
                 WHERE ca.catalog_release_id=cr.id
                   AND ca.status='approved'
                   AND ca.source_reference=cr.source_reference
                   AND ca.source_hash=cr.source_hash
                   AND cir.import_kind='catalog' AND cir.status='validated'
                   AND cir.source_reference=cr.source_reference
                   AND cir.source_hash=cr.source_hash
             )""",
        (evidence.textbook_id, evidence.curriculum_node_id, evidence.question_id),
    ).fetchone()
    if row is None:
        raise ValueError(
            "a current snapshot, trusted question source, and audited approved catalog release are required"
        )
    input_hash, revision, textbook_catalog_version, node_catalog_version = row
    actual_hash = compute_current_input_hash(conn, evidence.question_id)
    if actual_hash != input_hash:
        raise ValueError("persisted question snapshot does not match current question inputs")
    return input_hash, revision, textbook_catalog_version, node_catalog_version


def record_mapping_evidence(conn: sqlite3.Connection, evidence: MappingEvidence) -> str:
    """Persist one append-only draft decision and return its deterministic ID.

    A repeated or conflicting decision key is rejected.  This prevents a caller
    from treating an old deterministic result as fresh after a retry or input
    race; a new current snapshot/features/version are required instead.
    """
    _validate(evidence)
    input_hash, revision, textbook_version, node_version = _current_binding(conn, evidence)
    features_json = _canonical_json(dict(evidence.features))
    feature_hash = stable_hash(json.loads(features_json))
    payload = {
        "schema": "question-curriculum-mapping-evidence-v1",
        "question_id": evidence.question_id,
        "textbook_id": evidence.textbook_id,
        "curriculum_node_id": evidence.curriculum_node_id,
        "question_input_hash": input_hash,
        "question_snapshot_revision": revision,
        "textbook_catalog_version": textbook_version,
        "node_catalog_version": node_version,
        "feature_hash": feature_hash,
        "features_json": features_json,
        "decider_id": evidence.decider_id,
        "decider_version": evidence.decider_version,
        "status": evidence.status,
        "confidence": float(evidence.confidence),
        "reason": evidence.reason.strip(),
    }
    evidence_hash = stable_hash(payload)
    evidence_id = f"qme_{evidence_hash}"
    existing = conn.execute(
        """SELECT id, evidence_hash FROM question_curriculum_mapping_evidence
           WHERE question_id=? AND curriculum_node_id=? AND question_input_hash=?
             AND feature_hash=? AND decider_id=? AND decider_version=?""",
        (
            evidence.question_id, evidence.curriculum_node_id, input_hash,
            feature_hash, evidence.decider_id, evidence.decider_version,
        ),
    ).fetchone()
    if existing is not None:
        if existing[1] == evidence_hash:
            raise ValueError("duplicate evidence for deterministic decision key")
        raise ValueError("conflicting evidence for deterministic decision key")
    conn.execute(
        """INSERT INTO question_curriculum_mapping_evidence
           (id, question_id, textbook_id, curriculum_node_id, question_input_hash,
            question_snapshot_revision, textbook_catalog_version, node_catalog_version,
            feature_hash, features_json, decider_id, decider_version, status,
            confidence, reason, evidence_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            evidence_id, evidence.question_id, evidence.textbook_id, evidence.curriculum_node_id,
            input_hash, revision, textbook_version, node_version, feature_hash, features_json,
            evidence.decider_id, evidence.decider_version, evidence.status,
            float(evidence.confidence), evidence.reason.strip(), evidence_hash,
        ),
    )
    return evidence_id


def current_evidence(conn: sqlite3.Connection, evidence_id: str) -> bool:
    """Return whether the evidence remains valid for current inputs/catalog."""
    return conn.execute(
        "SELECT 1 FROM current_question_curriculum_mapping_evidence WHERE id=?",
        (evidence_id,),
    ).fetchone() is not None
