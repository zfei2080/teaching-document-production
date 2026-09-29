"""Import only complete first-batch DOCX candidates into the review queue.

This importer is intentionally narrow: it accepts the first 20 source-linked
objective candidates from the golden DOCX, writes them as needs_review/pending,
and never promotes a question to approved or deliverable.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from candidate_validation import validate_candidate
from docx_candidate_parser import CandidateQuestion, parse_candidates
from docx_forensics import extract_paragraphs

ROOT = Path(__file__).parent
DEV_DB = ROOT / "data" / "dev" / "teaching_docs_dev.db"
PROVENANCE_SCHEMA = ROOT / "schema_v2_1.sql"
SOURCE_DOC = ROOT / "data" / "dev" / "golden-samples" / "golden_source_001.docx"
SOURCE_ID = "golden-source-001"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


OPTION_TOKEN_RE = re.compile(r"(?<!\S)([A-F])[.．、]\s*")


def split_options(stem_text: str) -> tuple[str, list[str]]:
    """Split conventional A-F choices, including tab-separated Word options."""
    text = "\n".join(line.strip() for line in stem_text.splitlines() if line.strip())
    matches = list(OPTION_TOKEN_RE.finditer(text))
    if len(matches) < 2 or matches[0].group(1) != "A":
        return text, []

    stem = text[:matches[0].start()].strip()
    options = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end():end].strip()
        if not value:
            return text, []
        options.append(f"{match.group(1)}. {value}")
    return stem, options


def question_type(candidate: CandidateQuestion, options: list[str]) -> str:
    if options:
        return "选择题"
    if "______" in candidate.stem_text or "____" in candidate.stem_text:
        return "填空题"
    return "待分类"


def content_hash(stem: str, options: list[str], answer: str, analysis: str) -> str:
    payload = {"stem": stem, "options": options, "answer": answer, "analysis": analysis}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def main() -> None:
    if not DEV_DB.exists() or not SOURCE_DOC.exists():
        raise FileNotFoundError("Development database or source DOCX is missing")

    candidates = parse_candidates(SOURCE_DOC)
    reviewable = [candidate for candidate in candidates if validate_candidate(candidate).eligible_for_review]
    fragments = {fragment.index: fragment for fragment in extract_paragraphs(SOURCE_DOC)}

    conn = sqlite3.connect(DEV_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            conn.executescript(PROVENANCE_SCHEMA.read_text(encoding="utf-8"))
            conn.execute(
                """INSERT OR IGNORE INTO source_documents
                (id, relative_path, file_hash, file_type, source_label, copyright_status, parse_status)
                VALUES (?, ?, ?, 'docx', ?, 'unknown', 'parsed')""",
                (SOURCE_ID, SOURCE_DOC.relative_to(ROOT).as_posix(), file_sha256(SOURCE_DOC), "B2 golden source sample"),
            )
            for candidate in reviewable:
                stem, options = split_options(candidate.stem_text)
                source_fragment_ids: dict[int, str] = {}
                indexes = set(candidate.stem_fragment_ids)
                indexes.update(index for index in (candidate.answer_fragment_id, candidate.knowledge_fragment_id) if index is not None)
                indexes.update(candidate.analysis_fragment_ids)
                for index in indexes:
                    fragment = fragments[index]
                    fragment_id = f"golden-source-001-p{index:03d}"
                    source_fragment_ids[index] = fragment_id
                    conn.execute(
                        """INSERT OR REPLACE INTO source_fragments
                        (id, source_document_id, location_type, paragraph_index, question_number, raw_text, raw_hash)
                        VALUES (?, ?, 'paragraph', ?, ?, ?, ?)""",
                        (fragment_id, SOURCE_ID, index, candidate.number, fragment.text, fragment.sha256),
                    )

                question_id = f"golden-q{int(candidate.number):03d}"
                conn.execute(
                    """INSERT OR REPLACE INTO questions
                    (id, stem, options_json, answer, analysis, question_type, difficulty, stage, grade_level,
                     source_document_id, source_fragment_id, source_question_no, source_page, content_hash,
                     extraction_status, quality_status, review_status)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, '初中', '初中', ?, ?, ?, NULL, ?,
                            'structured', 'needs_review', 'pending')""",
                    (
                        question_id,
                        stem,
                        json.dumps(options, ensure_ascii=False),
                        candidate.answer,
                        candidate.analysis_text,
                        question_type(candidate, options),
                        SOURCE_ID,
                        source_fragment_ids[candidate.stem_fragment_ids[0]],
                        candidate.number,
                        content_hash(stem, options, candidate.answer or "", candidate.analysis_text or ""),
                    ),
                )
                field_sources = {
                    "stem": candidate.stem_fragment_ids,
                    "answer": (candidate.answer_fragment_id,),
                    "knowledge_point": (candidate.knowledge_fragment_id,),
                    "analysis": candidate.analysis_fragment_ids,
                }
                if options:
                    field_sources["options"] = candidate.stem_fragment_ids
                for field_name, indexes_for_field in field_sources.items():
                    for index in indexes_for_field:
                        if index is None:
                            raise RuntimeError(f"Missing provenance for {question_id}/{field_name}")
                        fragment = fragments[index]
                        conn.execute(
                            """INSERT OR REPLACE INTO question_source_fragments
                            (question_id, source_fragment_id, field_name, source_hash)
                            VALUES (?, ?, ?, ?)""",
                            (question_id, source_fragment_ids[index], field_name, fragment.sha256),
                        )

        print(f"REVIEW_CANDIDATES_IMPORTED={len(reviewable)}")
        print("DEV_QUESTION_COUNT=", conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0])
        print("DELIVERY_ELIGIBLE=", conn.execute("SELECT COUNT(*) FROM questions WHERE quality_status='approved' AND review_status='approved'").fetchone()[0])
        print("PENDING_REVIEW=", conn.execute("SELECT COUNT(*) FROM questions WHERE quality_status='needs_review' AND review_status='pending'").fetchone()[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
