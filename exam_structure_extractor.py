from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

SECTION_PATTERNS = [
    re.compile(r"^[一二三四五六七八九十]+[、.]\s*(.+)$"),
    re.compile(r"^第[一二三四五六七八九十]+部分\s*(.+)?$"),
]
QUESTION_PATTERNS = [
    re.compile(r"^(?P<number>\d+)[.、．]\s*(?P<body>.*)$"),
    re.compile(r"^(?P<number>\d+)\s*[)]\s*(?P<body>.*)$"),
    re.compile(r"^[(（](?P<number>\d+)[)）]\s*(?P<body>.*)$"),
]
OPTION_PATTERN = re.compile(r"^(?P<label>[A-D])[.、．]\s*(?P<body>.+)$")
ANSWER_MARKERS = (
    "答案",
    "参考答案",
    "解答",
    "解析",
)
UNSUPPORTED_MARKERS = (
    "[图片]",
    "[图]",
    "见图",
    "如图",
    "图示",
    "附图",
    "公式",
    "推导式",
)


@dataclass
class Segment:
    kind: str
    text: str
    line_index: int
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateQuestion:
    number: str
    prompt: str
    evidence: list[dict[str, Any]]
    options: list[dict[str, Any]] = field(default_factory=list)
    answer_missing: bool = True
    status: str = "candidate"
    quarantine_reason: str | None = None
    subjective: bool = False


@dataclass
class ExtractionResult:
    segments: list[Segment]
    questions: list[CandidateQuestion]
    quarantined: list[dict[str, Any]]
    approved_questions: list[Any] = field(default_factory=list)
    approved_answers: list[Any] = field(default_factory=list)


def extract_exam_structure(text: str) -> ExtractionResult:
    lines = [_normalize_line(line) for line in text.splitlines()]
    segments: list[Segment] = []
    questions: list[CandidateQuestion] = []
    quarantined: list[dict[str, Any]] = []
    current_question: CandidateQuestion | None = None
    seen_numbers: set[str] = set()

    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue

        if _is_unsupported(line):
            payload = {
                "kind": "structured",
                "status": "unsupported/quarantined",
                "line_index": index,
                "text": line,
                "reason": "image-or-formula-unbound",
            }
            quarantined.append(payload)
            segments.append(
                Segment(
                    kind="quarantined",
                    text=line,
                    line_index=index,
                    status="unsupported/quarantined",
                    metadata={"reason": "image-or-formula-unbound"},
                )
            )
            current_question = None
            continue

        section_match = _match_first(SECTION_PATTERNS, line)
        if section_match:
            segments.append(
                Segment(
                    kind="section",
                    text=line,
                    line_index=index,
                    status="candidate",
                    metadata={"title": section_match.group(0)},
                )
            )
            current_question = None
            continue

        question_match = _match_first(QUESTION_PATTERNS, line)
        if question_match:
            number = question_match.group("number")
            body = question_match.group("body").strip()
            status = "candidate"
            quarantine_reason = None
            if number in seen_numbers:
                status = "unsupported/quarantined"
                quarantine_reason = "duplicate-question-number"
                quarantined.append(
                    {
                        "kind": "structured",
                        "status": status,
                        "line_index": index,
                        "text": line,
                        "reason": quarantine_reason,
                    }
                )
            else:
                seen_numbers.add(number)
            subjective = _is_subjective(body)
            current_question = CandidateQuestion(
                number=number,
                prompt=body,
                evidence=[{"line_index": index, "text": line}],
                status=status,
                quarantine_reason=quarantine_reason,
                subjective=subjective,
            )
            questions.append(current_question)
            segments.append(
                Segment(
                    kind="question",
                    text=line,
                    line_index=index,
                    status=status,
                    metadata={
                        "number": number,
                        "subjective": subjective,
                        "answer_missing": True,
                        "quarantine_reason": quarantine_reason,
                    },
                )
            )
            continue

        option_match = OPTION_PATTERN.match(line)
        if option_match and current_question and current_question.status == "candidate":
            option_payload = {
                "label": option_match.group("label"),
                "text": option_match.group("body"),
                "line_index": index,
            }
            current_question.options.append(option_payload)
            current_question.evidence.append({"line_index": index, "text": line})
            segments.append(
                Segment(
                    kind="option",
                    text=line,
                    line_index=index,
                    status="candidate",
                    metadata={"question_number": current_question.number, **option_payload},
                )
            )
            continue

        if any(marker in line for marker in ANSWER_MARKERS):
            segments.append(
                Segment(
                    kind="answer_reference",
                    text=line,
                    line_index=index,
                    status="unsupported/quarantined",
                    metadata={"reason": "answers-not-approved"},
                )
            )
            quarantined.append(
                {
                    "kind": "structured",
                    "status": "unsupported/quarantined",
                    "line_index": index,
                    "text": line,
                    "reason": "answers-not-approved",
                }
            )
            current_question = None
            continue

        kind = "header" if not questions else "question_text"
        segments.append(
            Segment(
                kind=kind,
                text=line,
                line_index=index,
                status="candidate",
                metadata={},
            )
        )
        if current_question and current_question.status == "candidate":
            current_question.prompt = _join_prompt(current_question.prompt, line)
            current_question.evidence.append({"line_index": index, "text": line})
            if _is_subjective(line):
                current_question.subjective = True

    return ExtractionResult(
        segments=segments,
        questions=questions,
        quarantined=quarantined,
        approved_questions=[],
        approved_answers=[],
    )


def _normalize_line(line: str) -> str:
    return line.replace("\u3000", " ").strip()


def _match_first(patterns: Iterable[re.Pattern[str]], text: str) -> re.Match[str] | None:
    for pattern in patterns:
        match = pattern.match(text)
        if match:
            return match
    return None


def _is_subjective(text: str) -> bool:
    return any(token in text for token in ("简答", "解答", "论述", "作文", "证明", "计算"))


def _join_prompt(prompt: str, line: str) -> str:
    return line if not prompt else f"{prompt} {line}".strip()


def _is_unsupported(text: str) -> bool:
    return any(marker in text for marker in UNSUPPORTED_MARKERS)
