"""Fail-closed class progress and per-question reuse authorization service.

This module intentionally avoids schema changes. It operates only on an explicit,
caller-supplied contract and blocks when the backing database does not provide
that contract. The caller must provide the current curriculum node plus the full
set of allowed prerequisite node ids; this service never infers prerequisite
relationships on its own.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Sequence

from automatic_gate import evaluate_automatic_admission, requires_live_p1_3c_scope_recheck


_PROGRESS_TABLE = "class_progress_controls"
_REUSE_TABLE = "question_reuse_authorizations"
_ALLOWED_TABLE = "class_progress_allowed_nodes"
_REQUIRED_USAGE_COLUMNS = (
    "question_id",
    "class_id",
    "document_id",
    "delivered",
    "reuse_allowed",
    "reuse_reason",
)
_REQUIRED_REQUEST_COLUMNS = ("id", "class_id", "status")
_REQUIRED_PLAN_COLUMNS = ("id", "request_id", "status")


@dataclass(frozen=True)
class ActiveProgress:
    class_id: str
    textbook_id: str
    current_curriculum_node_id: str
    allowed_curriculum_node_ids: tuple[str, ...]
    progress_id: str | None = None
    status: str = "active"


@dataclass(frozen=True)
class QuestionReuseAuthorization:
    authorization_id: str
    class_id: str
    request_id: str
    question_id: str
    progress_id: str
    reason: str
    status: str = "active"


class ProgressControlError(ValueError):
    """Raised when class progress or reuse authorization must fail closed."""


class ProgressControlStorageError(RuntimeError):
    """Raised when the required P1-1 storage contract is unavailable."""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _get_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    if not _table_exists(conn, table):
        raise ProgressControlStorageError(f"missing required table: {table}")
    return tuple(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))


def _require_table_columns(conn: sqlite3.Connection, table: str, required: Sequence[str]) -> None:
    columns = set(_get_columns(conn, table))
    missing = [name for name in required if name not in columns]
    if missing:
        raise ProgressControlStorageError(f"table {table} missing required columns: {', '.join(missing)}")


def _require_progress_contract(conn: sqlite3.Connection) -> None:
    _require_table_columns(
        conn,
        _PROGRESS_TABLE,
        ("id", "class_id", "textbook_id", "current_curriculum_node_id", "status", "allowed_nodes_json"),
    )
    _require_table_columns(
        conn,
        _REUSE_TABLE,
        ("id", "class_id", "request_id", "question_id", "progress_id", "reason", "status"),
    )
    if _table_exists(conn, _ALLOWED_TABLE):
        _require_table_columns(conn, _ALLOWED_TABLE, ("progress_id", "curriculum_node_id"))


def _normalize_node_ids(current_curriculum_node_id: str, allowed_curriculum_node_ids: Iterable[str]) -> tuple[str, ...]:
    current = (current_curriculum_node_id or "").strip()
    if not current:
        raise ProgressControlError("current_curriculum_node_id is required")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in allowed_curriculum_node_ids:
        node_id = str(raw).strip()
        if not node_id:
            continue
        if node_id not in seen:
            seen.add(node_id)
            normalized.append(node_id)
    if current not in seen:
        raise ProgressControlError("allowed_curriculum_node_ids must include current_curriculum_node_id")
    return tuple(normalized)


def _fetchone_required(conn: sqlite3.Connection, sql: str, params: Sequence[object], missing_message: str) -> sqlite3.Row:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise ProgressControlError(missing_message)
    return row


def _fetch_class_and_node(conn: sqlite3.Connection, class_id: str, current_curriculum_node_id: str) -> tuple[str, str]:
    class_row = _fetchone_required(
        conn,
        "SELECT textbook_id FROM classes WHERE id=?",
        (class_id,),
        "class does not exist",
    )
    textbook_id = class_row[0]
    if not textbook_id:
        raise ProgressControlError("class must be bound to a textbook")
    node_row = _fetchone_required(
        conn,
        "SELECT textbook_id FROM curriculum_nodes WHERE id=? AND status='active'",
        (current_curriculum_node_id,),
        "current curriculum node does not exist or is not active",
    )
    node_textbook_id = node_row[0]
    if node_textbook_id != textbook_id:
        raise ProgressControlError("current curriculum node must belong to the class textbook")
    return textbook_id, node_textbook_id


def _assert_allowed_nodes_same_textbook(conn: sqlite3.Connection, textbook_id: str, node_ids: Sequence[str]) -> None:
    placeholders = ",".join("?" for _ in node_ids)
    rows = conn.execute(
        f"SELECT id, textbook_id, status FROM curriculum_nodes WHERE id IN ({placeholders})",
        tuple(node_ids),
    ).fetchall()
    seen = {row[0] for row in rows}
    missing = [node_id for node_id in node_ids if node_id not in seen]
    if missing:
        raise ProgressControlError(f"allowed curriculum node does not exist: {missing[0]}")
    for node_id, node_textbook_id, status in rows:
        if status != "active":
            raise ProgressControlError(f"allowed curriculum node is not active: {node_id}")
        if node_textbook_id != textbook_id:
            raise ProgressControlError(f"allowed curriculum node must belong to the class textbook: {node_id}")


def set_active_progress(
    conn: sqlite3.Connection,
    *,
    progress_id: str,
    class_id: str,
    current_curriculum_node_id: str,
    allowed_curriculum_node_ids: Iterable[str],
) -> ActiveProgress:
    """Replace a class's active progress with an explicit allowed node set."""
    _require_progress_contract(conn)
    allowed_ids = _normalize_node_ids(current_curriculum_node_id, allowed_curriculum_node_ids)
    textbook_id, _ = _fetch_class_and_node(conn, class_id, current_curriculum_node_id)
    _assert_allowed_nodes_same_textbook(conn, textbook_id, allowed_ids)

    payload = json.dumps(list(allowed_ids), ensure_ascii=False, sort_keys=True)
    with conn:
        conn.execute(f"UPDATE {_PROGRESS_TABLE} SET status='replaced' WHERE class_id=? AND status='active'", (class_id,))
        conn.execute(
            f"""INSERT INTO {_PROGRESS_TABLE}
            (id, class_id, textbook_id, current_curriculum_node_id, allowed_nodes_json, status)
            VALUES (?, ?, ?, ?, ?, 'active')""",
            (progress_id, class_id, textbook_id, current_curriculum_node_id, payload),
        )
        if _table_exists(conn, _ALLOWED_TABLE):
            conn.execute(f"DELETE FROM {_ALLOWED_TABLE} WHERE progress_id=?", (progress_id,))
            conn.executemany(
                f"INSERT INTO {_ALLOWED_TABLE} (progress_id, curriculum_node_id) VALUES (?, ?)",
                [(progress_id, node_id) for node_id in allowed_ids],
            )
    return ActiveProgress(
        class_id=class_id,
        textbook_id=textbook_id,
        current_curriculum_node_id=current_curriculum_node_id,
        allowed_curriculum_node_ids=allowed_ids,
        progress_id=progress_id,
    )


