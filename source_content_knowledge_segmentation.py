"""Deterministic source-derived knowledge-note and worked-example segmentation."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence

TYPICAL_MARKER = "\u3010\u5178\u578b\u4f8b\u9898\u3011"
LEARNING_GOAL_MARKER = "\u3010\u5b66\u4e60\u76ee\u6807\u3011"
POINT_RE = re.compile(r"^\u8981\u70b9[\u4e00-\u9fff]+\u3001")
TYPE_RE = re.compile(r"^\u7c7b\u578b[\u4e00-\u9fff]+[\u3001\u3002]")
EXAMPLE_RE = re.compile(r"^\s*(?P<number>\d+)[,\u3001.\uff0e]")
VARIANT_MARKER = "\u3010\u53d8\u5f0f\u3011"
STUDENT_TO_INTERNAL_MARKERS = (
    "\u3010\u601d\u8def\u70b9\u62e8\u3011", "\u3010\u7b54\u6848", "\u3010\u89e3\u6790", "\u3010\u603b\u7ed3\u5347\u534e\u3011",
)


class KnowledgeSegmentationError(ValueError):
    """Raised when extracted blocks do not provide deterministic identities."""


@dataclass(frozen=True)
class SourceBlock:
    id: str
    ordinal: int
    raw_text: str


@dataclass(frozen=True)
class ContentEvidence:
    block_id: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class ContentCandidate:
    item_kind: str
    title: str
    student_text: str
    student_evidence: tuple[ContentEvidence, ...]
    internal_evidence: tuple[ContentEvidence, ...]
    source_block_ids: tuple[str, ...]


def _clean(value: str) -> str:
    return value.replace("\x01", "").replace("\x07", "").strip()


def _normalize(values: Iterable[dict[str, object] | SourceBlock]) -> list[SourceBlock]:
    rows: list[SourceBlock] = []
    for value in values:
        if isinstance(value, SourceBlock): rows.append(value)
        elif isinstance(value, dict) and isinstance(value.get("id"), str) and isinstance(value.get("ordinal"), int) and isinstance(value.get("raw_text"), str):
            rows.append(SourceBlock(value["id"],value["ordinal"],value["raw_text"]))
        else: raise KnowledgeSegmentationError("source_block_shape_invalid")
    return sorted(rows,key=lambda row: row.ordinal)


def _evidence(blocks: Sequence[SourceBlock]) -> tuple[ContentEvidence, ...]:
    return tuple(ContentEvidence(block.id,0,len(block.raw_text),block.raw_text) for block in blocks if _clean(block.raw_text))


def _student_text(blocks: Sequence[SourceBlock]) -> str:
    values=[_clean(block.raw_text) for block in blocks if _clean(block.raw_text)]
    if not values: raise KnowledgeSegmentationError("student_content_missing")
    return "\n".join(values)


def _note_candidates(blocks: Sequence[SourceBlock], *, end: int) -> list[ContentCandidate]:
    starts=[]
    for index in range(end):
        text=_clean(blocks[index].raw_text)
        if text == LEARNING_GOAL_MARKER or POINT_RE.match(text): starts.append(index)
    output=[]
    for position,start in enumerate(starts):
        stop=starts[position+1] if position+1<len(starts) else end
        segment=blocks[start:stop]
        if not _evidence(segment): continue
        output.append(ContentCandidate(
            item_kind="knowledge_note", title=_clean(segment[0].raw_text), student_text=_student_text(segment),
            student_evidence=_evidence(segment), internal_evidence=tuple(), source_block_ids=tuple(block.id for block in segment),
        ))
    return output


def _example_starts(blocks: Sequence[SourceBlock], *, start: int, end: int) -> list[int]:
    starts=[]
    for index in range(start,end):
        text=_clean(blocks[index].raw_text)
        if EXAMPLE_RE.match(text) or text.startswith(VARIANT_MARKER): starts.append(index)
    return starts


def _worked_example_candidates(blocks: Sequence[SourceBlock], *, start: int) -> list[ContentCandidate]:
    output=[]
    type_positions=[index for index in range(start,len(blocks)) if TYPE_RE.match(_clean(blocks[index].raw_text))]
    for type_index,group_start in enumerate(type_positions):
        group_end=type_positions[type_index+1] if type_index+1<len(type_positions) else len(blocks)
        title=_clean(blocks[group_start].raw_text)
        starts=_example_starts(blocks,start=group_start+1,end=group_end)
        for position,item_start in enumerate(starts):
            item_end=starts[position+1] if position+1<len(starts) else group_end
            segment=blocks[item_start:item_end]
            boundary=next((i for i,block in enumerate(segment) if _clean(block.raw_text).startswith(STUDENT_TO_INTERNAL_MARKERS)),len(segment))
            student_blocks=segment[:boundary]
            internal_blocks=segment[boundary:]
            if not _evidence(student_blocks): continue
            output.append(ContentCandidate(
                item_kind="worked_example", title=title + (" / variant" if _clean(segment[0].raw_text).startswith(VARIANT_MARKER) else ""),
                student_text=_student_text(student_blocks), student_evidence=_evidence(student_blocks),
                internal_evidence=_evidence(internal_blocks), source_block_ids=tuple(block.id for block in segment),
            ))
    return output


def segment_knowledge_and_examples(values: Iterable[dict[str, object] | SourceBlock]) -> tuple[ContentCandidate, ...]:
    blocks=_normalize(values)
    typical_index=next((index for index,block in enumerate(blocks) if TYPICAL_MARKER in _clean(block.raw_text)),None)
    first_type=next((index for index,block in enumerate(blocks) if TYPE_RE.match(_clean(block.raw_text))),None)
    content_boundary = typical_index if typical_index is not None else (first_type if first_type is not None else len(blocks))
    candidates=_note_candidates(blocks,end=content_boundary)
    if first_type is not None: candidates.extend(_worked_example_candidates(blocks,start=first_type))
    identities=[(candidate.item_kind,candidate.title,candidate.source_block_ids) for candidate in candidates]
    if len(identities)!=len(set(identities)): raise KnowledgeSegmentationError("duplicate_content_candidate_identity")
    return tuple(candidates)
