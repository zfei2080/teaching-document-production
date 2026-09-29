"""Controlled production workflow for selection, artifact binding, and delivery.

The workflow is deliberately fail-closed:
- only approved/current-snapshot-backed questions can enter generation,
- artifacts must be hash-bound before review,
- only passing quality reports can be promoted to delivery,
- delivery status and per-question usage rows commit in one transaction.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from artifact_validation import ArtifactEvidenceRecord
from progress_control import (
    ProgressControlError,
    ensure_plan_matches_active_progress,
    get_active_progress,
    get_question_reuse_authorization,
)
from selection_engine import SelectionRequest, SelectionResult, select_approved_questions


@dataclass(frozen=True)
class PlannedSelection:
    request_id: str
    plan_id: str
    result: SelectionResult


@dataclass(frozen=True)
class DraftDocument:
    request_id: str
    plan_id: str
    document_id: str
    question_ids: tuple[str, ...]


@dataclass(frozen=True)
class BoundArtifact:
    document_id: str
    content_hash: str
    output_path: str | None


@dataclass(frozen=True)
class RecordedQualityReport:
    document_id: str
    quality_report_id: str
    passed: bool


@dataclass(frozen=True)
class QualityBindingContext:
    document_id: str
    request_id: str
    document_type: str
    audience: str
    content_hash: str
    question_ids: tuple[str, ...]
    question_set_hash: str
    snapshot_hash: str


@dataclass(frozen=True)
class DeliveredDocument:
    request_id: str
    document_id: str
    usage_ids: tuple[str, ...]


PASSING_GATE = "pass"
BLOCKED_REPORT_STATUS = "blocked"
PASSING_REPORT_STATUS = "pass"
DEFAULT_SECTION = "待编排"
DEFAULT_SELECTION_REASON = "符合审核、教材、学段和复用规则"
DEFAULT_USAGE_TYPE = "exercise"


def _dump_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _hash_ordered(values: Sequence[str]) -> str:
    return "|".join(values)


def _get_quality_binding_context(conn: sqlite3.Connection, document_id: str) -> QualityBindingContext:
    document_row = conn.execute(
        """SELECT td.id, td.request_id, td.document_type, td.audience, td.content_hash
           FROM teaching_documents td
           WHERE td.id=?""",
        (document_id,),
    ).fetchone()
    if document_row is None:
        raise ValueError("document does not exist")
    if document_row[4] is None or not str(document_row[4]).strip():
        raise ProgressControlError("document requires content hash before quality binding")
    question_rows = conn.execute(
        """SELECT dq.question_id, qis.input_hash
           FROM document_questions dq
           LEFT JOIN question_input_snapshots qis ON qis.question_id = dq.question_id
           WHERE dq.document_id=?
           ORDER BY dq.sort_order ASC, dq.question_id ASC""",
        (document_id,),
    ).fetchall()
    if not question_rows:
        raise ProgressControlError("document requires question set before quality binding")
    question_ids = tuple(str(row[0]) for row in question_rows)
    snapshot_pairs: list[str] = []
    for question_id, input_hash in question_rows:
        snapshot_row = conn.execute(
            "SELECT input_hash, invalidated_at FROM question_input_snapshots WHERE question_id=?",
            (question_id,),
        ).fetchone()
        if snapshot_row is None or snapshot_row[0] is None or snapshot_row[1] is not None:
            raise ProgressControlError(f"document question lacks current snapshot: {question_id}")
        snapshot_pairs.append(f"{question_id}:{snapshot_row[0]}")
    return QualityBindingContext(
        document_id=str(document_row[0]),
        request_id=str(document_row[1]),
        document_type=str(document_row[2]),
        audience=str(document_row[3]),
        content_hash=str(document_row[4]).strip(),
        question_ids=question_ids,
        question_set_hash=_hash_ordered(question_ids),
        snapshot_hash=_hash_ordered(tuple(snapshot_pairs)),
    )


def _normalize_artifact_evidence(evidence: ArtifactEvidenceRecord | dict[str, Any] | None, *, context: QualityBindingContext) -> ArtifactEvidenceRecord:
    if evidence is None:
        if context.document_type == "pdf":
            return ArtifactEvidenceRecord(
                artifact_role="pdf",
                gate="unsupported",
                manifest_hash=None,
                artifact_hash=None,
                checks_json="[]",
                findings_json=_dump_json([
                    {"code": "pdf_unsupported", "message": "PDF 当前不支持可信成品验证。", "severity": "error"}
                ]),
            )
        return ArtifactEvidenceRecord(
            artifact_role=f"docx_{context.audience}",
            gate="missing",
            manifest_hash=None,
            artifact_hash=None,
            checks_json="[]",
            findings_json=_dump_json([
                {"code": "artifact_evidence_missing", "message": "缺少成品验证证据。", "severity": "error"}
            ]),
        )
    if isinstance(evidence, dict):
        evidence = ArtifactEvidenceRecord(**evidence)
    expected_role = "pdf" if context.document_type == "pdf" else f"docx_{context.audience}"
    if evidence.artifact_role != expected_role:
        raise ProgressControlError("artifact evidence role does not match document type/audience")
    if evidence.gate not in {"pass", "fail", "not_run", "unsupported", "missing"}:
        raise ProgressControlError("artifact evidence gate is invalid")
    return evidence


def _persist_artifact_evidence(conn: sqlite3.Connection, *, quality_report_id: str, context: QualityBindingContext, evidence: ArtifactEvidenceRecord) -> None:
    conn.execute(
        """INSERT INTO artifact_validation_evidence
        (id, document_id, quality_report_id, artifact_role, content_hash, question_set_hash, snapshot_hash,
         manifest_hash, artifact_hash, gate, checks_json, findings_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"{quality_report_id}:{evidence.artifact_role}",
            context.document_id,
            quality_report_id,
            evidence.artifact_role,
            context.content_hash,
            context.question_set_hash,
            context.snapshot_hash,
            evidence.manifest_hash,
            evidence.artifact_hash,
            evidence.gate,
            evidence.checks_json,
            evidence.findings_json,
        ),
    )


