"""Fail-closed mathematical validation for source-derived question candidates.

The contract derives only explicitly supported answers from student-visible source
content, then compares the result with the separate source answer evidence.  It
never uses an answer or analysis as input to mathematical derivation.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import unicodedata
from typing import Iterable

from source_content_question_segmentation import ANSWER_MARKER, QuestionCandidate

CONTRACT_ID = "content-question-math-contract"
CONTRACT_VERSION = "1.0.0"
DISPATCH_VALIDATOR_ID = "content-math-dispatch-v1"


@dataclass(frozen=True)
class KnowledgeMapping:
    knowledge_point_id: str
    relation_type: str


@dataclass(frozen=True)
class MathematicalValidation:
    status: str
    validator_id: str
    computed_answer: str | None
    source_answer: str | None
    evidence: dict[str, object]


@dataclass(frozen=True)
class QuestionValidationAssessment:
    mathematical_validation: MathematicalValidation
    knowledge_mappings: tuple[KnowledgeMapping, ...]
    difficulty: str | None
    difficulty_evidence: dict[str, object] | None
    content_eligible: bool
    input_sha256: str


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\x01", "").replace("\x07", "")
    return re.sub(r"\s+", "", text)


def _hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest().upper()


def _source_answer(candidate: QuestionCandidate) -> str | None:
    if not candidate.answer_evidence:
        return None
    raw = candidate.answer_evidence[0].text
    marker = raw.find(ANSWER_MARKER)
    if marker < 0:
        return None
    value = raw[marker + len(ANSWER_MARKER):]
    return _normalize(value) or None


def _compare_answer(*, validator_id: str, computed: str, candidate: QuestionCandidate, evidence: dict[str, object]) -> MathematicalValidation:
    source_answer = _source_answer(candidate)
    if source_answer is None:
        return MathematicalValidation(
            "unsupported", validator_id, computed, None,
            {**evidence, "reason": "source_answer_evidence_missing"},
        )
    if source_answer != _normalize(computed):
        return MathematicalValidation(
            "fail", validator_id, computed, source_answer,
            {**evidence, "reason": "independent_answer_does_not_match_source_answer"},
        )
    return MathematicalValidation("pass", validator_id, computed, source_answer, evidence)


def _triangle_side_choice(candidate: QuestionCandidate) -> tuple[MathematicalValidation, tuple[KnowledgeMapping, ...], str, dict[str, object]] | None:
    stem = _normalize(candidate.stem)
    if "能组成一个三角形" not in stem:
        return None
    parsed: list[tuple[str, tuple[int, int, int]]] = []
    for option in candidate.options:
        label = _normalize(option.get("label", ""))
        text = _normalize(option.get("text", ""))
        match = re.fullmatch(r"(\d+)[,，](\d+)[,，](\d+)", text)
        if label not in {"A", "B", "C", "D"} or match is None:
            return MathematicalValidation(
                "unsupported", "triangle-side-inequality-choice-v1", None, _source_answer(candidate),
                {"reason": "options_are_not_exact_three_integer_side_lengths"},
            ), tuple(), "", {}
        parsed.append((label, tuple(int(value) for value in match.groups())))
    if len(parsed) < 2:
        return MathematicalValidation(
            "unsupported", "triangle-side-inequality-choice-v1", None, _source_answer(candidate),
            {"reason": "insufficient_structured_options"},
        ), tuple(), "", {}
    valid = [label for label, sides in parsed if sum(sorted(sides)[:2]) > max(sides)]
    evidence = {
        "rule": "strict_triangle_inequality",
        "options": [{"label": label, "sides": sides, "forms_triangle": label in valid} for label, sides in parsed],
        "valid_option_labels": valid,
    }
    if len(valid) != 1:
        return MathematicalValidation(
            "fail", "triangle-side-inequality-choice-v1", valid[0] if len(valid) == 1 else None,
            _source_answer(candidate), {**evidence, "reason": "triangle_option_count_not_unique"},
        ), tuple(), "", {}
    validation = _compare_answer(
        validator_id="triangle-side-inequality-choice-v1", computed=valid[0], candidate=candidate, evidence=evidence,
    )
    mappings = (KnowledgeMapping("kp-triangle-side-inequality-v1", "primary"),)
    difficulty_evidence = {"method": "explicit_math_feature_rules-v1", "features": {"rule_count": 1, "case_enumeration": 0, "symbolic_solving": False}}
    return validation, mappings, "基础", difficulty_evidence


def _isosceles_perimeter(candidate: QuestionCandidate) -> tuple[MathematicalValidation, tuple[KnowledgeMapping, ...], str, dict[str, object]] | None:
    stem = _normalize(candidate.stem)
    if "等腰三角形" not in stem or "一边长为" not in stem or "另一边长为" not in stem or "周长" not in stem:
        return None
    match = re.search(r"一边长为(\d+)cm.*?另一边长为(\d+)cm", stem)
    if match is None:
        return MathematicalValidation(
            "unsupported", "isosceles-perimeter-case-analysis-v1", None, _source_answer(candidate),
            {"reason": "two_integral_lengths_with_cm_not_recognized"},
        ), tuple(), "", {}
    first, second = (int(value) for value in match.groups())
    candidates: list[dict[str, object]] = []
    if 2 * first > second:
        candidates.append({"sides": [first, first, second], "perimeter": 2 * first + second})
    if 2 * second > first:
        candidates.append({"sides": [first, second, second], "perimeter": first + 2 * second})
    perimeters = sorted({int(value["perimeter"]) for value in candidates})
    evidence = {"rule": "isosceles_case_enumeration_with_strict_triangle_inequality", "input_lengths_cm": [first, second], "valid_cases": candidates}
    if len(perimeters) != 1:
        return MathematicalValidation(
            "fail", "isosceles-perimeter-case-analysis-v1", None, _source_answer(candidate),
            {**evidence, "reason": "perimeter_not_unique"},
        ), tuple(), "", {}
    computed = f"{perimeters[0]}cm"
    validation = _compare_answer(
        validator_id="isosceles-perimeter-case-analysis-v1", computed=computed, candidate=candidate, evidence=evidence,
    )
    mappings = (
        KnowledgeMapping("kp-bsd8x-isosceles-triangle-properties-v1", "primary"),
        KnowledgeMapping("kp-triangle-side-inequality-v1", "secondary"),
    )
    difficulty_evidence = {"method": "explicit_math_feature_rules-v1", "features": {"rule_count": 2, "case_enumeration": 2, "symbolic_solving": False}}
    return validation, mappings, "中等", difficulty_evidence


def _dispatch(candidate: QuestionCandidate) -> tuple[MathematicalValidation, tuple[KnowledgeMapping, ...], str | None, dict[str, object] | None]:
    for validator in (_triangle_side_choice, _isosceles_perimeter):
        result = validator(candidate)
        if result is not None:
            return result
    return (
        MathematicalValidation(
            "unsupported", DISPATCH_VALIDATOR_ID, None, _source_answer(candidate),
            {"reason": "no_exact_supported_mathematical_form"},
        ),
        tuple(),
        None,
        None,
    )


def assess_candidate(candidate: QuestionCandidate, *, approved_knowledge_point_ids: Iterable[str]) -> QuestionValidationAssessment:
    validation, mappings, difficulty, difficulty_evidence = _dispatch(candidate)
    approved = frozenset(approved_knowledge_point_ids)
    missing = [mapping.knowledge_point_id for mapping in mappings if mapping.knowledge_point_id not in approved]
    eligible = validation.status == "pass" and bool(mappings) and difficulty is not None and not missing
    if missing:
        validation = MathematicalValidation(
            validation.status,
            validation.validator_id,
            validation.computed_answer,
            validation.source_answer,
            {**validation.evidence, "missing_approved_knowledge_point_ids": missing},
        )
    input_sha256 = _hash({
        "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "source_question_no": candidate.source_question_no,
        "question_type": candidate.question_type,
        "stem": candidate.stem,
        "options": list(candidate.options),
        "answer_evidence": [
            {"block_id": item.block_id, "start": item.start, "end": item.end, "text": item.text}
            for item in candidate.answer_evidence
        ],
    })
    return QuestionValidationAssessment(validation, mappings, difficulty, difficulty_evidence, eligible, input_sha256)
