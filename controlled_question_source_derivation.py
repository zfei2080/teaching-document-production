"""Append-only importer for P1-4b verifier-only answer evidence.

It consumes a P1-4b discovery report only after re-reading the converted DOCX
and the current controlled-source contract.  The importer creates no question,
mapping, approval, selection plan, document, or delivery history.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import sqlite3

from docx_forensics import extract_paragraphs
from fidelity_audit import normalize_text


ROOT = Path(__file__).resolve().parent
DEFAULT_P1_2B_AUDIT = ROOT / "output" / "audits" / "p1-2b_controlled_content_validation.json"
IMPORTER_ID = "p1-4b-controlled-question-source-derivation-import"
IMPORTER_VERSION = "1.0.0"
SCHEMA = "p1-4b-controlled-question-source-derivation-import-v1"
REQUIRED_FIELDS = ("stem", "options", "answer", "analysis")


class ControlledQuestionSourceDerivationError(RuntimeError):
    """The discovery report or current controlled source is not importable."""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest().upper()


def _digest_text(value: str) -> str:
    return _digest_bytes(value.encode("utf-8"))


def _digest_file(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _required_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControlledQuestionSourceDerivationError(f"{label}_missing")
    return value


def _required_string(value: dict[str, Any], key: str, label: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ControlledQuestionSourceDerivationError(f"{label}_{key}_missing")
    return item


def _range(value: object, label: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, int) for item in value):
        raise ControlledQuestionSourceDerivationError(f"{label}_invalid")
    start, end = value
    if start < 0 or end <= start:
        raise ControlledQuestionSourceDerivationError(f"{label}_invalid")
    return start, end


def _current_source(connection: sqlite3.Connection, discovery: dict[str, Any]) -> dict[str, Any]:
    source = _required_mapping(discovery.get("controlled_source"), "controlled_source")
    source_id = _required_string(source, "source_id", "controlled_source")
    segment_row_id = _required_string(source, "segment_row_id", "controlled_source")
    row = connection.execute(
        """SELECT s.id AS segment_row_id, s.textbook_id, s.curriculum_node_id,
                  s.source_character_range_json, s.normalized_text_sha256,
                  src.id AS source_id, src.converted_path, src.converted_sha256
             FROM current_controlled_content_segments s
             JOIN controlled_content_sources src ON src.id=s.source_id
            WHERE s.id=? AND src.id=?""",
        (segment_row_id, source_id),
    ).fetchone()
    if row is None:
        raise ControlledQuestionSourceDerivationError("discovery_controlled_source_not_current")
    current = dict(row)
    expected_range = _range(source.get("source_character_range"), "discovery_source_character_range")
    actual_range = _range(json.loads(str(current["source_character_range_json"])), "current_source_character_range")
    if actual_range != expected_range:
        raise ControlledQuestionSourceDerivationError("discovery_student_segment_range_drifted")
    if str(source.get("normalized_text_sha256", "")).casefold() != str(current["normalized_text_sha256"]).casefold():
        raise ControlledQuestionSourceDerivationError("discovery_student_segment_text_hash_drifted")
    converted = Path(str(current["converted_path"])).resolve()
    try:
        converted.relative_to(ROOT)
    except ValueError as exc:
        raise ControlledQuestionSourceDerivationError("controlled_converted_source_outside_workspace") from exc
    if not converted.is_file() or _digest_file(converted).casefold() != str(current["converted_sha256"]).casefold():
        raise ControlledQuestionSourceDerivationError("controlled_converted_source_not_current")
    return {**current, "source_character_range": actual_range, "converted_path": converted}


def _validate_anchors(discovery: dict[str, Any], source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    candidate = _required_mapping(discovery.get("candidate"), "candidate")
    anchors = _required_mapping(candidate.get("field_anchors"), "candidate_field_anchors")
    paragraphs = {paragraph.index: paragraph for paragraph in extract_paragraphs(source["converted_path"])}
    verified: dict[str, dict[str, Any]] = {}
    for field_name in REQUIRED_FIELDS:
        anchor = _required_mapping(anchors.get(field_name), f"candidate_{field_name}_anchor")
        index = anchor.get("paragraph_index")
        expected_hash = _required_string(anchor, "paragraph_sha256", f"candidate_{field_name}_anchor")
        expected_text = _required_string(anchor, "expected_text", f"candidate_{field_name}_anchor")
        if not isinstance(index, int) or index not in paragraphs:
            raise ControlledQuestionSourceDerivationError(f"candidate_{field_name}_paragraph_missing")
        paragraph = paragraphs[index]
        if paragraph.sha256.casefold() != expected_hash.casefold():
            raise ControlledQuestionSourceDerivationError(f"candidate_{field_name}_paragraph_hash_drifted")
        if normalize_text(expected_text) not in normalize_text(paragraph.text):
            raise ControlledQuestionSourceDerivationError(f"candidate_{field_name}_paragraph_text_drifted")
        verified[field_name] = {
            "paragraph_index": paragraph.index,
            "paragraph_sha256": paragraph.sha256,
            "expected_text": expected_text,
            "raw_text": paragraph.text,
        }
    return verified


def _current_converted_profile(source: dict[str, Any]) -> str:
    """Re-read the P1-2b profile that defines its character offsets."""
    if not DEFAULT_P1_2B_AUDIT.is_file():
        raise ControlledQuestionSourceDerivationError("p1_2b_audit_missing")
    try:
        audit = json.loads(DEFAULT_P1_2B_AUDIT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlledQuestionSourceDerivationError("p1_2b_audit_unreadable") from exc
    records = audit.get("records")
    if not isinstance(records, list):
        raise ControlledQuestionSourceDerivationError("p1_2b_audit_records_invalid")
    matches: list[str] = []
    for record in records:
        if not isinstance(record, dict) or record.get("content_type") != "consolidation_practice":
            continue
        manifest = record.get("manifest")
        if not isinstance(manifest, dict):
            continue
        converted = manifest.get("conversion", {}).get("converted", {})
        if not isinstance(converted, dict) or str(converted.get("sha256", "")).casefold() != str(source["converted_sha256"]).casefold():
            continue
        profile = manifest.get("profiles", {}).get("converted", {})
        text = profile.get("normalized_content_text") if isinstance(profile, dict) else None
        if isinstance(text, str) and text:
            matches.append(text)
    if len(matches) != 1:
        raise ControlledQuestionSourceDerivationError("p1_2b_profile_not_bound_to_current_converted_source")
    return matches[0]


def _candidate_fields(candidate: dict[str, Any]) -> tuple[str, list[str], str, str]:
    stem = _required_string(candidate, "stem", "candidate")
    options = candidate.get("options")
    if not isinstance(options, list) or not options or not all(isinstance(option, str) and option for option in options):
        raise ControlledQuestionSourceDerivationError("candidate_options_invalid")
    return stem, options, _required_string(candidate, "answer", "candidate"), _required_string(candidate, "analysis", "candidate")


def _profile_slice(profile: str, range_value: tuple[int, int], label: str) -> str:
    start, end = range_value
    if end > len(profile):
        raise ControlledQuestionSourceDerivationError(f"{label}_profile_range_invalid")
    return profile[start:end]


def _validate_candidate_profile_binding(
    *,
    profile: str,
    source_question_no: str,
    stem: str,
    options: list[str],
    answer: str,
    analysis: str,
    prompt_range: tuple[int, int],
    answer_range: tuple[int, int],
    analysis_range: tuple[int, int],
    anchors: dict[str, dict[str, Any]],
) -> None:
    """Bind report fields and offsets to current source text before any write."""
    prompt = _profile_slice(profile, prompt_range, "prompt")
    expected_prompt = f"{source_question_no}.{stem}{''.join(options)}"
    if normalize_text(prompt) != normalize_text(expected_prompt):
        raise ControlledQuestionSourceDerivationError("candidate_prompt_not_bound_to_source_profile")
    for label, range_value, value, marker in (
        ("answer", answer_range, answer, "【答案】"),
        ("analysis", analysis_range, analysis, "【解析】"),
    ):
        source_text = _profile_slice(profile, range_value, label)
        expected_anchor = str(anchors[label]["expected_text"])
        if normalize_text(source_text) != normalize_text(expected_anchor):
            raise ControlledQuestionSourceDerivationError(f"{label}_profile_not_bound_to_anchor")
        if f"{marker}{normalize_text(value)}" not in normalize_text(source_text):
            raise ControlledQuestionSourceDerivationError(f"candidate_{label}_not_bound_to_source_profile")


def import_discovery(connection: sqlite3.Connection, discovery: dict[str, Any]) -> dict[str, object]:
    """Import internal answer evidence and a derived question source from discovery.

    The caller owns the transaction boundary when composing this with a later
    candidate import.  Direct use remains atomic through the connection context.
    """
    if discovery.get("schema") != "p1-4b-controlled-source-candidate-discovery-v1":
        raise ControlledQuestionSourceDerivationError("discovery_schema_invalid")
    if discovery.get("candidate_import_authorized") is not False:
        raise ControlledQuestionSourceDerivationError("discovery_must_not_authorize_question_import")
    candidate = _required_mapping(discovery.get("candidate"), "candidate")
    controlled = _required_mapping(discovery.get("controlled_source"), "controlled_source")
    coverage = _required_mapping(controlled.get("coverage"), "controlled_source_coverage")
    if coverage.get("student_prompt_covered") is not True:
        raise ControlledQuestionSourceDerivationError("student_prompt_not_covered_by_current_segment")
    if coverage.get("answer_covered") is not False or coverage.get("analysis_covered") is not False:
        raise ControlledQuestionSourceDerivationError("answer_evidence_must_stay_outside_student_segment")
    stem, options, answer, analysis = _candidate_fields(candidate)
    source_question_no = _required_string(candidate, "source_question_no", "candidate")
    math = _required_mapping(candidate.get("math"), "candidate_math")
    if math.get("status") != "pass" or math.get("computed_answer") != answer:
        raise ControlledQuestionSourceDerivationError("candidate_independent_math_not_current_pass")
    profile_ranges = _required_mapping(controlled.get("profile_ranges"), "controlled_source_profile_ranges")
    prompt_range = _range(profile_ranges.get("prompt"), "prompt_range")
    answer_range = _range(profile_ranges.get("answer"), "answer_range")
    analysis_range = _range(profile_ranges.get("analysis"), "analysis_range")
    source = _current_source(connection, discovery)
    if not (source["source_character_range"][0] <= prompt_range[0] and prompt_range[1] <= source["source_character_range"][1]):
        raise ControlledQuestionSourceDerivationError("prompt_range_outside_current_student_segment")
    for range_value, label in ((answer_range, "answer"), (analysis_range, "analysis")):
        if range_value[0] < source["source_character_range"][1] and range_value[1] > source["source_character_range"][0]:
            raise ControlledQuestionSourceDerivationError(f"{label}_range_overlaps_student_segment")
    anchors = _validate_anchors(discovery, source)
    profile = _current_converted_profile(source)
    _validate_candidate_profile_binding(
        profile=profile,
        source_question_no=source_question_no,
        stem=stem,
        options=options,
        answer=answer,
        analysis=analysis,
        prompt_range=prompt_range,
        answer_range=answer_range,
        analysis_range=analysis_range,
        anchors=anchors,
    )
    manifest = {
        "schema": SCHEMA,
        "candidate": {
            "proposed_external_id": candidate.get("proposed_external_id"),
            "source_question_no": source_question_no,
            "stem": stem,
            "options": options,
            "answer": answer,
            "analysis": analysis,
            "field_anchors": anchors,
        },
        "controlled_source": {
            "source_id": source["source_id"],
            "segment_row_id": source["segment_row_id"],
            "converted_sha256": source["converted_sha256"],
            "profile_sha256": _digest_text(profile),
            "prompt_range": prompt_range,
            "answer_range": answer_range,
            "analysis_range": analysis_range,
        },
    }
    manifest_hash = _digest_text(_canonical(manifest))
    existing = connection.execute(
        "SELECT id FROM controlled_question_source_import_runs WHERE manifest_sha256=?", (manifest_hash,)
    ).fetchone()
    if existing is not None:
        return {"status": "reused", "import_run_id": existing[0], "manifest_sha256": manifest_hash}
    run_id = f"cqsir:{manifest_hash[:24]}"
    document_id = f"cqsdoc:{manifest_hash[:24]}"
    relative_path = source["converted_path"].relative_to(ROOT).as_posix()
    with connection:
        connection.execute(
            """INSERT INTO controlled_question_source_import_runs
               (id, manifest_sha256, importer_id, importer_version, status)
               VALUES (?, ?, ?, ?, 'validated')""",
            (run_id, manifest_hash, IMPORTER_ID, IMPORTER_VERSION),
        )
        existing_document = connection.execute(
            "SELECT id, file_hash, file_type, parse_status FROM source_documents WHERE relative_path=?",
            (relative_path,),
        ).fetchone()
        if existing_document is None:
            connection.execute(
                """INSERT INTO source_documents
                   (id, relative_path, file_hash, file_type, source_label, copyright_status, parse_status)
                   VALUES (?, ?, ?, 'docx', 'p1-4b-controlled-derived-source', 'authorized', 'parsed')""",
                (document_id, relative_path, source["converted_sha256"]),
            )
        else:
            if tuple(existing_document[1:]) != (source["converted_sha256"], "docx", "parsed"):
                raise ControlledQuestionSourceDerivationError("existing_source_document_contract_conflict")
            document_id = str(existing_document[0])
        fragment_ids: dict[str, str] = {}
        for field_name, anchor in anchors.items():
            fragment_payload = {
                "document_id": document_id,
                "field_name": field_name,
                "paragraph_index": anchor["paragraph_index"],
                "paragraph_sha256": anchor["paragraph_sha256"],
            }
            fragment_hash = _digest_text(_canonical(fragment_payload))
            fragment_id = f"cqsfrag:{fragment_hash[:24]}"
            connection.execute(
                """INSERT INTO source_fragments
                   (id, source_document_id, location_type, paragraph_index, question_number, raw_text, raw_hash)
                   VALUES (?, ?, 'paragraph', ?, ?, ?, ?)""",
                (
                    fragment_id,
                    document_id,
                    anchor["paragraph_index"],
                    source_question_no,
                    anchor["raw_text"],
                    anchor["paragraph_sha256"],
                ),
            )
            fragment_ids[field_name] = fragment_id
        answer_evidence_ids: dict[str, str] = {}
        for field_name, range_value, value in (("answer", answer_range, answer), ("analysis", analysis_range, analysis)):
            evidence_payload = {
                "run_id": run_id,
                "source_id": source["source_id"],
                "textbook_id": source["textbook_id"],
                "curriculum_node_id": source["curriculum_node_id"],
                "source_question_no": source_question_no,
                "field_name": field_name,
                "source_character_range": range_value,
                "normalized_text_sha256": _digest_text(normalize_text(value)),
                "anchor_fragment_id": fragment_ids[field_name],
            }
            evidence_hash = _digest_text(_canonical(evidence_payload))
            evidence_id = f"cqae:{evidence_hash[:24]}"
            connection.execute(
                """INSERT INTO controlled_question_answer_evidence
                   (id, import_run_id, controlled_source_id, textbook_id, curriculum_node_id,
                    source_question_no, field_name, source_character_range_json,
                    normalized_text_sha256, internal_only, evidence_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    evidence_id,
                    run_id,
                    source["source_id"],
                    evidence_payload["textbook_id"],
                    evidence_payload["curriculum_node_id"],
                    source_question_no,
                    field_name,
                    _canonical(list(range_value)),
                    evidence_payload["normalized_text_sha256"],
                    evidence_hash,
                ),
            )
            answer_evidence_ids[field_name] = evidence_id
        derivation_payload = {
            "run_id": run_id,
            "source_document_id": document_id,
            "source_question_no": source_question_no,
            "source_id": source["source_id"],
            "student_segment_row_id": source["segment_row_id"],
            "textbook_id": source["textbook_id"],
            "curriculum_node_id": source["curriculum_node_id"],
            "student_prompt_range": prompt_range,
            "student_prompt_sha256": _digest_text(normalize_text(str(candidate.get("stem", "")) + "".join(candidate.get("options", [])))),
            "answer_evidence_ids": answer_evidence_ids,
        }
        derivation_hash = _digest_text(_canonical(derivation_payload))
        connection.execute(
            """INSERT INTO controlled_question_source_derivations
               (source_document_id, source_question_no, import_run_id, controlled_source_id,
                student_segment_row_id, textbook_id, curriculum_node_id,
                student_prompt_range_json, student_prompt_sha256, derivation_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                document_id,
                source_question_no,
                run_id,
                source["source_id"],
                source["segment_row_id"],
                derivation_payload["textbook_id"],
                derivation_payload["curriculum_node_id"],
                _canonical(list(prompt_range)),
                derivation_payload["student_prompt_sha256"],
                derivation_hash,
            ),
        )
    return {
        "status": "validated_internal_evidence_only",
        "import_run_id": run_id,
        "manifest_sha256": manifest_hash,
        "source_document_id": document_id,
        "source_fragment_ids": fragment_ids,
        "answer_evidence_ids": answer_evidence_ids,
        "derivation_hash": derivation_hash,
    }