def _has_matching_passing_evidence(conn: sqlite3.Connection, *, context: QualityBindingContext) -> bool:
    row = conn.execute(
        """SELECT 1
           FROM quality_reports qr
           JOIN artifact_validation_evidence ave ON ave.quality_report_id = qr.id AND ave.document_id = qr.document_id
           WHERE qr.document_id=?
             AND qr.status='pass'
             AND qr.data_gate='pass'
             AND qr.rule_gate='pass'
             AND qr.fidelity_gate='pass'
             AND qr.artifact_gate='pass'
             AND ave.gate='pass'
             AND ave.content_hash=?
             AND ave.question_set_hash=?
             AND ave.snapshot_hash=?
             AND ave.artifact_role=?
           ORDER BY qr.created_at DESC, qr.id DESC
           LIMIT 1""",
        (
            context.document_id,
            context.content_hash,
            context.question_set_hash,
            context.snapshot_hash,
            "pdf" if context.document_type == "pdf" else f"docx_{context.audience}",
        ),
    ).fetchone()
    return row is not None


def plan_selection(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    plan_id: str,
    ruleset_id: str,
    raw_request: str,
    request_type: str,
    parsed_spec: dict[str, Any],
    selection_request: SelectionRequest,
) -> PlannedSelection:
    """Persist an auditable selection plan and block safely on a shortage."""
    active_progress = get_active_progress(conn, selection_request.class_id)
    if active_progress.textbook_id != selection_request.textbook_id:
        raise ProgressControlError("selection request textbook must match current active progress")
    with conn:
        conn.execute(
            """INSERT INTO production_requests
            (id, raw_request, request_type, parsed_spec_json, class_id, status)
            VALUES (?, ?, ?, ?, ?, 'selecting')""",
            (
                request_id,
                raw_request,
                request_type,
                _dump_json(parsed_spec),
                selection_request.class_id,
            ),
        )
        result = select_approved_questions(conn, selection_request)
        status = "blocked" if result.shortage else "ready"
        conn.execute(
            """INSERT INTO selection_plans
            (id, request_id, ruleset_id, filters_json, requirements_json, shortages_json, warnings_json, status)
            VALUES (?, ?, ?, ?, ?, ?, '[]', ?)""",
            (
                plan_id,
                request_id,
                ruleset_id,
                _dump_json(
                    {
                        "class_id": selection_request.class_id,
                        "textbook_id": selection_request.textbook_id,
                        "stage": selection_request.stage,
                        "question_types": selection_request.question_types,
                        "reuse_mode": "per-question-authorization-only",
                    }
                ),
                _dump_json({"count": selection_request.count}),
                json.dumps(list(result.blockers), ensure_ascii=False),
                status,
            ),
        )
        if result.shortage:
            conn.execute("UPDATE production_requests SET status='blocked' WHERE id=?", (request_id,))
        else:
            for position, question_id in enumerate(result.question_ids, start=1):
                conn.execute(
                    """INSERT INTO selection_plan_questions
                    (selection_plan_id, question_id, section, layer, sort_order, selection_reason)
                    VALUES (?, ?, ?, NULL, ?, ?)""",
                    (plan_id, question_id, DEFAULT_SECTION, position, DEFAULT_SELECTION_REASON),
                )
            ensure_plan_matches_active_progress(conn, plan_id)
            conn.execute("UPDATE production_requests SET status='selected' WHERE id=?", (request_id,))
    return PlannedSelection(request_id=request_id, plan_id=plan_id, result=result)


