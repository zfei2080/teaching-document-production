"""Deterministic candidate extraction from structured math-exam DOCX files.

This module is intentionally conservative. It identifies numbered question and
answer/analysis blocks from a source Word document and returns source-linked
candidates. It does not infer missing values, classify difficulty, call an AI
model, or mark any candidate as deliverable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from docx_forensics import ParagraphFragment, extract_paragraphs

# Match only the paragraph prefix. Question paragraphs can contain internal line
# breaks, so requiring the rest of a paragraph to match would silently skip them.
QUESTION_RE = re.compile(r"^(\d{1,3})\.(?!【答案】)")
# Answer paragraphs can contain internal line breaks or have an empty answer
# line. Match the paragraph prefix only, then extract the remaining content.
ANSWER_RE = re.compile(r"^(\d{1,3})\.【答案】\s*(.*)", re.DOTALL)
KNOWLEDGE_RE = re.compile(r"^【知识点】\s*(.+)$")
ANALYSIS_MARKER_RE = re.compile(r"^【(?:解析|分析|解答)")
SECTION_MARKER_RE = re.compile(r"^第[ⅠⅡIVX]+卷|^[一二三四五六七八九十]、")


@dataclass(frozen=True)
class CandidateQuestion:
    number: str
    stem_fragment_ids: tuple[int, ...]
    answer_fragment_id: int | None
    knowledge_fragment_id: int | None
    analysis_fragment_ids: tuple[int, ...]
    stem_text: str
    answer: str | None
    knowledge_text: str | None
    analysis_text: str | None


def _is_option_line(text: str) -> bool:
    return bool(re.match(r"^[A-F][.．、]", text))


def _join(fragments: list[ParagraphFragment]) -> str:
    return "\n".join(fragment.text for fragment in fragments).strip()


def parse_candidates(path: str | Path) -> list[CandidateQuestion]:
    """Parse a DOCX source into conservative source-linked question candidates.

    Supported source layout:
    - numbered question paragraphs, followed by option/sub-question paragraphs;
    - an answer section where a paragraph begins with ``N.【答案】``;
    - optional ``【知识点】`` and analysis paragraphs following the answer.

    A candidate remains incomplete if its answer or analysis cannot be found.
    """
    fragments = extract_paragraphs(path)
    answer_start = next(
        (index for index, fragment in enumerate(fragments) if ANSWER_RE.match(fragment.text)),
        len(fragments),
    )
    question_fragments = fragments[:answer_start]
    answer_fragments = fragments[answer_start:]

    question_blocks: dict[str, list[ParagraphFragment]] = {}
    current_number: str | None = None
    for fragment in question_fragments:
        match = QUESTION_RE.match(fragment.text)
        if match:
            current_number = match.group(1)
            question_blocks[current_number] = [fragment]
            continue
        if SECTION_MARKER_RE.match(fragment.text):
            # Section labels belong to the paper layout, not to the prior question.
            current_number = None
            continue
        if current_number is not None:
            # Keep contiguous content, including options and multi-part stems.
            question_blocks[current_number].append(fragment)

    answer_blocks: dict[str, dict[str, object]] = {}
    current_answer: dict[str, object] | None = None
    for fragment in answer_fragments:
        answer_match = ANSWER_RE.match(fragment.text)
        if answer_match:
            number = answer_match.group(1)
            current_answer = {
                "answer_fragment": fragment,
                "answer": answer_match.group(2).strip(),
                "knowledge_fragment": None,
                "knowledge": None,
                "analysis_fragments": [],
            }
            answer_blocks[number] = current_answer
            continue
        if current_answer is None:
            continue
        knowledge_match = KNOWLEDGE_RE.match(fragment.text)
        if knowledge_match:
            current_answer["knowledge_fragment"] = fragment
            current_answer["knowledge"] = knowledge_match.group(1).strip()
            continue
        if ANALYSIS_MARKER_RE.match(fragment.text) or current_answer["analysis_fragments"]:
            current_answer["analysis_fragments"].append(fragment)

    candidates: list[CandidateQuestion] = []
    for number in sorted(question_blocks, key=lambda value: int(value)):
        question_block = question_blocks[number]
        answer_block = answer_blocks.get(number, {})
        answer_fragment = answer_block.get("answer_fragment")
        knowledge_fragment = answer_block.get("knowledge_fragment")
        analysis_fragments = answer_block.get("analysis_fragments", [])
        candidates.append(
            CandidateQuestion(
                number=number,
                stem_fragment_ids=tuple(fragment.index for fragment in question_block),
                answer_fragment_id=answer_fragment.index if answer_fragment else None,
                knowledge_fragment_id=knowledge_fragment.index if knowledge_fragment else None,
                analysis_fragment_ids=tuple(fragment.index for fragment in analysis_fragments),
                stem_text=_join(question_block),
                answer=answer_block.get("answer"),
                knowledge_text=answer_block.get("knowledge"),
                analysis_text=_join(analysis_fragments) if analysis_fragments else None,
            )
        )
    return candidates
