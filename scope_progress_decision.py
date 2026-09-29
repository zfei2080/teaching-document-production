"""Pure, fail-closed suggestions for curriculum scope and class progress.

This module deliberately has no database or network dependency.  It can produce
reproducible *candidate* mappings from trusted catalog labels, but it cannot
approve a mapping, a question, or a delivery.  A separately controlled import
and scope validator must turn a candidate into approved evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]{4,}")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_MIN_CONFIDENCE = 0.75
_MIN_MARGIN = 0.15


@dataclass(frozen=True)
class CurriculumNode:
    """Minimal trusted catalog data needed by the suggestion function."""

    node_id: str
    textbook_id: str
    catalog_version: str
    name: str


@dataclass(frozen=True)
class AnchorEvidence:
    """Exact text evidence for one candidate-node match."""

    anchor: str
    question_offsets: tuple[int, ...]
    node_offsets: tuple[int, ...]


@dataclass(frozen=True)
class MappingCandidate:
    node_id: str
    textbook_id: str
    catalog_version: str
    node_name: str
    confidence: float
    anchors: tuple[AnchorEvidence, ...]


@dataclass(frozen=True)
class MappingSuggestion:
    """A non-authoritative recommendation or an explicit isolation result."""

    question_id: str
    textbook_id: str
    status: str  # candidate | isolated
    candidates: tuple[MappingCandidate, ...]
    isolation_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ApprovedScopeEvidence:
    """Evidence supplied by the controlled mapping and audit path.

    ``mapping_status`` must be ``approved`` and ``provenance_hash`` must be a
    SHA-256 digest before this pure function will use the mapping for progress.
    Suggestions from this module are intentionally not sufficient.
    """

    question_id: str
    textbook_id: str
    curriculum_node_ids: tuple[str, ...]
    mapping_status: str
    provenance_hash: str


@dataclass(frozen=True)
class ActiveClassProgress:
    class_id: str
    textbook_id: str
    current_curriculum_node_id: str
    allowed_curriculum_node_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProgressScopeDecision:
    question_id: str
    class_id: str
    status: str  # within_active_progress | blocked
    reasons: tuple[str, ...]
    allowed_node_ids: tuple[str, ...]
    mapped_node_ids: tuple[str, ...]


def _occurrences(text: str, fragment: str) -> tuple[int, ...]:
    start = 0
    result: list[int] = []
    while True:
        index = text.find(fragment, start)
        if index < 0:
            return tuple(result)
        result.append(index)
        start = index + len(fragment)


def _anchors_for_node(name: str) -> tuple[str, ...]:
    """Return catalog-label anchors that are specific enough for a suggestion.

    Two- and three-character Chinese fragments are intentionally excluded. They
    are too broad to automatically distinguish nearby curriculum nodes.
    """

    anchors: set[str] = set()
    for run in _CHINESE_RUN.findall(name):
        anchors.add(run)
        for size in range(4, len(run)):
            anchors.update(run[index:index + size] for index in range(len(run) - size + 1))
    return tuple(sorted(anchors, key=lambda item: (-len(item), item)))


def _candidate_for_node(question_text: str, node: CurriculumNode) -> MappingCandidate | None:
    evidence: list[AnchorEvidence] = []
    for anchor in _anchors_for_node(node.name):
        question_offsets = _occurrences(question_text, anchor)
        if question_offsets:
            evidence.append(
                AnchorEvidence(
                    anchor=anchor,
                    question_offsets=question_offsets,
                    node_offsets=_occurrences(node.name, anchor),
                )
            )
    if not evidence:
        return None
    longest = max(len(item.anchor) for item in evidence)
    # Four exact Chinese characters are intentionally only a suggestion, never approval.
    confidence = min(1.0, longest / 5.0)
    return MappingCandidate(
        node_id=node.node_id,
        textbook_id=node.textbook_id,
        catalog_version=node.catalog_version,
        node_name=node.name,
        confidence=confidence,
        anchors=tuple(sorted(evidence, key=lambda item: (-len(item.anchor), item.anchor))),
    )


def suggest_mapping(
    *,
    question_id: str,
    question_text: str,
    textbook_id: str,
    nodes: Iterable[CurriculumNode],
) -> MappingSuggestion:
    """Return one conservative, reproducible mapping suggestion or isolate it.

    The caller must supply nodes from a trusted, single textbook catalog.  A
    missing catalog version, a cross-textbook node, weak evidence, or a tied
    strongest candidate is isolated rather than guessed.
    """

    reasons: list[str] = []
    if not question_id.strip():
        reasons.append("missing_question_id")
    if not question_text.strip():
        reasons.append("missing_question_text")
    if not textbook_id.strip():
        reasons.append("missing_textbook_id")

    node_list = tuple(nodes)
    if not node_list:
        reasons.append("no_catalog_nodes")
    if any(not node.node_id.strip() or not node.name.strip() or not node.catalog_version.strip() for node in node_list):
        reasons.append("incomplete_catalog_node")
    if any(node.textbook_id != textbook_id for node in node_list):
        reasons.append("cross_textbook_catalog_node")
    if reasons:
        return MappingSuggestion(question_id, textbook_id, "isolated", (), tuple(sorted(set(reasons))))

    candidates = tuple(
        candidate
        for node in node_list
        if (candidate := _candidate_for_node(question_text, node)) is not None
    )
    ranked = tuple(sorted(candidates, key=lambda item: (-item.confidence, item.node_id)))
    if not ranked:
        return MappingSuggestion(question_id, textbook_id, "isolated", (), ("no_exact_catalog_anchor",))
    if ranked[0].confidence < _MIN_CONFIDENCE:
        return MappingSuggestion(question_id, textbook_id, "isolated", ranked, ("low_confidence",))
    if len(ranked) > 1 and ranked[0].confidence - ranked[1].confidence < _MIN_MARGIN:
        return MappingSuggestion(question_id, textbook_id, "isolated", ranked, ("ambiguous_top_candidate",))
    return MappingSuggestion(question_id, textbook_id, "candidate", (ranked[0],), ())


def decide_progress_scope(
    evidence: ApprovedScopeEvidence,
    progress: ActiveClassProgress,
) -> ProgressScopeDecision:
    """Fail closed when approved scope evidence cannot prove class eligibility.

    Every mapped node must be within the explicitly supplied progress set.  This
    prevents compound questions from entering merely because one of their
    knowledge nodes has already been taught.
    """

    mapped = tuple(dict.fromkeys(node_id.strip() for node_id in evidence.curriculum_node_ids if node_id.strip()))
    allowed = tuple(dict.fromkeys(node_id.strip() for node_id in progress.allowed_curriculum_node_ids if node_id.strip()))
    reasons: list[str] = []
    if not evidence.question_id.strip():
        reasons.append("missing_question_id")
    if evidence.mapping_status != "approved":
        reasons.append("mapping_not_approved")
    if not _HEX_DIGEST.fullmatch(evidence.provenance_hash or ""):
        reasons.append("invalid_mapping_provenance")
    if not mapped:
        reasons.append("missing_mapped_nodes")
    if not progress.class_id.strip() or not progress.textbook_id.strip() or not progress.current_curriculum_node_id.strip():
        reasons.append("incomplete_active_progress")
    if progress.current_curriculum_node_id not in allowed:
        reasons.append("current_node_not_allowed")
    if evidence.textbook_id != progress.textbook_id:
        reasons.append("textbook_mismatch")
    outside = tuple(node_id for node_id in mapped if node_id not in set(allowed))
    if outside:
        reasons.append("mapped_node_outside_active_progress")

    return ProgressScopeDecision(
        question_id=evidence.question_id,
        class_id=progress.class_id,
        status="blocked" if reasons else "within_active_progress",
        reasons=tuple(sorted(set(reasons))),
        allowed_node_ids=allowed,
        mapped_node_ids=mapped,
    )
