"""Auditable recording of human or authorized review decisions.

This module records a review decision with evidence and promotes a candidate
only through the existing five-review admission gate. It intentionally has no
"approve all" operation.
"""

from __future__ import annotations

import sqlite3
import uuid

from review_gate import AdmissionResult, promote_if_eligible


def record_review(
    conn: sqlite3.Connection,
    *,
    question_id: str,
    review_type: str,
    decision: str,
    reviewer: str,
    rationale: str,
    reviewer_role: str = "assistant_precheck",
) -> AdmissionResult:
    """Append a review decision and attempt promotion through the strict gate."""
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    if not rationale.strip():
        raise ValueError("rationale is required")
    if reviewer_role not in {"assistant_precheck", "teacher_final"}:
        raise ValueError("reviewer_role must be assistant_precheck or teacher_final")
    with conn:
        conn.execute(
            """INSERT INTO question_reviews
            (id, question_id, review_type, decision, reviewer, rationale, reviewer_role)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()), question_id, review_type, decision,
                reviewer, rationale, reviewer_role,
            ),
        )
        return promote_if_eligible(conn, question_id)
