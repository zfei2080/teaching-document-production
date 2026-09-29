"""Fail-closed asset-semantics verification for narrowly defined no-image items.

This validator is deliberately *not* an image understanding system.  It can only
pass a small, source-locked set of question forms where all of the following are
proved: the exact source document is intact, the source range contains no
embedded media, the question contains no visual-reference language, the database
contains no linked assets, and the normalized stem matches a reviewed rule.
Everything else remains ``unsupported``.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from docx_candidate_parser import parse_candidates
from docx_forensics import extract_paragraphs
from docx_media_forensics import paragraph_media

ROOT = Path(__file__).parent
VALIDATOR_ID = "asset-semantics-no-image-v1"
VALIDATOR_VERSION = "1.0.0"

# These are the only reviewed no-image forms in golden-source-001.  The stem
# SHA-256 prevents an otherwise similarly numbered, later-edited item from
# inheriting this exemption.  Adding a rule requires a code review and tests.
REVIEWED_NO_IMAGE_STEM_HASHES: dict[str, str] = {
    "2": "33e61e91c0d1979fedbd30a684996d5c20977a7c80d588ec614b6d2569a9b86d",
    "3": "5f6a9022237dbee272352764fb125f32a7c42f97a89db81180b38eccc5ac90f7",
    "6": "79fcbb662cf7ad80379c9d73dd08be1ab0dad53cc4fe736fe8d12d7d2a81e565",
    "13": "672620e797be794aebf9e94d47899699075327d6fdd923ad3326c3361bba030a",
    "15": "38a0a4c57719d58fe49ad26b6ec5a14f3068dc15610b7951d23f5fca2c7317d7",
    "20": "11d5974a59b8df8e107376f4c103139df3cd409e33d2182596fbf01b620f95a4",
}
VISUAL_REFERENCE_RE = re.compile(r"如图|图中|图示|下图|上图|图形|画图|作图|网格|坐标系")


def normalize_text(value: str | None) -> str:
    return "".join((value or "").split())


def text_sha256(value: str | None) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def _result(status: str, **evidence: Any) -> tuple[str, dict[str, Any]]:
    return status, evidence


def validate(conn: sqlite3.Connection, question_id: str) -> tuple[str, dict[str, Any]]:
    """Return a pass only for a fully evidenced, reviewed no-image exemption."""
    row = conn.execute(
        """SELECT q.id, q.stem, q.source_question_no, q.source_document_id,
                  s.relative_path, s.file_hash
           FROM questions q JOIN source_documents s ON s.id=q.source_document_id
           WHERE q.id=?""",
        (question_id,),
    ).fetchone()
    if row is None:
        return _result("unsupported", reason="question_or_source_document_missing")

    question_no = str(row["source_question_no"] or "")
    expected_stem_hash = REVIEWED_NO_IMAGE_STEM_HASHES.get(question_no)
    stem_hash = text_sha256(row["stem"])
    if not expected_stem_hash or stem_hash != expected_stem_hash:
        return _result(
            "unsupported",
            reason="no_reviewed_source_locked_rule",
            source_question_no=question_no,
            stem_hash=stem_hash,
        )
    if VISUAL_REFERENCE_RE.search(row["stem"] or ""):
        return _result("unsupported", reason="visual_reference_language", source_question_no=question_no)

    asset_count = conn.execute(
        "SELECT COUNT(*) FROM question_assets WHERE question_id=?", (question_id,)
    ).fetchone()[0]
    if asset_count:
        return _result("unsupported", reason="linked_question_assets_present", asset_count=asset_count)

    source_path = ROOT / row["relative_path"]
    if not source_path.is_file():
        return _result("unsupported", reason="source_file_missing", source_path=str(source_path))
    actual_file_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if actual_file_hash != row["file_hash"]:
        return _result("unsupported", reason="source_file_hash_mismatch")

    candidates = parse_candidates(source_path)
    current_index = next((i for i, item in enumerate(candidates) if item.number == question_no), None)
    if current_index is None:
        return _result("unsupported", reason="source_question_not_reparsed", source_question_no=question_no)
    candidate = candidates[current_index]

    # Reconstruct the imported stem independently, so numbering alone cannot
    # attach a rule to altered source content.
    from import_review_candidates import split_options

    reparsed_stem, _ = split_options(candidate.stem_text)
    if text_sha256(reparsed_stem) != stem_hash:
        return _result("unsupported", reason="stored_stem_not_equal_to_source")

    fragments = {fragment.index: fragment for fragment in extract_paragraphs(source_path)}
    provenance_rows = conn.execute(
        """SELECT sf.paragraph_index, qsf.source_hash
           FROM question_source_fragments qsf
           JOIN source_fragments sf ON sf.id=qsf.source_fragment_id
           WHERE qsf.question_id=? AND qsf.field_name='stem'""",
        (question_id,),
    ).fetchall()
    source_indexes = set(candidate.stem_fragment_ids)
    if not source_indexes or {item["paragraph_index"] for item in provenance_rows} != source_indexes:
        return _result("unsupported", reason="stem_provenance_not_exact")
    if any(fragments.get(item["paragraph_index"]) is None or fragments[item["paragraph_index"]].sha256 != item["source_hash"] for item in provenance_rows):
        return _result("unsupported", reason="stem_provenance_hash_mismatch")

    start = min(candidate.stem_fragment_ids)
    end = (
        min(candidates[current_index + 1].stem_fragment_ids)
        if current_index + 1 < len(candidates)
        else 10**9
    )
    media = {
        name
        for item in paragraph_media(source_path)
        if start <= item.paragraph_index < end
        for name in item.filenames
    }
    if media:
        return _result(
            "unsupported",
            reason="embedded_media_in_question_source_range",
            paragraph_range=[start, end],
            media_filenames=sorted(media),
        )

    return _result(
        "pass",
        rule_id=f"golden-source-001-no-image-q{question_no}",
        source_question_no=question_no,
        stem_hash=stem_hash,
        source_file_hash=actual_file_hash,
        paragraph_range=[start, end],
        media_filenames=[],
        linked_asset_count=0,
    )
