"""Read-only scope evaluation for golden-sample questions.

This utility is intentionally an audit aid.  Its keyword candidates are not
question mappings, approvals, or instructions to change a question's state.
The database is opened using SQLite's ``mode=ro`` URI and the only artifact it
writes is an explicitly requested JSON report outside the database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


TOOL_ID = "golden_sample_readonly_scope_evaluation"
TOOL_VERSION = "v1"
DEFAULT_TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
DEFAULT_SOURCE_DOCUMENT_ID = "golden-source-001"
DEFAULT_CURRENT_CHAPTER = "第二章 不等式与不等式组"
DEFAULT_LIMIT = 20
DEFAULT_THRESHOLD = 0.35
DEFAULT_MARGIN = 0.15


class ReadOnlyScopeEvaluationError(RuntimeError):
    """Raised when the source data cannot be evaluated safely."""


def sha256_file(path: Path) -> str:
    """Return a content hash without modifying the file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database with both URI and connection-level read guards."""
    if not db_path.is_file():
        raise ReadOnlyScopeEvaluationError(f"database_not_found:{db_path}")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone() is not None


def _require_tables(conn: sqlite3.Connection) -> None:
    missing = [
        table for table in ("questions", "textbooks", "curriculum_nodes")
        if not _table_exists(conn, table)
    ]
    if missing:
        raise ReadOnlyScopeEvaluationError("missing_required_tables:" + ",".join(missing))


def _load_keyword_scorer() -> tuple[Callable[[str, str], float] | None, str | None]:
    """Load the existing pure candidate scorer without making it a requirement."""
    try:
        from question_classifier import score_question_node
    except (ImportError, ModuleNotFoundError) as exc:
        return None, f"keyword_classifier_unavailable:{type(exc).__name__}"
    return score_question_node, None


def _load_catalog(conn: sqlite3.Connection, textbook_id: str) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    textbook = conn.execute(
        "SELECT id, name, catalog_version, status FROM textbooks WHERE id=?", (textbook_id,)
    ).fetchone()
    if textbook is None:
        raise ReadOnlyScopeEvaluationError(f"textbook_not_found:{textbook_id}")
    nodes = conn.execute(
        """
        SELECT id, parent_id, node_type, name, sequence, catalog_version, status
        FROM curriculum_nodes
        WHERE textbook_id=? AND status='active'
        ORDER BY sequence, id
        """,
        (textbook_id,),
    ).fetchall()
    if not nodes:
        raise ReadOnlyScopeEvaluationError(f"no_active_catalog_nodes:{textbook_id}")
    return textbook, nodes


def _resolve_current_chapter(nodes: list[sqlite3.Row], requested: str) -> sqlite3.Row | None:
    by_id = {row["id"]: row for row in nodes}
    if requested in by_id:
        return by_id[requested] if by_id[requested]["node_type"] == "chapter" else None
    exact = [row for row in nodes if row["node_type"] == "chapter" and row["name"] == requested]
    return exact[0] if len(exact) == 1 else None


def _chapter_for_node(node_id: str, nodes_by_id: dict[str, sqlite3.Row]) -> sqlite3.Row | None:
    """Follow the trusted catalog parent chain; cycles fail closed."""
    visited: set[str] = set()
    current = nodes_by_id.get(node_id)
    while current is not None:
        current_id = current["id"]
        if current_id in visited:
            return None
        visited.add(current_id)
        if current["node_type"] == "chapter":
            return current
        current = nodes_by_id.get(current["parent_id"])
    return None


def _candidate_records(
    stem: str,
    nodes: list[sqlite3.Row],
    nodes_by_id: dict[str, sqlite3.Row],
    scorer: Callable[[str, str], float] | None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if scorer is None:
        return []
    ranked = sorted(
        ((row, round(float(scorer(stem, row["name"])), 4)) for row in nodes),
        key=lambda item: (-item[1], item[0]["id"]),
    )
    result: list[dict[str, Any]] = []
    for node, confidence in ranked:
        # Keep non-zero observations for auditability.  They are explicitly
        # marked ineligible and cannot affect the scope decision below.
        if confidence <= 0:
            continue
        chapter = _chapter_for_node(node["id"], nodes_by_id)
        result.append(
            {
                "node_id": node["id"],
                "node_name": node["name"],
                "node_type": node["node_type"],
                "confidence": confidence,
                "eligible_for_scope_decision": confidence >= DEFAULT_THRESHOLD,
                "evidence": {
                    "method": "question_classifier.score_question_node",
                    "classifier_authority": "candidate_only",
                    "threshold": DEFAULT_THRESHOLD,
                    "note": "关键词重叠分数；不构成教材、知识点或题目状态映射。",
                },
                "chapter": None if chapter is None else {
                    "node_id": chapter["id"], "node_name": chapter["name"]
                },
            }
        )
        if len(result) == limit:
            break
    return result


def _scope_decision(
    candidates: list[dict[str, Any]], current_chapter: sqlite3.Row | None, scorer_error: str | None,
) -> tuple[str, bool, str]:
    if scorer_error:
        return "unknown", False, scorer_error
    if current_chapter is None:
        return "unknown", False, "current_chapter_not_found_or_not_chapter"
    decision_candidates = [item for item in candidates if item["eligible_for_scope_decision"]]
    if not decision_candidates:
        return "unknown", False, "no_candidate_at_or_above_threshold"
    if (len(decision_candidates) > 1
            and decision_candidates[0]["confidence"] - decision_candidates[1]["confidence"] < DEFAULT_MARGIN):
        return "unknown", False, "ambiguous_top_candidate"
    chapter = decision_candidates[0]["chapter"]
    if chapter is None:
        return "unknown", False, "candidate_has_no_resolvable_chapter_ancestor"
    if chapter["node_id"] == current_chapter["id"]:
        return "in_scope", True, "top_candidate_resolves_to_current_chapter"
    return "out_of_scope", True, "top_candidate_resolves_to_different_chapter"


def evaluate_golden_sample_scope(
    *,
    db_path: Path,
    textbook_id: str = DEFAULT_TEXTBOOK_ID,
    source_document_id: str = DEFAULT_SOURCE_DOCUMENT_ID,
    current_chapter: str = DEFAULT_CURRENT_CHAPTER,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Evaluate up to ``limit`` golden questions without changing the database."""
    if limit <= 0:
        raise ValueError("limit_must_be_positive")
    before_hash = sha256_file(db_path)
    scorer, scorer_error = _load_keyword_scorer()
    conn = connect_read_only(db_path)
    try:
        _require_tables(conn)
        textbook, nodes = _load_catalog(conn, textbook_id)
        nodes_by_id = {row["id"]: row for row in nodes}
        chapter = _resolve_current_chapter(nodes, current_chapter)
        questions = conn.execute(
            """
            SELECT id, source_question_no, stem
            FROM questions
            WHERE source_document_id=?
            ORDER BY CAST(source_question_no AS INTEGER), source_question_no, id
            LIMIT ?
            """,
            (source_document_id, limit),
        ).fetchall()
    finally:
        conn.close()
    after_hash = sha256_file(db_path)
    if before_hash != after_hash:
        raise ReadOnlyScopeEvaluationError("database_hash_changed_during_readonly_evaluation")

    evaluations: list[dict[str, Any]] = []
    for question in questions:
        candidates = _candidate_records(question["stem"], nodes, nodes_by_id, scorer)
        status, determinable, reason = _scope_decision(candidates, chapter, scorer_error)
        evaluations.append(
            {
                "question_id": question["id"],
                "source_question_no": question["source_question_no"],
                "candidate_course_nodes": candidates,
                "determinable": determinable,
                "scope_status": status,
                "scope_reason": reason,
            }
        )
    counts = {status: sum(item["scope_status"] == status for item in evaluations)
              for status in ("in_scope", "out_of_scope", "unknown")}
    return {
        "tool": {"id": TOOL_ID, "version": TOOL_VERSION, "read_only": True},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "database_path": str(db_path),
            "database_open_mode": "sqlite_uri_mode_ro",
            "database_sha256_before": before_hash,
            "database_sha256_after": after_hash,
            "database_hash_unchanged": True,
            "textbook": {"id": textbook["id"], "name": textbook["name"],
                         "catalog_version": textbook["catalog_version"]},
            "source_document_id": source_document_id,
            "requested_question_limit": limit,
            "loaded_question_count": len(evaluations),
            "current_chapter": {
                "requested": current_chapter,
                "resolved": None if chapter is None else {
                    "node_id": chapter["id"], "node_name": chapter["name"]
                },
            },
        },
        "limitations": [
            "候选课程节点仅由只读关键词评分产生，不能批准教材或知识点映射。",
            "本工具不会写入数据库、题目原件、题目状态、批准记录或映射记录。",
            "不满足唯一且明确候选条件时，范围状态为 unknown。",
        ],
        "summary": {"counts": counts, "determinable_count": sum(item["determinable"] for item in evaluations)},
        "questions": evaluations,
    }


def write_report(report: dict[str, Any], output_path: Path) -> None:
    """Write only the generated audit artifact, never the evaluated database."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    root = Path(__file__).parent
    parser = argparse.ArgumentParser(description="Read-only golden-sample course scope evaluation")
    parser.add_argument("--db", type=Path, default=root / "data" / "dev" / "teaching_docs_dev.db")
    parser.add_argument("--textbook", default=DEFAULT_TEXTBOOK_ID)
    parser.add_argument("--source-document-id", default=DEFAULT_SOURCE_DOCUMENT_ID)
    parser.add_argument("--current-chapter", default=DEFAULT_CURRENT_CHAPTER)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path,
                        default=root / "output" / "audits" / "golden_sample_readonly_scope_report.json")
    args = parser.parse_args()
    report = evaluate_golden_sample_scope(
        db_path=args.db,
        textbook_id=args.textbook,
        source_document_id=args.source_document_id,
        current_chapter=args.current_chapter,
        limit=args.limit,
    )
    write_report(report, args.output)
    print(json.dumps({"output": str(args.output), "summary": report["summary"],
                      "database_hash_unchanged": report["input"]["database_hash_unchanged"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
