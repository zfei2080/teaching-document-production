"""Read-only discovery of source-bound P1-4b candidate material.

The first discovery target is intentionally small: the first complete choice
question in the current controlled consolidation source.  It produces an
auditable import precondition report, not a database question or student
artifact.  In particular, a prompt being inside a student-safe segment does
not make its answer and analysis eligible for import.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import sqlite3

from docx_forensics import extract_paragraphs
from fidelity_audit import normalize_text
from independent_math_validators import validate as validate_math


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
DEFAULT_P1_2B_AUDIT = ROOT / "output" / "audits" / "p1-2b_controlled_content_validation.json"
SCHEMA = "p1-4b-controlled-source-candidate-discovery-v1"
TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
NODE_ID = "bsd-math-8x-2026-node-01-section-02"

STEM = "\u5df2\u77e5\u4e00\u4e2a\u7b49\u8170\u4e09\u89d2\u5f62\u4e24\u8fb9\u957f\u5206\u522b\u4e3a5\uff0c6\uff0c\u5219\u5b83\u7684\u5468\u957f\u4e3a(     )"
OPTIONS_TEXT = "A\uff0e16    \tB\uff0e17       C\uff0e16\u621617\t\t\tD\uff0e10\u621612"
OPTIONS = ("A\uff0e16", "B\uff0e17", "C\uff0e16\u621617", "D\uff0e10\u621612")
ANSWER = "C"
ANALYSIS = "\u6ce8\u610f\u5206\u7c7b\u8ba8\u8bba."
PROFILE_PROMPT = "1." + STEM.replace("     ", "") + "A\uff0e16B\uff0e17C\uff0e16\u621617D\uff0e10\u621612"
PROFILE_ANSWER = "1.\u3010\u7b54\u6848\u3011C\uff1b"
PROFILE_ANALYSIS = "\u3010\u89e3\u6790\u3011" + ANALYSIS


class P14BDiscoveryError(RuntimeError):
    """Raised when the controlled source cannot prove the discovery facts."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _readonly_connection(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise P14BDiscoveryError("development_database_missing")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _file_fact(path: str, expected_hash: str) -> dict[str, object]:
    candidate = Path(path)
    result: dict[str, object] = {"path": str(candidate), "exists": candidate.is_file(), "sha256": None, "matches_expected": False}
    if not candidate.is_file():
        return result
    actual = _sha256(candidate)
    result["sha256"] = actual
    result["matches_expected"] = actual.casefold() == expected_hash.casefold()
    return result


def _current_consolidation_source(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        """SELECT s.id AS segment_row_id, s.source_character_range_json,
                  s.normalized_text_sha256, src.id AS source_id,
                  src.original_path, src.original_sha256, src.archive_path,
                  src.archive_sha256, src.converted_path, src.converted_sha256,
                  src.source_profile_sha256
             FROM current_controlled_content_segments s
             JOIN controlled_content_sources src ON src.id=s.source_id
            WHERE s.textbook_id=? AND s.curriculum_node_id=?
              AND s.content_type='consolidation_practice'
              AND s.layer='basic_reinforcement'""",
        (TEXTBOOK_ID, NODE_ID),
    ).fetchall()
    if len(rows) != 1:
        raise P14BDiscoveryError("current_consolidation_source_not_unique")
    row = dict(rows[0])
    try:
        character_range = json.loads(str(row["source_character_range_json"]))
    except json.JSONDecodeError as exc:
        raise P14BDiscoveryError("current_consolidation_source_range_invalid") from exc
    if not isinstance(character_range, list) or len(character_range) != 2 or not all(isinstance(value, int) for value in character_range):
        raise P14BDiscoveryError("current_consolidation_source_range_invalid")
    triple = {
        "original": _file_fact(str(row["original_path"]), str(row["original_sha256"])),
        "archive": _file_fact(str(row["archive_path"]), str(row["archive_sha256"])),
        "converted": _file_fact(str(row["converted_path"]), str(row["converted_sha256"])),
    }
    if not all(bool(item["matches_expected"]) for item in triple.values()):
        raise P14BDiscoveryError("current_consolidation_source_triple_not_current")
    return {**row, "source_character_range": character_range, "triple": triple}


def _p1_2b_profile(source: dict[str, Any], audit_path: Path) -> str:
    if not audit_path.is_file():
        raise P14BDiscoveryError("p1_2b_audit_missing")
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise P14BDiscoveryError("p1_2b_audit_unreadable") from exc
    records = audit.get("records")
    if not isinstance(records, list):
        raise P14BDiscoveryError("p1_2b_audit_records_invalid")
    expected_hash = str(source["converted_sha256"])
    matched: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or record.get("content_type") != "consolidation_practice":
            continue
        manifest = record.get("manifest")
        if not isinstance(manifest, dict):
            continue
        converted = manifest.get("conversion", {}).get("converted", {})
        if isinstance(converted, dict) and str(converted.get("sha256", "")).casefold() == expected_hash.casefold():
            matched.append(manifest)
    if len(matched) != 1:
        raise P14BDiscoveryError("p1_2b_profile_not_bound_to_current_converted_source")
    profile = matched[0].get("profiles", {}).get("converted", {})
    text = profile.get("normalized_content_text") if isinstance(profile, dict) else None
    if not isinstance(text, str) or not text:
        raise P14BDiscoveryError("p1_2b_profile_text_missing")
    return text


