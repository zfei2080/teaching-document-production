"""Append-only revalidation entry point for already structured source content.

This module is intentionally separate from candidate import: it re-reads exact
source blocks for one explicit source version, preserves the existing question
records, and can only append new evidence records in later contract versions.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Iterable, Mapping

from source_content_question_segmentation import QuestionCandidate, segment_questions


class ContentQuestionRevalidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RevalidationSourceSnapshot:
    source_version_id: str
    extraction_run_id: str
    candidates: tuple[QuestionCandidate, ...]
    raw_blocks: Mapping[str, str]


def load_source_snapshot(connection: sqlite3.Connection, *, source_version_id: str) -> RevalidationSourceSnapshot:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT id FROM content_extraction_runs WHERE source_version_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
        (source_version_id,),
    ).fetchone()
    if row is None:
        raise ContentQuestionRevalidationError("source_blocks_not_extracted")
    blocks = [
        {"id": item["id"], "ordinal": item["ordinal"], "raw_text": item["raw_text"]}
        for item in connection.execute(
            "SELECT id,ordinal,raw_text FROM source_content_blocks WHERE extraction_run_id=? ORDER BY ordinal",
            (row["id"],),
        )
    ]
    if not blocks:
        raise ContentQuestionRevalidationError("source_blocks_missing")
    return RevalidationSourceSnapshot(
        source_version_id, row["id"], segment_questions(blocks),
        {str(block["id"]): str(block["raw_text"]) for block in blocks},
    )


def candidate_source_keys(candidates: Iterable[QuestionCandidate]) -> tuple[str, ...]:
    values = tuple(candidates)
    counts: dict[str, int] = {}
    for item in values:
        counts[item.source_question_no] = counts.get(item.source_question_no, 0) + 1
    return tuple(
        item.source_question_no if counts[item.source_question_no] == 1 else f"{item.question_type}:{item.source_question_no}"
        for item in values
    )


def verify_snapshot_matches_structured_questions(connection: sqlite3.Connection, snapshot: RevalidationSourceSnapshot) -> None:
    keys = candidate_source_keys(snapshot.candidates)
    rows = connection.execute(
        """SELECT q.source_question_no FROM questions q
           JOIN content_item_question_links l ON l.question_id=q.id
           JOIN content_items ci ON ci.id=l.content_item_id
           WHERE ci.source_version_id=? ORDER BY q.source_question_no""",
        (snapshot.source_version_id,),
    ).fetchall()
    found = tuple(row[0] for row in rows)
    if sorted(found) != sorted(keys):
        raise ContentQuestionRevalidationError(
            json.dumps({"reason":"structured_question_source_keys_mismatch","expected":sorted(keys),"found":sorted(found)}, ensure_ascii=False)
        )


KNOWLEDGE_MARKER = "\u3010\u77e5\u8bc6\u70b9\u3011"

def source_knowledge_labels(snapshot: RevalidationSourceSnapshot, candidate: QuestionCandidate) -> tuple[str, ...]:
    """Read labels from exact answer source blocks; labels remain evidence only."""
    values: list[str] = []
    for evidence in candidate.answer_evidence + candidate.analysis_evidence:
        raw = snapshot.raw_blocks.get(evidence.block_id, "")
        marker = raw.find(KNOWLEDGE_MARKER)
        if marker < 0:
            continue
        value = raw[marker + len(KNOWLEDGE_MARKER):].split("\u3010", 1)[0].strip()
        if value:
            values.append(value)
    return tuple(values)