def create_generation_task(
    conn: sqlite3.Connection,
    *,
    document_id: str,
    request_id: str,
    plan_id: str,
    ruleset_id: str,
    class_id: str,
    document_type: str,
    audience: str,
) -> DraftDocument:
    """Create the auditable draft document shell for a selected plan."""
    with conn:
        ensure_plan_matches_active_progress(conn, plan_id)
        rows = conn.execute(
            """SELECT question_id, section, layer, sort_order
               FROM selection_plan_questions
               WHERE selection_plan_id=?
               ORDER BY sort_order ASC, question_id ASC""",
            (plan_id,),
        ).fetchall()
        if not rows:
            raise ValueError("selection plan has no questions")
        conn.execute(
            """INSERT INTO teaching_documents
            (id, request_id, selection_plan_id, ruleset_id, class_id, document_type, audience, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')""",
            (document_id, request_id, plan_id, ruleset_id, class_id, document_type, audience),
        )
        for question_id, section, layer, sort_order in rows:
            conn.execute(
                """INSERT INTO document_questions
                (document_id, question_id, section, layer, sort_order)
                VALUES (?, ?, ?, ?, ?)""",
                (document_id, question_id, section, layer, sort_order),
            )
        conn.execute("UPDATE production_requests SET status='generating' WHERE id=?", (request_id,))
    return DraftDocument(
        request_id=request_id,
        plan_id=plan_id,
        document_id=document_id,
        question_ids=tuple(row[0] for row in rows),
    )


def bind_generated_artifact(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    document_id: str,
    content_hash: str,
    output_path: str | None = None,
) -> BoundArtifact:
    """Bind a rendered artifact hash before quality review can begin."""
    normalized_hash = content_hash.strip()
    if not normalized_hash:
        raise ValueError("content_hash is required")
    with conn:
        plan_row = conn.execute(
            "SELECT selection_plan_id FROM teaching_documents WHERE id=?",
            (document_id,),
        ).fetchone()
        if plan_row is None or not plan_row[0]:
            raise ValueError("document does not reference a selection plan")
        ensure_plan_matches_active_progress(conn, plan_row[0])
        conn.execute(
            "UPDATE teaching_documents SET content_hash=?, output_path=?, status='pending_review' WHERE id=?",
            (normalized_hash, output_path, document_id),
        )
        conn.execute("UPDATE production_requests SET status='validating' WHERE id=?", (request_id,))
    return BoundArtifact(document_id=document_id, content_hash=normalized_hash, output_path=output_path)


