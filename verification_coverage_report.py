from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence

SUPPORTED_FILE_TYPES = {"pdf", "doc", "docx", "image"}
VERDICT_BUCKETS = ("pass", "fail", "unsupported")
COVERAGE_BUCKETS = ("full", "partial", "none", "unknown", "invalid")


@dataclass(frozen=True)
class NormalizedRecord:
    source_document_id: str
    file_type: str
    validator: str
    verdict: str
    coverage: str
    unsupported_reason: str | None


def build_verification_coverage_report(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [_normalize_record(record) for record in records]
    by_source: dict[str, Any] = {}
    by_file_type: dict[str, Any] = {}
    by_validator: dict[str, Any] = {}

    for key_name, target in (
        ("source_document_id", by_source),
        ("file_type", by_file_type),
        ("validator", by_validator),
    ):
        grouped = _group_by(normalized, key_name)
        for key, group in grouped.items():
            target[key] = _summarize_group(group)

    return {
        "totals": _summarize_group(normalized),
        "by_source_document_id": by_source,
        "by_file_type": by_file_type,
        "by_validator": by_validator,
    }


def _group_by(records: Sequence[NormalizedRecord], key_name: str) -> dict[str, list[NormalizedRecord]]:
    grouped: dict[str, list[NormalizedRecord]] = defaultdict(list)
    for record in records:
        grouped[getattr(record, key_name)].append(record)
    return dict(grouped)


def _summarize_group(records: Sequence[NormalizedRecord]) -> dict[str, Any]:
    coverage_counts = Counter({bucket: 0 for bucket in COVERAGE_BUCKETS})
    verdict_counts = Counter({bucket: 0 for bucket in VERDICT_BUCKETS})
    unsupported_reasons = Counter()

    for record in records:
        coverage_counts[record.coverage] += 1
        verdict_counts[record.verdict] += 1
        if record.verdict == "unsupported" and record.unsupported_reason:
            unsupported_reasons[record.unsupported_reason] += 1

    return {
        "total": len(records),
        "coverage": dict(coverage_counts),
        "verdict": dict(verdict_counts),
        "unsupported_reason": dict(unsupported_reasons),
    }


def _normalize_record(record: Mapping[str, Any]) -> NormalizedRecord:
    source_document_id = _string_or_unknown(record.get("source_document_id"), default="unknown_source")
    file_type = _normalize_file_type(record.get("file_type"))
    validator = _string_or_unknown(record.get("validator"), default="unknown_validator")
    evidence = record.get("evidence")
    unsupported_reason = record.get("unsupported_reason")

    coverage, invalid_reason = _derive_coverage(evidence)
    verdict, derived_unsupported_reason = _derive_verdict(file_type, evidence, unsupported_reason)

    if invalid_reason:
        coverage = "invalid"
        verdict = "fail"
        unsupported_reason = invalid_reason
    elif verdict == "unsupported":
        unsupported_reason = derived_unsupported_reason or _string_or_unknown(unsupported_reason, default="unsupported_file_type")
    else:
        unsupported_reason = None

    return NormalizedRecord(
        source_document_id=source_document_id,
        file_type=file_type,
        validator=validator,
        verdict=verdict,
        coverage=coverage,
        unsupported_reason=unsupported_reason,
    )


def _derive_coverage(evidence: Any) -> tuple[str, str | None]:
    if evidence is None:
        return "unknown", None
    if not isinstance(evidence, Mapping):
        return "invalid", "invalid_evidence"

    coverage = evidence.get("coverage")
    expected_hash = evidence.get("expected_hash")
    observed_hash = evidence.get("observed_hash")

    if expected_hash is not None or observed_hash is not None:
        if not expected_hash or not observed_hash or expected_hash != observed_hash:
            return "invalid", "hash_mismatch"

    if coverage is None:
        return "unknown", None
    if coverage in {"full", "partial", "none"}:
        return coverage, None
    return "invalid", "invalid_coverage"


def _derive_verdict(file_type: str, evidence: Any, unsupported_reason: Any) -> tuple[str, str | None]:
    if file_type not in SUPPORTED_FILE_TYPES:
        reason = _string_or_unknown(unsupported_reason, default=f"unsupported_file_type:{file_type}")
        return "unsupported", reason

    if evidence is None:
        return "fail", None
    if not isinstance(evidence, Mapping):
        return "fail", None

    status = evidence.get("status")
    if status == "pass":
        return "pass", None
    if status == "fail":
        return "fail", None
    if status == "unsupported":
        reason = _string_or_unknown(unsupported_reason or evidence.get("unsupported_reason"), default="unsupported")
        return "unsupported", reason
    return "fail", None


def _normalize_file_type(value: Any) -> str:
    normalized = _string_or_unknown(value, default="unknown")
    return normalized.lower()


def _string_or_unknown(value: Any, default: str) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return default
