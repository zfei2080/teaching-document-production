"""Deterministic admission gate for reviewed question candidates.

A source-faithful candidate is not deliverable until every mandatory review has
an explicit approved record and there is no rejection or revision request.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

REQUIRED_REVIEW_TYPES = frozenset(
    {
        "math_correctness",
        "teaching_fit",
        "textbook_scope",
        "asset_match",
        "copyright_source",
    }
)


@dataclass(frozen=True)
class AdmissionResult:
    eligible: bool
    missing_reviews: tuple[str, ...]
    blocking_reviews: tuple[str, ...]


def evaluate_question_admission(conn: sqlite3.Connection, question_id: str) -> AdmissionResult:
    """Return whether a pending candidate has all required approved reviews."""
    rows = conn.execute(
        "SELECT review_type, decision, reviewer_role FROM question_reviews WHERE question_id=?",
        (question_id,),
    ).fetchall()
    by_type: dict[str, set[str]] = {}
    for review_type, decision, reviewer_role in rows:
        by_type.setdefault(review_type, set()).add(decision)

    missing = sorted(
        review_type
        for review_type in REQUIRED_REVIEW_TYPES
        if by_type.get(review_type) != {"approved"}
    )
    blocking = sorted(
        review_type
        for review_type, decisions in by_type.items()
        if "rejected" in decisions or "needs_revision" in decisions
    )
    return AdmissionResult(
        eligible=not missing and not blocking,
        missing_reviews=tuple(missing),
        blocking_reviews=tuple(blocking),
    )


def promote_if_eligible(conn: sqlite3.Connection, question_id: str) -> AdmissionResult:
    """Promote only a fully reviewed question; otherwise leave its status intact."""
    result = evaluate_question_admission(conn, question_id)
    if result.eligible:
        conn.execute(
            "UPDATE questions SET quality_status='approved', review_status='approved' WHERE id=?",
            (question_id,),
        )
    return result