def get_active_progress(conn: sqlite3.Connection, class_id: str) -> ActiveProgress:
    """Return the only active progress row for a class or fail closed."""
    _require_progress_contract(conn)
    rows = conn.execute(
        f"""SELECT id, class_id, textbook_id, current_curriculum_node_id, allowed_nodes_json, status
        FROM {_PROGRESS_TABLE}
        WHERE class_id=? AND status='active'
        ORDER BY id DESC""",
        (class_id,),
    ).fetchall()
    if not rows:
        raise ProgressControlError("class has no active progress")
    if len(rows) != 1:
        raise ProgressControlError("class has multiple active progress rows")
    progress_id, resolved_class_id, textbook_id, current_node_id, allowed_nodes_json, status = rows[0]
    try:
        allowed_ids = tuple(json.loads(allowed_nodes_json))
    except Exception as exc:
        raise ProgressControlStorageError("active progress allowed_nodes_json is invalid") from exc
    normalized = _normalize_node_ids(current_node_id, allowed_ids)
    _assert_allowed_nodes_same_textbook(conn, textbook_id, normalized)
    return ActiveProgress(
        class_id=resolved_class_id,
        textbook_id=textbook_id,
        current_curriculum_node_id=current_node_id,
        allowed_curriculum_node_ids=normalized,
        progress_id=progress_id,
        status=status,
    )


