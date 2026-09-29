"""Conservative eligibility rules for deterministic DOCX candidates.

Eligibility here means only that a candidate has the source fields needed for
manual review. It never means mathematical approval or delivery eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass

from docx_candidate_parser import CandidateQuestion


@dataclass(frozen=True)
class CandidateValidation:
    number: str
    eligible_for_review: bool
    reasons: tuple[str, ...]


def validate_candidate(candidate: CandidateQuestion) -> CandidateValidation:
    reasons: list[str] = []
    if not candidate.stem_text.strip():
        reasons.append("missing stem")
    if not candidate.answer:
        reasons.append("missing answer")
    if not candidate.knowledge_text:
        reasons.append("missing knowledge point")
    if not candidate.analysis_text:
        reasons.append("missing analysis")
    if not candidate.answer_fragment_id:
        reasons.append("missing answer provenance")
    if not candidate.knowledge_fragment_id:
        reasons.append("missing knowledge provenance")
    if not candidate.analysis_fragment_ids:
        reasons.append("missing analysis provenance")

    # The first B2 bulk batch is intentionally limited to simple objective
    # questions. Multi-part items require a separate answer/score parser.
    if int(candidate.number) > 20:
        reasons.append("outside first objective-question batch")

    return CandidateValidation(
        number=candidate.number,
        eligible_for_review=not reasons,
        reasons=tuple(reasons),
    )