def record_quality_report(
    conn: sqlite3.Connection,
    *,
    quality_report_id: str,
    document_id: str,
    data_gate: str,
    rule_gate: str,
    fidelity_gate: str,
    artifact_gate: str,
    findings: Sequence[object] | None = None,
    blockers: Sequence[object] | None = None,
    artifact_evidence: ArtifactEvidenceRecord | dict[str, Any] | None = None,
) -> RecordedQualityReport:
    """Persist the only quality report allowed for a generated artifact."""
    context = _get_quality_binding_context(conn, document_id)
    normalized_evidence = _normalize_artifact_evidence(artifact_evidence, context=context)
    if artifact_gate != normalized_evidence.gate:
        raise ProgressControlError("artifact gate must match persisted artifact evidence gate")
    passed = all(gate == PASSING_GATE for gate in (data_gate, rule_gate, fidelity_gate, artifact_gate))
    status = PASSING_REPORT_STATUS if passed else BLOCKED_REPORT_STATUS
    with conn:
        conn.execute(
            """INSERT INTO quality_reports
            (id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, findings_json, blockers_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                quality_report_id,
                document_id,
                data_gate,
                rule_gate,
                fidelity_gate,
                artifact_gate,
                _dump_json(list(findings or [])),
                _dump_json(list(blockers or [])),
                status,
            ),
        )
        _persist_artifact_evidence(conn, quality_report_id=quality_report_id, context=context, evidence=normalized_evidence)
        if passed:
            if not _has_matching_passing_evidence(conn, context=context):
                raise ProgressControlError("passing quality report requires exact matching artifact evidence binding")
            conn.execute("UPDATE teaching_documents SET status='passed' WHERE id=?", (document_id,))
        else:
            conn.execute("UPDATE teaching_documents SET status='blocked' WHERE id=?", (document_id,))
    return RecordedQualityReport(document_id=document_id, quality_report_id=quality_report_id, passed=passed)


def deliver_document(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    document_id: str,
    usage_rows: Iterable[dict[str, Any]],
) -> DeliveredDocument:
    """Atomically mark delivery and write immutable per-question usage rows."""
    rows = tuple(usage_rows)
    if not rows:
        raise ValueError("usage_rows is required")
    usage_ids: list[str] = []
    with conn:
        plan_row = conn.execute(
            "SELECT selection_plan_id, class_id FROM teaching_documents WHERE id=?",
            (document_id,),
        ).fetchone()
        if plan_row is None or not plan_row[0]:
            raise ValueError("document does not reference a selection plan")
        plan_id, document_class_id = plan_row
        ensure_plan_matches_active_progress(conn, plan_id)
        context = _get_quality_binding_context(conn, document_id)
        if not _has_matching_passing_evidence(conn, context=context):
            raise ProgressControlError("delivery blocked: missing exact passing quality evidence for current content/question set/snapshot")

        document_question_ids = {
            row[0]
            for row in conn.execute(
                "SELECT question_id FROM document_questions WHERE document_id=?",
                (document_id,),
            ).fetchall()
        }
        if len(rows) != len(document_question_ids):
            raise ProgressControlError("usage_rows must contain one row per document question")

        seen_question_ids: set[str] = set()
        for row in rows:
            usage_id = str(row["id"])
            usage_ids.append(usage_id)
            question_id = str(row["question_id"])
            class_id = str(row["class_id"])
            if class_id != document_class_id:
                raise ProgressControlError("usage row class must match document class")
            if question_id not in document_question_ids:
                raise ProgressControlError(f"usage row question is not part of document: {question_id}")
            if question_id in seen_question_ids:
                raise ProgressControlError(f"duplicate usage row for question: {question_id}")
            seen_question_ids.add(question_id)

            delivered_before = conn.execute(
                """SELECT 1 FROM question_usage
                WHERE question_id=? AND class_id=? AND delivered=1
                LIMIT 1""",
                (question_id, class_id),
            ).fetchone()
            reuse_allowed = 1 if row.get("reuse_allowed") else 0
            reuse_reason = row.get("reuse_reason")
            if delivered_before is not None:
                auth = get_question_reuse_authorization(
                    conn,
                    class_id=class_id,
                    question_id=question_id,
                )
                reuse_allowed = 1
                reuse_reason = auth.reason
            elif reuse_allowed:
                raise ProgressControlError("reuse_allowed is only valid for questions with an active per-question authorization")

            conn.execute(
                """INSERT INTO question_usage
                (id, question_id, class_id, document_id, section, usage_type, delivered, reuse_allowed, reuse_reason)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    usage_id,
                    question_id,
                    class_id,
                    document_id,
                    row.get("section", DEFAULT_SECTION),
                    row.get("usage_type", DEFAULT_USAGE_TYPE),
                    reuse_allowed,
                    reuse_reason,
                ),
            )
        if seen_question_ids != document_question_ids:
            missing = sorted(document_question_ids - seen_question_ids)
            raise ProgressControlError(f"usage_rows missing document questions: {missing[0]}")
        conn.execute("UPDATE teaching_documents SET status='delivered' WHERE id=?", (document_id,))
        conn.execute("UPDATE production_requests SET status='delivered' WHERE id=?", (request_id,))
    return DeliveredDocument(request_id=request_id, document_id=document_id, usage_ids=tuple(usage_ids))
