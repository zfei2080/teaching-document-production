"""Automatic delivery gate driven only by reproducible verifier evidence.

This is the production admission path. Human review records are not consulted
here: a question is eligible only when each required automated verification has
a passing result and no verifier has reported fail/unsupported for that type.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from input_snapshot import compute_current_input_hash
from textbook_scope_validator import validate as validate_textbook_scope


P13C_ADMISSION_MIGRATION = "v2.30-p14-current-p13c-question-admission-path"

REQUIRED_VERIFICATION_TYPES = frozenset(
    {
        "source_fidelity",
        "structural_consistency",
        "mathematical_independent",
        "textbook_scope",
        "asset_semantics",
    }
)


@dataclass(frozen=True)
class AutomaticAdmissionResult:
    eligible: bool
    missing_or_nonpassing: tuple[str, ...]
    blockers: tuple[str, ...]


def evaluate_automatic_admission(conn: sqlite3.Connection, question_id: str) -> AutomaticAdmissionResult:
    """Require current verifier passes and a live textbook-scope recheck.

    The persisted five-verifier bundle is bound to the v1 question snapshot.
    P1-3c additionally binds scope to source-revision and controlled-content
    files that can change outside SQLite.  Re-evaluating scope here prevents a
    stale P1-3c approval chain from admitting a question merely because its old
    verifier rows still exist.
    """
    snapshot = conn.execute(
        """SELECT input_hash, invalidated_at FROM question_input_snapshots
           WHERE question_id=?""",
        (question_id,),
    ).fetchone()
    if snapshot is None or snapshot[0] is None or snapshot[1] is not None:
        return AutomaticAdmissionResult(False, tuple(sorted(REQUIRED_VERIFICATION_TYPES)), ("input_snapshot",))
    try:
        if snapshot[0] != compute_current_input_hash(conn, question_id):
            return AutomaticAdmissionResult(False, tuple(sorted(REQUIRED_VERIFICATION_TYPES)), ("input_snapshot",))
    except ValueError:
        return AutomaticAdmissionResult(False, tuple(sorted(REQUIRED_VERIFICATION_TYPES)), ("input_snapshot",))

    rows = conn.execute(
        """SELECT verification_type, status, input_hash
           FROM question_verifications WHERE question_id=?
           ORDER BY verified_at ASC, rowid ASC""",
        (question_id,),
    ).fetchall()
    latest: dict[str, tuple[str, str]] = {}
    for verification_type, status, input_hash in rows:
        latest[verification_type] = (status, input_hash)

    missing_or_nonpassing = sorted(
        verification_type for verification_type in REQUIRED_VERIFICATION_TYPES
        if latest.get(verification_type) != ("pass", snapshot[0])
    )
    blockers = sorted(
        verification_type for verification_type, (status, input_hash) in latest.items()
        if status in {"fail", "unsupported"} or input_hash != snapshot[0]
    )
    p1_3c_recheck_required = requires_live_p1_3c_scope_recheck(conn, question_id)
    if p1_3c_recheck_required:
        try:
            live_scope = validate_textbook_scope(conn, question_id)
        except (OSError, ValueError, sqlite3.Error) as exc:
            live_scope_status = f"unavailable:{type(exc).__name__}"
        else:
            live_scope_status = live_scope.status
        if live_scope_status != "pass":
            if "textbook_scope" not in missing_or_nonpassing:
                missing_or_nonpassing.append("textbook_scope")
            blockers.append("textbook_scope_live:" + live_scope_status)
    return AutomaticAdmissionResult(not missing_or_nonpassing and not blockers,
                                    tuple(sorted(missing_or_nonpassing)), tuple(sorted(blockers)))


def requires_live_p1_3c_scope_recheck(conn: sqlite3.Connection, question_id: str) -> bool:
    """Return whether this question's P1-3c chain requires live scope proof.

    It applies only after v2.30 to questions with a current P1-3c source
    revision, whose approval input includes files outside the v1 snapshot.
    """
    migration_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone() is not None
    revision_view = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name='current_question_mapping_source_revisions'"
    ).fetchone() is not None
    if migration_table and revision_view:
        return (
            conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (P13C_ADMISSION_MIGRATION,)
            ).fetchone() is not None
            and conn.execute(
                "SELECT 1 FROM current_question_mapping_source_revisions WHERE question_id=? LIMIT 1",
                (question_id,),
            ).fetchone() is not None
        )
    return False


def apply_automatic_admission(conn: sqlite3.Connection, question_id: str) -> AutomaticAdmissionResult:
    """Synchronize admission state with the latest reproducible evidence.

    An incomplete, failed, or unsupported verification set is an automatic
    isolation result. This leaves source data and optional human-audit history
    intact, but keeps the candidate out of the delivery pool until a later
    verifier run can prove every requirement.
    """
    result = evaluate_automatic_admission(conn, question_id)
    if result.eligible:
        conn.execute(
            "UPDATE questions SET quality_status='approved', review_status='approved' WHERE id=?",
            (question_id,),
        )
    else:
        conn.execute(
            "UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id=?",
            (question_id,),
        )
    return result


def promote_if_automatically_verified(conn: sqlite3.Connection, question_id: str) -> AutomaticAdmissionResult:
    """Backward-compatible name for the automatic admission state transition."""
    return apply_automatic_admission(conn, question_id)
