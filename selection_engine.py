"""Deterministic approved-question selection with class-scoped reuse rules.

This module does not create questions and never selects pending, rejected, or
unmapped candidates. A shortage is a first-class result, not an excuse to
relax textbook, stage, quality, or class-history constraints.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

from automatic_gate import evaluate_automatic_admission, requires_live_p1_3c_scope_recheck
from progress_control import ProgressControlError, get_active_progress, get_question_reuse_authorization


@dataclass(frozen=True)
class SelectionRequest:
    class_id: str
    textbook_id: str
    stage: str
    count: int
    question_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectionResult:
    question_ids: tuple[str, ...]
    shortage: int
    blockers: tuple[str, ...]


def select_approved_questions(conn: sqlite3.Connection, request: SelectionRequest) -> SelectionResult:
    """Select approved questions while enforcing class-local delivery history."""
    if request.count < 1:
        raise ValueError("count must be at least 1")

    active_progress = get_active_progress(conn, request.class_id)
    allowed_nodes = active_progress.allowed_curriculum_node_ids
    if not allowed_nodes:
        raise ProgressControlError("active progress must expose at least one allowed curriculum node")

    conditions = [
        "q.quality_status = 'approved'",
        "q.review_status = 'approved'",
        "q.stage = ?",
        "qt.textbook_id = ?",
        "qt.curriculum_node_id IS NOT NULL",
        "qt.fit_status = 'approved'",
        """NOT EXISTS (
            SELECT 1
            FROM question_verifications verification
            WHERE verification.question_id = q.id
              AND verification.verification_type IN (
                  'source_fidelity', 'structural_consistency', 'mathematical_independent',
                  'textbook_scope', 'asset_semantics'
              )
              AND verification.rowid = (
                  SELECT MAX(later.rowid)
                  FROM question_verifications later
                  WHERE later.question_id = verification.question_id
                    AND later.verification_type = verification.verification_type
              )
              AND verification.status <> 'pass'
        )""",
        """(SELECT COUNT(DISTINCT verification_type)
             FROM question_verifications verification
             WHERE verification.question_id = q.id
               AND verification.status = 'pass'
               AND verification.verification_type IN (
                   'source_fidelity', 'structural_consistency', 'mathematical_independent',
                   'textbook_scope', 'asset_semantics'
               )
               AND verification.rowid = (
                   SELECT MAX(later.rowid)
                   FROM question_verifications later
                   WHERE later.question_id = verification.question_id
                     AND later.verification_type = verification.verification_type
               )
        ) = 5""", 
    ]
    params: list[object] = [request.stage, request.textbook_id]

    node_placeholders = ",".join("?" for _ in allowed_nodes)
    conditions.append(f"qt.curriculum_node_id IN ({node_placeholders})")
    params.extend(allowed_nodes)

    conditions.append(
        """NOT EXISTS (
            SELECT 1 FROM question_usage usage
            WHERE usage.question_id = q.id
              AND usage.class_id = ?
              AND usage.delivered = 1
              AND NOT EXISTS (
                  SELECT 1 FROM question_reuse_authorizations reuse_auth
                  WHERE reuse_auth.class_id = usage.class_id
                    AND reuse_auth.question_id = usage.question_id
                    AND reuse_auth.status = 'active'
              )
        )"""
    )
    params.append(request.class_id)

    if request.question_types:
        placeholders = ",".join("?" for _ in request.question_types)
        conditions.append(f"q.question_type IN ({placeholders})")
        params.extend(request.question_types)

    sql = f"""
        SELECT DISTINCT q.id
        FROM questions q
        JOIN question_textbooks qt ON qt.question_id = q.id
        WHERE {' AND '.join(conditions)}
        ORDER BY q.created_at ASC, q.id ASC
    """
    candidates = tuple(row[0] for row in conn.execute(sql, params).fetchall())

    # P1-3c binds approval to controlled files outside the v1 verifier
    # snapshot, so its candidate must still pass the live gate when selected.
    ids: list[str] = []
    for question_id in candidates:
        if requires_live_p1_3c_scope_recheck(conn, question_id):
            if not evaluate_automatic_admission(conn, question_id).eligible:
                continue
        ids.append(question_id)
        if len(ids) == request.count:
            break
    selected_ids = tuple(ids)

    for question_id in selected_ids:
        delivered = conn.execute(
            """SELECT 1 FROM question_usage
            WHERE question_id=? AND class_id=? AND delivered=1
            LIMIT 1""",
            (question_id, request.class_id),
        ).fetchone()
        if delivered is None:
            continue
        # Re-validate per-question authorization against current active progress.
        get_question_reuse_authorization(
            conn,
            class_id=request.class_id,
            question_id=question_id,
        )

    shortage = max(request.count - len(selected_ids), 0)

    blockers: list[str] = []
    if shortage:
        blockers.append(
            f"题库不足：需要 {request.count} 道，符合审核、教材、学段和班级复用规则的题仅有 {len(ids)} 道"
        )
    return SelectionResult(question_ids=selected_ids, shortage=shortage, blockers=tuple(blockers))
