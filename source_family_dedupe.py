from __future__ import annotations

import hashlib
import re
from pathlib import PurePath
from typing import Any, Dict, Mapping, Sequence

ALGORITHM_VERSION = "source_family_dedupe/v1"
THRESHOLDS = {
    "filename_base_similarity_gte": 0.72,
    "stem_fingerprint_similarity_gte": 0.74,
}

_NOISE_TOKENS = {
    "copy",
    "draft",
    "final",
    "revised",
    "revision",
    "version",
}
_VERSION_TOKEN_RE = re.compile(r"^(?:v|ver|rev|r)\d+$")
_HASH_FIELDS = ("content_hash", "sha256", "hash")
_FILENAME_FIELDS = ("filename", "file_name", "source_filename", "path")
_STEM_FIELDS = ("stem_text", "question_text", "prompt", "title")


def classify_source_relationship(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> Dict[str, Any]:
    """Classify whether two source records are exact duplicates or likely siblings.

    The function is deterministic and fail-closed: missing data never escalates a pair
    into a merge/delete action, and suspected family matches are always manual-review
    suggestions rather than automatic mutations.
    """

    left_hash = _first_nonempty_str(left, _HASH_FIELDS)
    right_hash = _first_nonempty_str(right, _HASH_FIELDS)
    left_filename = _first_nonempty_str(left, _FILENAME_FIELDS)
    right_filename = _first_nonempty_str(right, _FILENAME_FIELDS)
    left_stem = _first_nonempty_str(left, _STEM_FIELDS)
    right_stem = _first_nonempty_str(right, _STEM_FIELDS)

    missing_inputs = []
    if not left_hash:
        missing_inputs.append("left.content_hash")
    if not right_hash:
        missing_inputs.append("right.content_hash")
    if not left_filename:
        missing_inputs.append("left.filename")
    if not right_filename:
        missing_inputs.append("right.filename")
    if not left_stem:
        missing_inputs.append("left.stem_text")
    if not right_stem:
        missing_inputs.append("right.stem_text")

    hash_match = None
    if left_hash and right_hash:
        hash_match = left_hash.lower() == right_hash.lower()

    left_filename_base = _normalize_filename_base(left_filename)
    right_filename_base = _normalize_filename_base(right_filename)
    filename_similarity = _similarity_bundle(left_filename_base, right_filename_base)

    left_stem_normalized = _normalize_text(left_stem)
    right_stem_normalized = _normalize_text(right_stem)
    left_stem_fingerprint = _fingerprint_digest(left_stem_normalized)
    right_stem_fingerprint = _fingerprint_digest(right_stem_normalized)
    stem_similarity = _similarity_bundle(left_stem_normalized, right_stem_normalized)

    evidence = {
        "hash_match": hash_match,
        "left_hash_prefix": _hash_prefix(left_hash),
        "right_hash_prefix": _hash_prefix(right_hash),
        "left_filename_base": left_filename_base or None,
        "right_filename_base": right_filename_base or None,
        "filename_similarity": filename_similarity,
        "left_stem_fingerprint": left_stem_fingerprint,
        "right_stem_fingerprint": right_stem_fingerprint,
        "stem_fingerprint_similarity": stem_similarity,
        "missing_inputs": missing_inputs,
    }

    if hash_match is True:
        return _result(
            decision="exact_duplicate",
            uncertainty="low",
            evidence=evidence,
            recommended_action="manual_review_for_dedupe",
        )

    can_evaluate_family = bool(
        left_filename_base
        and right_filename_base
        and left_stem_normalized
        and right_stem_normalized
    )
    if can_evaluate_family:
        filename_score = filename_similarity["score"]
        stem_score = stem_similarity["score"]
        if (
            filename_score >= THRESHOLDS["filename_base_similarity_gte"]
            and stem_score >= THRESHOLDS["stem_fingerprint_similarity_gte"]
        ):
            return _result(
                decision="suspected_same_family",
                uncertainty="medium",
                evidence=evidence,
                recommended_action="manual_review_only",
            )
        return _result(
            decision="no_match",
            uncertainty="low",
            evidence=evidence,
            recommended_action="none",
        )

    return _result(
        decision="insufficient_input",
        uncertainty="high",
        evidence=evidence,
        recommended_action="none",
    )



def _result(
    *,
    decision: str,
    uncertainty: str,
    evidence: Mapping[str, Any],
    recommended_action: str,
) -> Dict[str, Any]:
    return {
        "decision": decision,
        "algorithm_version": ALGORITHM_VERSION,
        "thresholds": dict(THRESHOLDS),
        "uncertainty": uncertainty,
        "recommended_action": recommended_action,
        "auto_merge": False,
        "auto_delete": False,
        "evidence": dict(evidence),
    }



def _first_nonempty_str(record: Mapping[str, Any], fields: Sequence[str]) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return ""



def _normalize_filename_base(filename: str) -> str:
    if not filename:
        return ""
    base = PurePath(filename).stem
    normalized = _normalize_text(base)
    filtered_tokens = []
    for token in normalized.split():
        if token in _NOISE_TOKENS or _VERSION_TOKEN_RE.match(token):
            continue
        filtered_tokens.append(token)
    return " ".join(filtered_tokens)



def _normalize_text(value: str) -> str:
    if not value:
        return ""
    lowered = value.lower().strip()
    cleaned = []
    pending_space = False
    for char in lowered:
        if char.isalnum() or _is_cjk(char):
            cleaned.append(char)
            pending_space = False
            continue
        if not pending_space:
            cleaned.append(" ")
            pending_space = True
    return " ".join("".join(cleaned).split())



def _is_cjk(char: str) -> bool:
    code = ord(char)
    return 0x4E00 <= code <= 0x9FFF



def _similarity_bundle(left: str, right: str) -> Dict[str, Any]:
    if not left or not right:
        return {
            "score": None,
            "token_jaccard": None,
            "trigram_jaccard": None,
        }
    left_tokens = left.split()
    right_tokens = right.split()
    token_jaccard = _jaccard(set(left_tokens), set(right_tokens))
    trigram_jaccard = _jaccard(_char_ngrams(left), _char_ngrams(right))
    return {
        "score": round(max(token_jaccard, trigram_jaccard), 4),
        "token_jaccard": round(token_jaccard, 4),
        "trigram_jaccard": round(trigram_jaccard, 4),
    }



def _char_ngrams(value: str, n: int = 3) -> set[str]:
    compact = value.replace(" ", "")
    if not compact:
        return set()
    if len(compact) < n:
        return {compact}
    return {compact[index : index + n] for index in range(len(compact) - n + 1)}



def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)



def _fingerprint_digest(normalized_text: str) -> str | None:
    if not normalized_text:
        return None
    features = sorted(_char_ngrams(normalized_text))
    joined = "|".join(features).encode("utf-8")
    return hashlib.sha1(joined).hexdigest()



def _hash_prefix(value: str) -> str | None:
    if not value:
        return None
    return value[:12].lower()
