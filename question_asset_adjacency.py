"""Build a fail-closed question-to-media adjacency evidence manifest.

Only image instances hosted by a direct ``word/document.xml`` body paragraph
whose paragraph index is explicitly present in exactly one parsed question stem
are assigned.  Tables, text boxes, headers/footers, notes/comments and body
paragraphs outside question stems remain unassigned; visual proximity is never
used as evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from docx_candidate_parser import CandidateQuestion, parse_candidates
from docx_media_forensics import MediaInstance, media_instances

MANIFEST_SCHEMA = "question-asset-adjacency-manifest-v1"
GENERATOR_ID = "exact-source-paragraph-adjacency-v1"
GENERATOR_VERSION = "1.1.0"


@dataclass(frozen=True)
class AssetAdjacencyEvidence:
    instance_id: str
    assignment_status: str
    source_question_no: str | None
    assignment_reason: str
    package_part: str
    relationship_id: str
    media_path: str | None
    filename: str | None
    media_sha256: str | None
    host_kind: str
    host_location: str
    paragraph_index: int | None
    relationship_status: str
    relationship_target: str | None
    drawing_layout: tuple[tuple[str, str], ...]


def _instance_id(instance: MediaInstance) -> str:
    payload = "\0".join(
        (
            instance.package_part,
            instance.relationship_id,
            instance.media_path or "",
            instance.host_location,
            "" if instance.paragraph_index is None else str(instance.paragraph_index),
            instance.media_sha256 or "",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assign_instances(
    candidates: Iterable[CandidateQuestion], instances: Iterable[MediaInstance]
) -> list[AssetAdjacencyEvidence]:
    """Assign only exact direct-body-paragraph instances to question stems."""
    paragraph_owners: dict[int, list[str]] = {}
    for candidate in candidates:
        for paragraph_index in candidate.stem_fragment_ids:
            paragraph_owners.setdefault(paragraph_index, []).append(candidate.number)

    result: list[AssetAdjacencyEvidence] = []
    for instance in instances:
        owners = paragraph_owners.get(instance.paragraph_index, []) if instance.paragraph_index is not None else []
        if instance.relationship_status != "resolved_internal":
            status, question_no, reason = "unassigned", None, f"anomalous_relationship:{instance.relationship_status}"
        elif instance.package_part != "word/document.xml" or instance.host_kind != "body_paragraph":
            status, question_no, reason = "unassigned", None, f"unsupported_host:{instance.host_kind}"
        elif instance.paragraph_index is None:
            status, question_no, reason = "unassigned", None, "body_paragraph_index_missing"
        elif len(owners) == 1:
            status, question_no, reason = "assigned", owners[0], "exact_stem_paragraph_membership"
        elif not owners:
            status, question_no, reason = "unassigned", None, "paragraph_outside_question_stems"
        else:
            status, question_no, reason = "ambiguous", None, "paragraph_claimed_by_multiple_questions"
        result.append(
            AssetAdjacencyEvidence(
                instance_id=_instance_id(instance),
                assignment_status=status,
                source_question_no=question_no,
                assignment_reason=reason,
                **asdict(instance),
            )
        )
    return result


def build_manifest(source_path: str | Path) -> dict:
    source_path = Path(source_path)
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    candidates = parse_candidates(source_path)
    evidence = assign_instances(candidates, media_instances(source_path))
    rows = [asdict(item) for item in evidence]
    payload = {
        "schema": MANIFEST_SCHEMA,
        "generator": {"id": GENERATOR_ID, "version": GENERATOR_VERSION},
        "source": {"file": source_path.name, "sha256": source_sha256},
        "policy": {
            "assignment_rule": "exact_direct_body_stem_paragraph_membership_only",
            "unassigned_blocks_semantic_inference": True,
        },
        "summary": {
            "candidate_count": len(candidates),
            "media_instance_count": len(rows),
            "assigned_instance_count": sum(row["assignment_status"] == "assigned" for row in rows),
            "unassigned_instance_count": sum(row["assignment_status"] == "unassigned" for row in rows),
            "ambiguous_instance_count": sum(row["assignment_status"] == "ambiguous" for row in rows),
        },
        "instances": rows,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def write_manifest(source_path: str | Path, output_path: str | Path) -> dict:
    payload = build_manifest(source_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = write_manifest(args.source, args.output)
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