def ensure_plan_matches_active_progress(conn: sqlite3.Connection, plan_id: str) -> ActiveProgress:
    """Verify every selected question in a plan remains bound to the active progress."""
    active = get_active_progress(conn, _fetchone_required(
        conn,
        "SELECT class_id FROM production_requests WHERE id=(SELECT request_id FROM selection_plans WHERE id=?)",
        (plan_id,),
        "selection plan does not exist",
    )[0])

    question_rows = conn.execute(
        """SELECT spq.question_id, qt.textbook_id, qt.curriculum_node_id
        FROM selection_plan_questions spq
        JOIN selection_plans sp ON sp.id = spq.selection_plan_id
        JOIN production_requests pr ON pr.id = sp.request_id
        JOIN question_textbooks qt ON qt.question_id = spq.question_id
        WHERE spq.selection_plan_id=? AND qt.fit_status='approved' AND qt.textbook_id=?""",
        (plan_id, active.textbook_id),
    ).fetchall()
    if not question_rows:
        raise ProgressControlError("selection plan has no approved textbook-bound questions")
    matched_questions = {row[0] for row in question_rows}
    plan_questions = {
        row[0]
        for row in conn.execute(
            "SELECT question_id FROM selection_plan_questions WHERE selection_plan_id=?",
            (plan_id,),
        ).fetchall()
    }
    if matched_questions != plan_questions:
        raise ProgressControlError("selection plan contains question/textbook mappings that do not match the active class textbook")
    allowed = set(active.allowed_curriculum_node_ids)
    for question_id, textbook_id, node_id in question_rows:
        if textbook_id != active.textbook_id:
            raise ProgressControlError(f"selection plan question textbook mismatch: {question_id}")
        if node_id is None or node_id not in allowed:
            raise ProgressControlError(f"selection plan question is outside active progress: {question_id}")
        if requires_live_p1_3c_scope_recheck(conn, question_id):
            admission = evaluate_automatic_admission(conn, question_id)
            if not admission.eligible:
                raise ProgressControlError(
                    f"selection plan question no longer passes automatic admission: {question_id}"
                )
    return active


