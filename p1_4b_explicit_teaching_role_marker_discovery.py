"""Read-only discovery of literal teaching-role marker candidates in P1-2b evidence.

This is deliberately not a role-assignment mechanism.  It validates that each
converted local source still matches the P1-2b audit, then records literal
marker hits as candidate evidence with paragraph-level anchors.  A source
folder such as "basic" or "advanced" never supplies a teaching role.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable
import json

from docx_forensics import extract_paragraphs


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_AUDIT = ROOT / "output" / "audits" / "p1-2b_controlled_content_validation.json"
SCHEMA = "p1-4b-explicit-teaching-role-marker-discovery-v1"
EXPECTED_SOURCE_KEYS = frozenset(
    {
        "basic-knowledge-explanation",
        "basic-consolidation-practice",
        "advanced-knowledge-explanation",
        "advanced-consolidation-practice",
    }
)
ROLE_MARKERS = {
    "G1_pre_lesson_diagnosis": ("课前自测", "前置诊断", "诊断", "自我检测", "预习检测"),
    "G3_controlled_example_or_method": ("例题", "例", "方法", "解法", "注意"),
    "G7_summary_or_self_assessment": ("小结", "自评", "自我评价", "总结", "反思"),
}


class ExplicitMarkerDiscoveryError(RuntimeError):
    """Raised when the controlled source audit cannot prove a current input."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExplicitMarkerDiscoveryError(f"{label}_invalid")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExplicitMarkerDiscoveryError(f"{label}_invalid")
    return value


def _validated_records(audit_path: Path) -> list[dict[str, Any]]:
    if not audit_path.is_file():
        raise ExplicitMarkerDiscoveryError("source_audit_missing")
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExplicitMarkerDiscoveryError("source_audit_unreadable") from exc
    if audit.get("schema") != "p1-2b-controlled-content-run-v1":
        raise ExplicitMarkerDiscoveryError("source_audit_schema_invalid")
    records = audit.get("records")
    if not isinstance(records, list):
        raise ExplicitMarkerDiscoveryError("source_audit_records_invalid")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for record in records:
        item = _mapping(record, "source_record")
        source_key = _string(item.get("source_key"), "source_key")
        if source_key in seen:
            raise ExplicitMarkerDiscoveryError("source_key_not_unique")
        seen.add(source_key)
        manifest = _mapping(item.get("manifest"), "source_manifest")
        source = _mapping(manifest.get("source"), "source_manifest_source")
        conversion = _mapping(manifest.get("conversion"), "source_manifest_conversion")
        converted = _mapping(conversion.get("converted"), "source_manifest_converted")
        source_path = Path(_string(source.get("path"), "source_path"))
        converted_path = Path(_string(converted.get("path"), "converted_path"))
        expected_source_hash = _string(source.get("sha256"), "source_sha256")
        expected_converted_hash = _string(converted.get("sha256"), "converted_sha256")
        if not source_path.is_file() or _sha256(source_path).casefold() != expected_source_hash.casefold():
            raise ExplicitMarkerDiscoveryError("source_original_not_current")
        if not converted_path.is_file() or _sha256(converted_path).casefold() != expected_converted_hash.casefold():
            raise ExplicitMarkerDiscoveryError("source_conversion_not_current")
        if item.get("status") != "candidate_content_only" or manifest.get("delivery_eligible") is not False:
            raise ExplicitMarkerDiscoveryError("source_record_not_candidate_only")
        validated.append(
            {
                "source_key": source_key,
                "source_relative_path": _string(item.get("source_relative_path"), "source_relative_path"),
                "content_type": _string(item.get("content_type"), "content_type"),
                "source_path": str(source_path),
                "source_sha256": expected_source_hash,
                "converted_path": str(converted_path),
                "converted_sha256": expected_converted_hash,
                "converted_profile_sha256": _string(
                    _mapping(manifest.get("profiles"), "source_manifest_profiles")
                    .get("converted", {})
                    .get("normalized_content_sha256"),
                    "converted_profile_sha256",
                ),
            }
        )
    if frozenset(seen) != EXPECTED_SOURCE_KEYS:
        raise ExplicitMarkerDiscoveryError("source_audit_source_set_mismatch")
    return sorted(validated, key=lambda item: str(item["source_key"]))


def _excerpt(text: str, marker: str, limit: int = 180) -> str:
    index = text.find(marker)
    if index < 0:
        return text[:limit]
    start = max(0, index - 45)
    end = min(len(text), max(index + len(marker) + 90, start + min(limit, len(text))))
    return text[start:end]


def build_explicit_teaching_role_marker_discovery(
    source_audit_path: str | Path = DEFAULT_SOURCE_AUDIT,
    *,
    paragraph_extractor: Callable[[str | Path], Iterable[Any]] = extract_paragraphs,
) -> dict[str, object]:
    """Return source-bound literal candidates without assigning any teaching role."""
    audit_path = Path(source_audit_path).resolve()
    records = _validated_records(audit_path)
    candidates: list[dict[str, object]] = []
    source_summaries: list[dict[str, object]] = []
    for record in records:
        paragraphs = tuple(paragraph_extractor(record["converted_path"]))
        source_summaries.append({**record, "paragraph_count": len(paragraphs)})
        for paragraph in paragraphs:
            text = _string(getattr(paragraph, "text", None), "paragraph_text")
            paragraph_index = getattr(paragraph, "index", None)
            paragraph_sha256 = _string(getattr(paragraph, "sha256", None), "paragraph_sha256")
            if not isinstance(paragraph_index, int) or paragraph_index < 0:
                raise ExplicitMarkerDiscoveryError("paragraph_index_invalid")
            for role, markers in ROLE_MARKERS.items():
                for marker in markers:
                    if marker in text:
                        candidates.append(
                            {
                                "role_candidate": role,
                                "marker": marker,
                                "evidence_status": "literal_marker_candidate_only",
                                "source_key": record["source_key"],
                                "source_relative_path": record["source_relative_path"],
                                "content_type": record["content_type"],
                                "source_sha256": record["source_sha256"],
                                "converted_sha256": record["converted_sha256"],
                                "converted_profile_sha256": record["converted_profile_sha256"],
                                "paragraph_index": paragraph_index,
                                "paragraph_sha256": paragraph_sha256,
                                "excerpt": _excerpt(text, marker),
                            }
                        )
    counts = {role: sum(1 for item in candidates if item["role_candidate"] == role) for role in ROLE_MARKERS}
    return {
        "schema": SCHEMA,
        "purpose": "read_only_literal_marker_discovery_not_role_assignment",
        "source_audit": {"path": str(audit_path), "sha256": _sha256(audit_path)},
        "expected_source_keys": sorted(EXPECTED_SOURCE_KEYS),
        "sources": source_summaries,
        "marker_definitions": {role: list(markers) for role, markers in ROLE_MARKERS.items()},
        "candidate_counts": counts,
        "candidates": candidates,
        "role_assignment_performed": False,
        "student_document_generation_authorized": False,
        "decision": "candidate_evidence_only_manual_contract_and_admission_required",
    }


def write_explicit_teaching_role_marker_discovery(report: dict[str, object], path: str | Path) -> Path:
    output = Path(path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return output
