"""Pure, fail-closed automatic question-scope candidate engine.

This module joins three *read-only* inputs: extracted question features,
keyword-classifier scores, and a previously resolved curriculum-progress scope.
It never opens a database and cannot approve a mapping, question, or delivery.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from curriculum_progress_scope import CurriculumProgressScope
from question_classifier import score_question_node
from question_scope_features import ScopeFeatures, extract_question_scope_features

ENGINE_ID = "question_scope_engine"
ENGINE_VERSION = "v1"
MIN_CONFIDENCE = 0.75
_MIN_EXACT_ANCHOR_LENGTH = 4
_CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]{4,}")


@dataclass(frozen=True)
class ScopeCatalogNode:
    """The small, trusted catalog projection consumed by this pure engine."""

    node_id: str
    textbook_id: str
    catalog_version: str
    name: str


@dataclass(frozen=True)
class ScopeCandidate:
    node_id: str
    textbook_id: str
    catalog_version: str
    node_name: str
    confidence: float
    evidence: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class QuestionScopeDecision:
    """A deterministic draft result; ``candidate`` is never an approval."""

    engine_id: str
    engine_version: str
    status: str  # candidate | unsupported | rejected
    candidate_nodes: tuple[ScopeCandidate, ...]
    confidence: float
    reasons: tuple[dict[str, Any], ...]
    input_hash: str
    catalog_hash: str
    decision_hash: str
    approval_eligible: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def determine_question_scope(
    *,
    question: Mapping[str, Any] | object,
    textbook_id: str,
    nodes: Iterable[ScopeCatalogNode | Mapping[str, Any]],
    allowed_node_ids: Iterable[str] | None = None,
    progress_scope: CurriculumProgressScope | None = None,
) -> QuestionScopeDecision:
    """Return only a proven, unique in-progress candidate or fail closed.

    ``progress_scope`` is preferred when available and must be resolved.  The
    direct ``allowed_node_ids`` form is provided for callers that have already
    materialised the allowed set; an empty/malformed set is never interpreted
    as unrestricted scope.
    """
    features = _features(question)
    node_list, node_errors = _coerce_nodes(nodes)
    allowed, progress_errors = _allowed_ids(allowed_node_ids, progress_scope)
    input_hash = features.input_hash
    catalog_hash = _hash({
        "textbook_id": textbook_id if isinstance(textbook_id, str) else "",
        "nodes": [asdict(node) for node in node_list],
        "allowed_node_ids": sorted(allowed),
        "progress": _progress_payload(progress_scope),
    })
    reasons: list[dict[str, Any]] = []

    if not isinstance(textbook_id, str) or not textbook_id.strip():
        reasons.append(_reason("missing_textbook_id"))
    if features.status == "unsupported":
        reasons.append(_reason("unsupported_question_input", feature_reason=features.reason))
    elif not features.candidate_features:
        reasons.append(_reason("insufficient_question_features"))
    reasons.extend(_reason(code) for code in node_errors + progress_errors)
    if node_list and any(node.textbook_id != textbook_id for node in node_list):
        reasons.append(_reason("cross_textbook_catalog_node"))

    if reasons:
        status = "unsupported" if any(item["code"] in {"unsupported_question_input", "insufficient_question_features", "invalid_or_empty_allowed_node_set", "unresolved_progress_scope"} for item in reasons) else "rejected"
        return _decision(status, (), 0.0, reasons, input_hash, catalog_hash)

    candidates: list[ScopeCandidate] = []
    out_of_progress = False
    for node in node_list:
        exact_evidence = _exact_evidence(features.normalized_text, node.name)
        if not exact_evidence:
            continue
        classifier_score = score_question_node(features.normalized_text, node.name)
        # The classifier is read-only candidate input.  Exact catalogue anchors
        # independently establish the auditable chain and can be stronger than
        # its broad n-gram fallback, but never replace that fallback with a
        # non-exact semantic guess.
        anchor_score = min(1.0, max(len(str(item["anchor"])) for item in exact_evidence) / 5.0)
        score = max(float(classifier_score), anchor_score)
        if score < MIN_CONFIDENCE:
            continue
        candidate = ScopeCandidate(
            node.node_id, node.textbook_id, node.catalog_version, node.name,
            round(score, 4), tuple(exact_evidence),
        )
        if node.node_id in allowed:
            candidates.append(candidate)
        else:
            out_of_progress = True

    candidates.sort(key=lambda value: (-value.confidence, value.node_id))
    if not candidates:
        code = "candidate_node_outside_allowed_progress" if out_of_progress else "no_complete_evidence_chain"
        return _decision("rejected", (), 0.0, [_reason(code)], input_hash, catalog_hash)
    if len(candidates) > 1 and candidates[0].confidence - candidates[1].confidence < 0.15:
        return _decision("rejected", tuple(candidates), 0.0, [_reason("ambiguous_top_candidate")], input_hash, catalog_hash)

    # A future node mentioned by the question is evidence of a compound or
    # cross-range prompt; do not silently choose only its in-range portion.
    if out_of_progress:
        return _decision("rejected", tuple(candidates), 0.0, [_reason("candidate_node_outside_allowed_progress")], input_hash, catalog_hash)
    return _decision("candidate", (candidates[0],), candidates[0].confidence, [_reason("unique_evidence_chain")], input_hash, catalog_hash)


# A descriptive alias makes the module easy to adopt without granting authority.
evaluate_question_scope = determine_question_scope


def _features(question: Mapping[str, Any] | object) -> ScopeFeatures:
    if not isinstance(question, Mapping):
        return extract_question_scope_features(stem=None)
    return extract_question_scope_features(
        stem=question.get("stem", question.get("stem_text", question.get("question_text", ""))),
        options=question.get("options", question.get("choices", "")),
        answer=question.get("answer", question.get("reference_answer", "")),
        analysis=question.get("analysis", question.get("explanation", question.get("solution", ""))),
        question_type=question.get("question_type", ""),
    )


def _coerce_nodes(values: Iterable[ScopeCatalogNode | Mapping[str, Any]]) -> tuple[tuple[ScopeCatalogNode, ...], tuple[str, ...]]:
    result: list[ScopeCatalogNode] = []
    errors: set[str] = set()
    try:
        source = tuple(values)
    except TypeError:
        return (), ("invalid_catalog_nodes",)
    for value in source:
        try:
            node = value if isinstance(value, ScopeCatalogNode) else ScopeCatalogNode(
                str(value["node_id"]).strip(), str(value["textbook_id"]).strip(),
                str(value["catalog_version"]).strip(), str(value["name"]).strip(),
            )
        except (KeyError, TypeError, AttributeError):
            errors.add("incomplete_catalog_node")
            continue
        if not all((node.node_id, node.textbook_id, node.catalog_version, node.name)):
            errors.add("incomplete_catalog_node")
        else:
            result.append(node)
    if not result:
        errors.add("no_catalog_nodes")
    if len({node.node_id for node in result}) != len(result):
        errors.add("duplicate_catalog_node_id")
    return tuple(sorted(result, key=lambda node: node.node_id)), tuple(sorted(errors))


def _allowed_ids(direct: Iterable[str] | None, progress: CurriculumProgressScope | None) -> tuple[set[str], tuple[str, ...]]:
    if progress is not None:
        if progress.status != "resolved":
            return set(), ("unresolved_progress_scope",)
        values = progress.allowed_node_ids
    else:
        values = direct
    if values is None:
        return set(), ("invalid_or_empty_allowed_node_set",)
    try:
        allowed = {value.strip() for value in values if isinstance(value, str) and value.strip()}
    except TypeError:
        return set(), ("invalid_or_empty_allowed_node_set",)
    return allowed, () if allowed else ("invalid_or_empty_allowed_node_set",)


def _exact_evidence(question_text: str, node_name: str) -> list[dict[str, Any]]:
    """Find exact, four-plus-character catalog anchors in the question.

    A full node label need not appear verbatim (for example, sibling nodes may
    share ``全等三角形``); those shared anchors are retained precisely so the
    caller can reject the resulting tie rather than fabricate uniqueness.
    """
    anchors: set[str] = set()
    for run in _CHINESE_RUN.findall(node_name):
        for size in range(_MIN_EXACT_ANCHOR_LENGTH, len(run) + 1):
            anchors.update(run[index:index + size] for index in range(len(run) - size + 1))
    evidence: list[dict[str, Any]] = []
    for anchor in sorted(anchors, key=lambda item: (-len(item), item)):
        start = question_text.find(anchor)
        if start >= 0:
            evidence.append({"kind": "exact_catalog_anchor", "anchor": anchor, "question_start": start, "question_end": start + len(anchor), "node_start": node_name.find(anchor)})
    return evidence


def _reason(code: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "details": details}


def _progress_payload(progress: CurriculumProgressScope | None) -> dict[str, Any] | None:
    return None if progress is None else {"status": progress.status, "textbook_id": progress.textbook_id, "catalog_version": progress.catalog_version, "allowed_node_ids": list(progress.allowed_node_ids)}


def _decision(status: str, candidates: tuple[ScopeCandidate, ...], confidence: float, reasons: list[dict[str, Any]], input_hash: str, catalog_hash: str) -> QuestionScopeDecision:
    canonical_reasons = tuple(sorted(reasons, key=lambda item: (item["code"], _hash(item))))
    payload = {"engine_id": ENGINE_ID, "engine_version": ENGINE_VERSION, "status": status, "candidate_nodes": [asdict(item) for item in candidates], "confidence": confidence, "reasons": list(canonical_reasons), "input_hash": input_hash, "catalog_hash": catalog_hash}
    return QuestionScopeDecision(ENGINE_ID, ENGINE_VERSION, status, candidates, confidence, canonical_reasons, input_hash, catalog_hash, _hash(payload))


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