def _unique_paragraph_anchor(paragraphs: list[Any], expected: str, label: str) -> dict[str, object]:
    expected_normalized = normalize_text(expected)
    matches = [paragraph for paragraph in paragraphs if expected_normalized in normalize_text(paragraph.text)]
    if len(matches) != 1:
        raise P14BDiscoveryError(f"{label}_paragraph_anchor_not_unique")
    paragraph = matches[0]
    return {
        "paragraph_index": paragraph.index,
        "paragraph_sha256": paragraph.sha256,
        "expected_text": expected,
    }


def _range(text: str, expected: str, label: str) -> list[int]:
    start = text.find(expected)
    if start < 0 or text.find(expected, start + 1) >= 0:
        raise P14BDiscoveryError(f"{label}_profile_range_not_unique")
    return [start, start + len(expected)]


def build_p1_4b_source_discovery(
    database_path: str | Path = DEFAULT_DATABASE,
    *,
    p1_2b_audit_path: str | Path = DEFAULT_P1_2B_AUDIT,
) -> dict[str, object]:
    """Discover one source-bound candidate and report why it remains blocked."""
    database = Path(database_path).resolve()
    audit_path = Path(p1_2b_audit_path).resolve()
    before_hash = _sha256(database)
    connection = _readonly_connection(database)
    try:
        source = _current_consolidation_source(connection)
        profile_text = _p1_2b_profile(source, audit_path)
        paragraphs = extract_paragraphs(Path(str(source["converted_path"])))
        anchors = {
            "stem": _unique_paragraph_anchor(paragraphs, STEM, "stem"),
            "options": _unique_paragraph_anchor(paragraphs, OPTIONS_TEXT, "options"),
            "answer": _unique_paragraph_anchor(paragraphs, PROFILE_ANSWER, "answer"),
            "analysis": _unique_paragraph_anchor(paragraphs, PROFILE_ANALYSIS, "analysis"),
        }
        prompt_range = _range(profile_text, PROFILE_PROMPT, "prompt")
        answer_range = _range(profile_text, PROFILE_ANSWER, "answer")
        analysis_range = _range(profile_text, PROFILE_ANALYSIS, "analysis")
        range_start, range_end = source["source_character_range"]
        prompt_covered = range_start <= prompt_range[0] and prompt_range[1] <= range_end
        answer_covered = range_start <= answer_range[0] and answer_range[1] <= range_end
        analysis_covered = range_start <= analysis_range[0] and analysis_range[1] <= range_end
        math = validate_math({
            "source_question_no": "controlled-practice-001",
            "stem": STEM,
            "options": list(OPTIONS),
        })
        blockers = [
            "controlled_question_source_derivation_contract_missing",
            "answer_and_analysis_are_outside_current_student_safe_segment",
            "current_question_mapping_and_p1_3c_approval_missing",
            "question_pedagogical_role_evidence_contract_missing",
        ]
        if not prompt_covered:
            blockers.insert(0, "question_prompt_not_covered_by_current_student_safe_segment")
        if math.status != "pass" or math.computed_answer != ANSWER:
            blockers.insert(0, "independent_math_validation_not_pass")
        report: dict[str, object] = {
            "schema": SCHEMA,
            "purpose": "read_only_p1_4b_source_candidate_discovery_without_question_import",
            "database": {"path": str(database), "sha256_before": before_hash, "sha256_after": None, "unchanged": None},
            "candidate": {
                "proposed_external_id": "p1-4b-consolidation-choice-001",
                "source_question_no": "1",
                "question_type": "choice",
                "stem": STEM,
                "options": list(OPTIONS),
                "answer": ANSWER,
                "analysis": ANALYSIS,
                "field_anchors": anchors,
                "math": {"status": math.status, "computed_answer": math.computed_answer, "evidence": math.evidence},
            },
            "controlled_source": {
                "source_id": source["source_id"],
                "segment_row_id": source["segment_row_id"],
                "source_character_range": source["source_character_range"],
                "normalized_text_sha256": source["normalized_text_sha256"],
                "source_profile_sha256": source["source_profile_sha256"],
                "triple": source["triple"],
                "profile_ranges": {
                    "prompt": prompt_range,
                    "answer": answer_range,
                    "analysis": analysis_range,
                },
                "coverage": {
                    "student_prompt_covered": prompt_covered,
                    "answer_covered": answer_covered,
                    "analysis_covered": analysis_covered,
                },
            },
            "candidate_import_authorized": False,
            "student_document_generation_authorized": False,
            "decision": "blocked_pending_separate_answer_evidence_and_full_question_admission_chain",
            "blockers": blockers,
        }
    finally:
        connection.close()
    after_hash = _sha256(database)
    if after_hash != before_hash:
        raise P14BDiscoveryError("read_only_candidate_discovery_changed_database")
    report["database"]["sha256_after"] = after_hash
    report["database"]["unchanged"] = True
    return report


def write_p1_4b_source_discovery(report: dict[str, object], output_path: str | Path) -> Path:
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return output
