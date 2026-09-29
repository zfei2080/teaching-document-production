"""Deterministic source-derived question/solution segmentation.

This module only groups and slices already extracted source blocks. It never
creates mathematical text; every returned field remains bound to source blocks.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence

SECTION_RE = re.compile(r"^\s*[\u4e00-\u9fff]+[.\uff0e\u3001]\s*(?P<name>\u9009\u62e9\u9898|\u586b\u7a7a\u9898|\u8ba1\u7b97\u9898|\u89e3\u7b54\u9898)")
QUESTION_RE = re.compile(r"^\s*(?P<number>\d+)[.\uff0e\u3001]")
OPTION_RE = re.compile(r"(?:^|\s)(?P<label>[A-D])[.\uff0e\u3001]\s*")
ASSET_ONLY_OPTION_RE = re.compile(r"(?:^|\s)(?P<label>[A-D])(?P<delimiter>[^\s\x01]{1,4})\s*(?P<marker>\x01)")
ANSWER_HEADING = "\u3010\u7b54\u6848\u4e0e\u89e3\u6790\u3011"
ANSWER_MARKER = "\u3010\u7b54\u6848\u3011"
ANALYSIS_MARKER = "\u3010\u89e3\u6790\u3011"


class QuestionSegmentationError(ValueError):
    """Raised when the source structure is ambiguous instead of guessed."""


@dataclass(frozen=True)
class SourceBlock:
    id: str
    ordinal: int
    raw_text: str


@dataclass(frozen=True)
class FieldEvidence:
    block_id: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class QuestionCandidate:
    source_question_no: str
    question_type: str
    stem: str
    options: tuple[dict[str, object], ...]
    student_evidence: tuple[FieldEvidence, ...]
    option_evidence: tuple[FieldEvidence, ...]
    answer_evidence: tuple[FieldEvidence, ...]
    analysis_evidence: tuple[FieldEvidence, ...]
    source_block_ids: tuple[str, ...]


def _clean_display(value: str) -> str:
    # Word COM paragraph/cell end artifacts are evidence noise, not new math.
    return value.replace("\x01", "").replace("\x07", "").strip()


def _question_starts(blocks: Sequence[SourceBlock], *, start: int, end: int) -> list[int]:
    return [index for index in range(start, end) if QUESTION_RE.match(blocks[index].raw_text)]


def _option_slices(text: str) -> list[tuple[str, int, int, str | None, bool]]:
    """Return exact option slices without inventing text for Word image markers."""
    asset_matches = list(ASSET_ONLY_OPTION_RE.finditer(text))
    if asset_matches:
        labels = [match.group("label") for match in asset_matches]
        if len(asset_matches) < 2 or len(labels) != len(set(labels)):
            raise QuestionSegmentationError("unreadable_option_assets")
        return [
            (match.group("label"), match.start("label"), match.end("marker"), None, True)
            for match in asset_matches
        ]
    matches = list(OPTION_RE.finditer(text))
    result: list[tuple[str, int, int, str | None, bool]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        option_text = _clean_display(text[start:end])
        if not option_text:
            raise QuestionSegmentationError("empty_option_text")
        result.append((match.group("label"), match.start("label"), end, option_text, False))
    return result


def _section_ranges(blocks: Sequence[SourceBlock]) -> list[tuple[int, int, str]]:
    starts: list[tuple[int, str]] = []
    for index, block in enumerate(blocks):
        match = SECTION_RE.match(block.raw_text)
        if match:
            starts.append((index, match.group("name")))
    ranges: list[tuple[int, int, str]] = []
    for index, (start, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(blocks)
        ranges.append((start, end, name))
    return ranges


def _build_student_candidate(
    blocks: Sequence[SourceBlock], *, start: int, end: int, question_type: str, number: str
) -> QuestionCandidate:
    unit = blocks[start:end]
    if not unit:
        raise QuestionSegmentationError("empty_question_unit")
    student_blocks: list[SourceBlock] = []
    option_evidence: list[FieldEvidence] = []
    options: list[dict[str, object]] = []
    stem_parts: list[str] = []
    for block_index, block in enumerate(unit):
        text = block.raw_text
        slices = _option_slices(text)
        if slices:
            for label, option_start, option_end, option_text, asset_only in slices:
                label_start = option_start
                option = {"label": label, "text": option_text}
                if asset_only:
                    option["asset_only"] = True
                options.append(option)
                option_evidence.append(FieldEvidence(block.id, label_start, option_end, text[label_start:option_end]))
            prefix = _clean_display(text[:slices[0][1]])
            if prefix:
                stem_parts.append(prefix)
                student_blocks.append(block)
        else:
            cleaned = _clean_display(text)
            if cleaned:
                stem_parts.append(cleaned)
                student_blocks.append(block)
    if not stem_parts:
        raise QuestionSegmentationError("question_stem_missing")
    labels = [str(option["label"]) for option in options]
    if len(labels) != len(set(labels)):
        raise QuestionSegmentationError("duplicate_option_label")
    stem = "\n".join(stem_parts)
    stem_evidence = tuple(
        FieldEvidence(block.id, 0, len(block.raw_text), block.raw_text)
        for block in student_blocks
        if block.raw_text
    )
    return QuestionCandidate(
        source_question_no=number, question_type=question_type, stem=stem, options=tuple(options),
        student_evidence=stem_evidence, option_evidence=tuple(option_evidence),
        answer_evidence=tuple(), analysis_evidence=tuple(),
        source_block_ids=tuple(block.id for block in unit),
    )


def _answer_evidence(
    blocks: Sequence[SourceBlock], *, start: int, end: int, number: str
) -> tuple[tuple[FieldEvidence, ...], tuple[FieldEvidence, ...]]:
    answer_blocks = blocks[start:end]
    answer_evidence: list[FieldEvidence] = []
    analysis_evidence: list[FieldEvidence] = []
    for block in answer_blocks:
        text = block.raw_text
        answer_marker_index = text.find(ANSWER_MARKER)
        analysis_marker_index = text.find(ANALYSIS_MARKER)
        if answer_marker_index >= 0:
            payload_start = answer_marker_index + len(ANSWER_MARKER)
            payload_end = analysis_marker_index if analysis_marker_index > payload_start else len(text)
            payload = _clean_display(text[payload_start:payload_end])
            if payload:
                answer_evidence.append(FieldEvidence(block.id, answer_marker_index, payload_end, text[answer_marker_index:payload_end]))
        if analysis_marker_index >= 0:
            payload = _clean_display(text[analysis_marker_index + len(ANALYSIS_MARKER):])
            if payload:
                analysis_evidence.append(FieldEvidence(block.id, analysis_marker_index, len(text), text[analysis_marker_index:]))
        elif answer_blocks and block is not answer_blocks[0] and _clean_display(text):
            # Continuation lines after the first answer line remain internal analysis evidence.
            analysis_evidence.append(FieldEvidence(block.id, 0, len(text), text))
    return tuple(answer_evidence), tuple(analysis_evidence)


def segment_questions(blocks: Iterable[dict[str, object] | SourceBlock]) -> tuple[QuestionCandidate, ...]:
    normalized: list[SourceBlock] = []
    for value in blocks:
        if isinstance(value, SourceBlock):
            normalized.append(value)
        elif isinstance(value, dict) and isinstance(value.get("id"), str) and isinstance(value.get("ordinal"), int) and isinstance(value.get("raw_text"), str):
            normalized.append(SourceBlock(value["id"], value["ordinal"], value["raw_text"]))
        else:
            raise QuestionSegmentationError("source_block_shape_invalid")
    normalized.sort(key=lambda block: block.ordinal)
    results: list[QuestionCandidate] = []
    # Some sources have no standalone answer heading.  Treat the first numbered
    # block containing the answer marker as the answer boundary, but only after
    # the student sections have been scanned; this prevents answer entries from
    # being mistaken for source questions.
    answer_start = next((
        i for i, block in enumerate(normalized)
        if ANSWER_HEADING in block.raw_text
        or (QUESTION_RE.match(block.raw_text) is not None and ANSWER_MARKER in block.raw_text)
    ), None)
    student_end = answer_start if answer_start is not None else len(normalized)
    answer_entry_start = (
        answer_start + 1
        if answer_start is not None and ANSWER_HEADING in normalized[answer_start].raw_text
        else answer_start
    )
    for section_start, section_end, question_type in _section_ranges(normalized[:student_end]):
        starts = _question_starts(normalized, start=section_start + 1, end=section_end)
        for position, question_start in enumerate(starts):
            question_end = starts[position + 1] if position + 1 < len(starts) else section_end
            match = QUESTION_RE.match(normalized[question_start].raw_text)
            assert match is not None
            candidate = _build_student_candidate(
                normalized, start=question_start, end=question_end,
                question_type=question_type, number=match.group("number"),
            )
            results.append(candidate)
    if answer_start is None:
        return tuple(results)
    assert answer_entry_start is not None
    answer_starts = _question_starts(normalized, start=answer_entry_start, end=len(normalized))
    answer_by_number: dict[str, tuple[tuple[FieldEvidence, ...], tuple[FieldEvidence, ...]]] = {}
    for position, start in enumerate(answer_starts):
        end = answer_starts[position + 1] if position + 1 < len(answer_starts) else len(normalized)
        match = QUESTION_RE.match(normalized[start].raw_text)
        assert match is not None
        answer_by_number[match.group("number")] = _answer_evidence(
            normalized, start=start, end=end, number=match.group("number"),
        )
    output: list[QuestionCandidate] = []
    for candidate in results:
        answer, analysis = answer_by_number.get(candidate.source_question_no, (tuple(), tuple()))
        output.append(QuestionCandidate(
            source_question_no=candidate.source_question_no, question_type=candidate.question_type,
            stem=candidate.stem, options=candidate.options,
            student_evidence=candidate.student_evidence, option_evidence=candidate.option_evidence,
            answer_evidence=answer, analysis_evidence=analysis,
            source_block_ids=candidate.source_block_ids,
        ))
    return tuple(output)
