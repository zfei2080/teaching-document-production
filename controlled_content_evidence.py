"""P1-2b temporary evidence chain for trusted legacy teaching-material originals.

This module is intentionally outside the production database.  It copies a trusted
legacy ``.doc`` into a deterministic temporary archive, converts it through an
explicit converter, compares independent read-only Word profiles, and produces
content segments tied to the first-pilot curriculum contract.  It never imports
questions, maps or approves questions, or creates delivery artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from shutil import copy2
from typing import Any, Callable, Mapping, Sequence
import json
import re

from controlled_doc_conversion import LegacyDocConversionBlockedError, convert_legacy_doc
from word_com_document_profile import profile_document


SCHEMA = "p1-2b-controlled-content-evidence-v1"
PILOT_TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
PILOT_CURRENT_NODE_ID = "bsd-math-8x-2026-node-01-section-02"
PILOT_PRIOR_NODE_ID = "bsd-math-8x-2026-node-01-section-01"
PILOT_THEME = "等腰三角形"
# Explicit downstream curriculum topics. Tool words such as compass or
# current-topic angle-bisector properties are not blanket blockers.
BLOCKED_FUTURE_DEPENDENCIES = (
    "\u76f4\u89d2\u4e09\u89d2\u5f62",
    "\u7ebf\u6bb5\u7684\u5782\u76f4\u5e73\u5206\u7ebf",
    "\u5e73\u884c\u56db\u8fb9\u5f62\u7684\u6027\u8d28",
    "\u5706\u7684\u6027\u8d28",
)


# P1-2b-only source locator: the first basic-practice item is target-aligned;
# all following mixed material begins at this deterministic boundary.
PILOT_CONTENT_BOUNDARIES = {
    "consolidation_practice": ("\u7528\u53cd\u8bc1\u6cd5",),
}

class ControlledContentBlockedError(RuntimeError):
    """Raised where P1-2b cannot establish safe temporary evidence."""


@dataclass(frozen=True)
class PilotContentContract:
    textbook_id: str = PILOT_TEXTBOOK_ID
    current_node_id: str = PILOT_CURRENT_NODE_ID
    allowed_node_ids: tuple[str, ...] = (PILOT_PRIOR_NODE_ID, PILOT_CURRENT_NODE_ID)
    theme: str = PILOT_THEME


@dataclass(frozen=True)
class ControlledContentEvidence:
    status: str
    manifest: dict[str, Any]

    @property
    def eligible_for_delivery(self) -> bool:
        """P1-2b evidence never authorizes a student document by itself."""
        return False


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _archive_source(source: Path, *, allowed_root: Path, archive_root: Path) -> tuple[Path, str]:
    if not _is_within(source, allowed_root):
        raise ControlledContentBlockedError("source_outside_allowed_root")
    if _is_within(archive_root, allowed_root):
        raise ControlledContentBlockedError("archive_root_inside_source_root")
    source_hash = _sha256(source)
    destination = archive_root / source_hash / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _sha256(destination) != source_hash:
            raise ControlledContentBlockedError("archive_destination_conflict")
    else:
        copy2(source, destination)
    if _sha256(source) != source_hash:
        raise ControlledContentBlockedError("source_changed_during_archival")
    if _sha256(destination) != source_hash:
        raise ControlledContentBlockedError("archive_hash_mismatch")
    return destination.resolve(), source_hash


def _profile_differences(source: Mapping[str, Any], converted: Mapping[str, Any]) -> list[str]:
    required = (
        "normalized_content_sha256",
        "normalized_content_length",
        "paragraph_count",
        "table_signatures",
        "inline_shape_signatures",
        "shape_signatures",
        "omath_count",
    )
    return [key for key in required if source.get(key) != converted.get(key)]


def _segment_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest().upper()


def _content_segments(normalized_text: str, *, content_type: str, contract: PilotContentContract) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not normalized_text or contract.theme not in normalized_text:
        return [], [{"status": "quarantined", "reason": "theme_not_proven_in_source_text"}]

    occurrences = [(term, normalized_text.find(term)) for term in BLOCKED_FUTURE_DEPENDENCIES]
    future_terms = [(term, index) for term, index in occurrences if index >= 0]
    content_markers = [
        (marker, normalized_text.find(marker))
        for marker in PILOT_CONTENT_BOUNDARIES.get(content_type, ())
    ]
    content_boundaries = [(marker, index) for marker, index in content_markers if index >= 0]
    first_future_index = min((index for _, index in future_terms), default=len(normalized_text))
    first_content_boundary = min((index for _, index in content_boundaries), default=len(normalized_text))
    safe_end_index = min(first_future_index, first_content_boundary)
    safe_text = normalized_text[:safe_end_index]
    segments: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    if safe_text and contract.theme in safe_text:
        segments.append(
            {
                "segment_id": f"{content_type}-before-future-dependency",
                "content_type": content_type,
                "source_character_range": [0, safe_end_index],
                "normalized_text_sha256": _segment_hash(safe_text),
                "text_preview": safe_text[:120],
                "textbook_id": contract.textbook_id,
                "curriculum_node_id": contract.current_node_id,
                "required_node_ids": list(contract.allowed_node_ids),
                "scope_status": "candidate_only",
                "delivery_eligible": False,
                "reason": "theme_and_current_progress_contract_bound; separate_content_import_and_validation_required",
            }
        )
    elif safe_text:
        quarantined.append(
            {
                "status": "quarantined",
                "reason": "theme_not_proven_in_safe_segment",
                "source_character_range": [0, safe_end_index],
            }
        )
    for term, index in future_terms:
        quarantined.append(
            {
                "status": "quarantined",
                "reason": "future_dependency_detected",
                "dependency": term,
                "source_character_range": [index, len(normalized_text)],
            }
        )
    if not future_terms:
        quarantined.append(
            {
                "status": "quarantined",
                "reason": "no_future_dependency_boundary_found_manual_semantic_review_required",
            }
        )
    return segments, quarantined


def build_controlled_content_evidence(
    source_path: str | Path,
    *,
    content_type: str,
    allowed_root: str | Path,
    archive_root: str | Path,
    conversion_root: str | Path,
    converter_command: Sequence[str],
    contract: PilotContentContract = PilotContentContract(),
    profiler: Callable[[str | Path], dict[str, Any]] = profile_document,
) -> ControlledContentEvidence:
    """Build one fail-closed temporary P1-2b source/conversion/fidelity record."""
    if content_type not in {"knowledge_explanation", "consolidation_practice"}:
        raise ControlledContentBlockedError("unsupported_content_type")
    source_root = Path(allowed_root).resolve(strict=True)
    source = Path(source_path).resolve(strict=True)
    archive = Path(archive_root).resolve()
    conversion = Path(conversion_root).resolve()
    if source.suffix.lower() != ".doc":
        raise ControlledContentBlockedError("source_not_legacy_doc")
    if _is_within(conversion, source_root):
        raise ControlledContentBlockedError("conversion_root_inside_source_root")

    archived, source_hash = _archive_source(source, allowed_root=source_root, archive_root=archive)
    source_profile = profiler(source)
    archived_profile = profiler(archived)
    archive_profile_differences = _profile_differences(source_profile, archived_profile)
    if archive_profile_differences:
        raise ControlledContentBlockedError("archive_fidelity_mismatch:" + ",".join(archive_profile_differences))

    conversion_result = convert_legacy_doc(
        archived,
        allowed_root=archive,
        conversion_root=conversion,
        converter_command=converter_command,
    )
    converted = Path(conversion_result.converted_path)
    converted_profile = profiler(converted)
    if _sha256(source) != source_hash:
        raise ControlledContentBlockedError("source_changed_during_fidelity_profile")
    if _sha256(archived) != source_hash:
        raise ControlledContentBlockedError("archive_changed_during_fidelity_profile")
    conversion_profile_differences = _profile_differences(source_profile, converted_profile)
    fidelity_status = "passed" if not conversion_profile_differences else "quarantined"
    normalized_text = str(source_profile.get("normalized_content_text", ""))
    segments, quarantined_segments = _content_segments(
        normalized_text, content_type=content_type, contract=contract
    )
    if conversion_profile_differences:
        quarantined_segments.insert(
            0,
            {
                "status": "quarantined",
                "reason": "conversion_fidelity_mismatch",
                "differences": conversion_profile_differences,
            },
        )

    manifest = {
        "schema": SCHEMA,
        "purpose": "temporary_p1_2b_validation_only",
        "delivery_eligible": False,
        "approval_eligible": False,
        "question_database_write": False,
        "contract": {
            "textbook_id": contract.textbook_id,
            "current_curriculum_node_id": contract.current_node_id,
            "allowed_curriculum_node_ids": list(contract.allowed_node_ids),
            "theme": contract.theme,
        },
        "content": {"type": content_type},
        "source": {
            "path": str(source),
            "sha256": source_hash,
            "file_type": "doc",
            "unchanged_through_fidelity_profile": True,
        },
        "archive": {
            "path": str(archived),
            "sha256": _sha256(archived),
            "file_type": "doc",
            "unchanged_through_fidelity_profile": True,
        },
        "conversion": conversion_result.manifest,
        "profiles": {
            "source": source_profile,
            "archive": archived_profile,
            "converted": converted_profile,
            "archive_differences": archive_profile_differences,
            "conversion_differences": conversion_profile_differences,
            "fidelity_status": fidelity_status,
        },
        "segments": segments,
        "quarantined_segments": quarantined_segments,
        "final_status": "candidate_content_only" if fidelity_status == "passed" else "quarantined",
        "next_required_gate": "controlled_content_import_theme_dependency_and_student_artifact_validation",
    }
    return ControlledContentEvidence(status=manifest["final_status"], manifest=manifest)


def write_evidence_manifest(evidence: ControlledContentEvidence, path: str | Path) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(_canonical_json(evidence.manifest) + "\n", encoding="utf-8")
    return destination
