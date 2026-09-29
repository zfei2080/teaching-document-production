"""Suggest curriculum mappings from keyword matching.

Classifier output is reviewable candidate evidence only.  It must never make a
question-to-textbook or knowledge-point mapping eligible for textbook-scope
approval; that still requires the controlled, source-bound import and audit
workflow.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

CLASSIFIER_ID = "keyword_classifier"
CLASSIFIER_VERSION = "v1"
DEFAULT_CONFIDENCE_THRESHOLD = 0.35
TOP_K = 3


class ClassificationWriteDisabled(RuntimeError):
    """Raised when legacy code attempts to persist automatic classifications."""


# ──────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────

def extract_chinese_keywords(text: str) -> list[str]:
    """Extract all unique 2-4 character Chinese substrings from text."""
    # keep only Chinese characters
    chinese = re.sub(r'[^\u4e00-\u9fff]', '', text)
    seen: set[str] = set()
    result: list[str] = []
    for length in (4, 3, 2):
        for i in range(len(chinese) - length + 1):
            kw = chinese[i:i + length]
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
    return result


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def score_question_node(stem: str, node_name: str) -> float:
    """Return a deterministic keyword-overlap score for a candidate preview.

    A complete curriculum-node label occurring in the question is stronger
    evidence than its component n-grams.  Score it as an exact candidate match
    before the conservative n-gram fallback.  This still has no authority to
    write mappings or approve textbook scope.
    """
    normalized_stem = re.sub(r"\s+", "", stem)
    normalized_node_name = re.sub(r"\s+", "", node_name)
    if len(normalized_node_name) >= 2 and normalized_node_name in normalized_stem:
        return 1.0
    stem_kws = extract_chinese_keywords(stem)
    node_kws = extract_chinese_keywords(node_name)
    if not node_kws or not stem_kws:
        return 0.0
    covered = sum(1 for kw in stem_kws if kw in node_name)
    matched = sum(1 for kw in node_kws if kw in stem)
    precision = covered / len(stem_kws)
    recall = matched / len(node_kws)
    return _f1(precision, recall)


def score_question_nodes(
    stem: str,
    nodes: list[tuple[str, str]],   # [(node_id, node_name), ...]
) -> list[tuple[str, float]]:
    """Return [(node_id, score), ...] sorted descending by score."""
    scored = [(nid, score_question_node(stem, name)) for nid, name in nodes]
    return sorted(scored, key=lambda x: x[1], reverse=True)


# ──────────────────────────────────────────────────────────────
# Hash helpers
# ──────────────────────────────────────────────────────────────

def _sha256(obj: Any) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _mapping_hash(question_id: str, textbook_id: str, node_id: str, kp_ids: list[str]) -> str:
    return _sha256({
        "question_id": question_id,
        "textbook_id": textbook_id,
        "curriculum_node_id": node_id,
        "knowledge_point_ids": sorted(kp_ids),
    })


def _kp_mapping_hash(question_id: str, kp_id: str, node_id: str) -> str:
    return _sha256({
        "question_id": question_id,
        "knowledge_point_id": kp_id,
        "curriculum_node_id": node_id,
    })


# ──────────────────────────────────────────────────────────────
# DB write
# ──────────────────────────────────────────────────────────────

def write_classification_results(
    conn: sqlite3.Connection,
    question_id: str,
    textbook_id: str,
    matches: list[dict],     # [{node_id, node_name, stage, confidence}]
    import_run_id: str,
    classifier_run_id: str,
) -> None:
    """Refuse the legacy automatic write path.

    Candidate keyword evidence cannot be promoted into textbook-scope or
    knowledge-point mappings. The controlled import-and-audit path is the only
    authoritative mapping writer.
    """
    raise ClassificationWriteDisabled(
        "automatic keyword classification cannot write textbook or knowledge mappings"
    )

    # Retained below for source-history readability; unreachable by design.
    for match in matches:
        node_id = match["node_id"]
        node_name = match["node_name"]
        stage = match["stage"]
        confidence = match["confidence"]

        # 1. knowledge_point (upsert-or-skip)
        kp_id = f"auto-kp:{node_id}"
        conn.execute(
            """INSERT OR IGNORE INTO knowledge_points
               (id, canonical_name, knowledge_type, stage_scope, review_status, version, created_at)
               VALUES (?, ?, 'concept', ?, 'pending', 'auto-v1', ?)""",
            (kp_id, node_name, stage, datetime.now(timezone.utc).isoformat()),
        )

        # 2. curriculum_knowledge_points (upsert-or-skip)
        conn.execute(
            """INSERT OR IGNORE INTO curriculum_knowledge_points
               (curriculum_node_id, knowledge_point_id, relation_type)
               VALUES (?, ?, 'covers')""",
            (node_id, kp_id),
        )

        kp_ids = [kp_id]
        mhash = _mapping_hash(question_id, textbook_id, node_id, kp_ids)
        kp_hash = _kp_mapping_hash(question_id, kp_id, node_id)

        # 3. question_textbooks: keyword output is a candidate, never approval.
        conn.execute(
            """INSERT OR REPLACE INTO question_textbooks
               (question_id, textbook_id, curriculum_node_id, fit_status,
                classifier_run_id, confidence, classification_method)
               VALUES (?, ?, ?, 'pending', ?, ?, 'candidate')""",
            (question_id, textbook_id, node_id, classifier_run_id, confidence),
        )

        # 4. question_textbook_imports (upsert-or-skip)
        conn.execute(
            """INSERT OR IGNORE INTO question_textbook_imports
               (question_id, textbook_id, curriculum_node_id, import_run_id, mapping_hash)
               VALUES (?, ?, ?, ?, ?)""",
            (question_id, textbook_id, node_id, import_run_id, mhash),
        )

        # 5. question_knowledge_points: retain suggestion provenance only.
        conn.execute(
            """INSERT OR REPLACE INTO question_knowledge_points
               (question_id, knowledge_point_id, relation_type,
                classifier_run_id, confidence, classification_method)
               VALUES (?, ?, 'primary', ?, ?, 'candidate')""",
            (question_id, kp_id, classifier_run_id, confidence),
        )

        # 6. question_knowledge_point_imports (upsert-or-skip)
        conn.execute(
            """INSERT OR IGNORE INTO question_knowledge_point_imports
               (question_id, knowledge_point_id, import_run_id, mapping_hash)
               VALUES (?, ?, ?, ?)""",
            (question_id, kp_id, import_run_id, kp_hash),
        )


# ──────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────

def classify_questions(
    conn: sqlite3.Connection,
    question_ids: list[str],
    textbook_id: str,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    dry_run: bool = False,
) -> dict[str, list[dict]]:
    """
    Classify questions to curriculum nodes using keyword matching.

    Returns {question_id: [{node_id, node_name, stage, confidence}, ...]}
    Only entries with confidence >= threshold are included.
    """
    # Load nodes for this textbook
    nodes = conn.execute(
        """SELECT id, name, stage FROM curriculum_nodes
           WHERE textbook_id=? AND status='active'""",
        (textbook_id,),
    ).fetchall()
    node_pairs = [(r[0], r[1]) for r in nodes]
    node_meta = {r[0]: {"name": r[1], "stage": r[2]} for r in nodes}

    # Load stems
    questions = conn.execute(
        "SELECT id, stem FROM questions WHERE id IN ({})".format(
            ",".join("?" * len(question_ids))
        ),
        question_ids,
    ).fetchall()

    results: dict[str, list[dict]] = {}

    # Build per-question matches
    for qid, stem in questions:
        scored = score_question_nodes(stem, node_pairs)
        top_matches = [
            {
                "node_id": nid,
                "node_name": node_meta[nid]["name"],
                "stage": node_meta[nid]["stage"],
                "confidence": round(score, 4),
            }
            for nid, score in scored[:TOP_K]
            if score >= confidence_threshold
        ]
        if top_matches:
            results[qid] = top_matches

    # Classifier output is a preview only. It intentionally has no persistence
    # path, even when legacy callers omit ``dry_run``.
    return results