def authorize_question_reuse(
    conn: sqlite3.Connection,
    *,
    authorization_id: str,
    class_id: str,
    request_id: str,
    question_id: str,
    reason: str,
) -> QuestionReuseAuthorization:
    """Create a single explicit reuse authorization for a delivered question."""
    _require_progress_contract(conn)
    normalized_reason = (reason or "").strip()
    if not normalized_reason:
        raise ProgressControlError("reuse reason is required")
    active = get_active_progress(conn, class_id)
    _require_table_columns(conn, "question_usage", _REQUIRED_USAGE_COLUMNS)
    _require_table_columns(conn, "production_requests", _REQUIRED_REQUEST_COLUMNS)
    request_row = conn.execute(
        "SELECT class_id, status FROM production_requests WHERE id=?",
        (request_id,),
    ).fetchone()
    if request_row is None:
        raise ProgressControlError("production request does not exist")
    request_class_id, request_status = request_row
    if request_class_id != class_id:
        raise ProgressControlError("production request must belong to the same class")
    if request_status not in {"selected", "generating", "validating", "passed"}:
        raise ProgressControlError("production request is not in a live status")
    usage_rows = conn.execute(
        """SELECT class_id, question_id, reuse_allowed, reuse_reason
        FROM question_usage
        WHERE class_id=? AND question_id=? AND delivered=1""",
        (class_id, question_id),
    ).fetchall()
    if not usage_rows:
        raise ProgressControlError("reuse authorization requires an existing delivered usage in the same class")
    question_map = conn.execute(
        "SELECT textbook_id, curriculum_node_id, fit_status FROM question_textbooks WHERE question_id=?",
        (question_id,),
    ).fetchall()
    if not question_map:
        raise ProgressControlError("question has no textbook mapping")
    allowed = set(active.allowed_curriculum_node_ids)
    matched = False
    for textbook_id, node_id, fit_status in question_map:
        if fit_status != 'approved':
            continue
        if textbook_id != active.textbook_id:
            raise ProgressControlError("question textbook does not match active progress textbook")
        if node_id is None or node_id not in allowed:
            raise ProgressControlError("question is outside active progress")
        matched = True
    if not matched:
        raise ProgressControlError("question has no approved mapping within active progress")
    existing = conn.execute(
        f"SELECT id, reason, status FROM {_REUSE_TABLE} WHERE class_id=? AND request_id=? AND question_id=? AND status='active'",
        (class_id, request_id, question_id),
    ).fetchall()
    if existing:
        raise ProgressControlError("active reuse authorization already exists for this class/question")
    with conn:
        conn.execute(
            f"""INSERT INTO {_REUSE_TABLE}
            (id, class_id, request_id, question_id, progress_id, reason, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')""",
            (authorization_id, class_id, request_id, question_id, active.progress_id, normalized_reason),
        )
    return QuestionReuseAuthorization(
        authorization_id=authorization_id,
        class_id=class_id,
        request_id=request_id,
        question_id=question_id,
        progress_id=active.progress_id or "",
        reason=normalized_reason,
    )


def get_question_reuse_authorization(
    conn: sqlite3.Connection,
    *,
    class_id: str,
    request_id: str,
    question_id: str,
) -> QuestionReuseAuthorization:
    """Read the active per-question reuse authorization and validate it still matches progress."""
    _require_progress_contract(conn)
    active = get_active_progress(conn, class_id)
    _require_table_columns(conn, "production_requests", _REQUIRED_REQUEST_COLUMNS)
    request_row = conn.execute(
        "SELECT class_id, status FROM production_requests WHERE id=?",
        (request_id,),
    ).fetchone()
    if request_row is None:
        raise ProgressControlError("production request does not exist")
    request_class_id, request_status = request_row
    if request_class_id != class_id:
        raise ProgressControlError("production request must belong to the same class")
    if request_status not in {"selected", "generating", "validating", "passed"}:
        raise ProgressControlError("production request is not in a live status")
    rows = conn.execute(
        f"SELECT id, class_id, request_id, question_id, progress_id, reason, status FROM {_REUSE_TABLE} WHERE class_id=? AND request_id=? AND question_id=? AND status='active'",
        (class_id, request_id, question_id),
    ).fetchall()
    if not rows:
        raise ProgressControlError("no active reuse authorization for this class/question")
    if len(rows) != 1:
        raise ProgressControlError("multiple active reuse authorizations for this class/question")
    authorization_id, auth_class_id, auth_request_id, auth_question_id, progress_id, reason, status = rows[0]
    if progress_id != active.progress_id:
        raise ProgressControlError("reuse authorization is not bound to the current active progress")
    if not reason or not str(reason).strip():
        raise ProgressControlError("reuse authorization reason is empty")
    return QuestionReuseAuthorization(
        authorization_id=authorization_id,
        class_id=auth_class_id,
        request_id=auth_request_id,
        question_id=auth_question_id,
        progress_id=progress_id,
        reason=str(reason).strip(),
        status=status,
    )
