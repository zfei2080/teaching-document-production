"""Run available automatic verification stages for pending candidates.

The runner is intentionally fail-closed. A stage without a capable validator is
recorded as ``unsupported``. That evidence keeps the question isolated and is
never converted into a manual-review task or an approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from automatic_gate import apply_automatic_admission
from input_snapshot import refresh_current_snapshot
from asset_semantics_validator import VALIDATOR_ID as ASSET_SEMANTICS_VALIDATOR_ID
from asset_semantics_validator import VALIDATOR_VERSION as ASSET_SEMANTICS_VALIDATOR_VERSION
from asset_semantics_validator import validate as validate_asset_semantics
from candidate_consistency import all_pass as consistency_all_pass
from candidate_consistency import check_candidate_consistency
from docx_forensics import ParagraphFragment, extract_paragraphs
from fidelity_audit import all_pass as fidelity_all_pass
from fidelity_audit import check_question_fields
from independent_math_validators import validate as validate_math
from textbook_scope_validator import VALIDATOR_ID as TEXTBOOK_SCOPE_VALIDATOR_ID
from textbook_scope_validator import VALIDATOR_VERSION as TEXTBOOK_SCOPE_VALIDATOR_VERSION
from textbook_scope_validator import validate as validate_textbook_scope

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"
VALIDATOR_VERSION = "automatic-runner-v3"
SUPPORTED_SOURCE_FILE_TYPES = frozenset({"docx"})


def normalize_answer(value: str | None) -> str:
    return "".join((value or "").split()).replace("，", ",").replace("；", ";")


def _math_answers_match(computed: str | None, stored: str | None) -> bool:
    """NFKC + set-aware comparison for multi-value answers (e.g. "26或22" vs "22或26")."""

    def norm(value: str | None) -> str:
        text = unicodedata.normalize("NFKC", value or "")
        return "".join(text.split()).rstrip(";.;，、")

    left = norm(computed)
    right = norm(stored)
    if left == right:
        return True
    if not left or not right:
        return False
    left_parts = [part for part in re.split(r"或|；|;|，|,|、", left) if part]
    right_parts = [part for part in re.split(r"或|；|;|，|,|、", right) if part]
    return len(left_parts) > 1 and len(left_parts) == len(right_parts) and set(left_parts) == set(right_parts)


# Stage A validators perform their own final comparison against the source
# answer (the runner only supplies it); their pass status is authoritative.
SELF_COMPARING_VALIDATORS = frozenset({"choice-letter-v1", "fill-numeric-v1", "fill-expr-v1"})


def _source_answer(conn: sqlite3.Connection, question_id: str) -> str | None:
    """Resolve the stored answer for final comparison.

    Preference: question_internal_evidence.answer, then
    content_item_math_validation_evidence.source_answer, then questions.answer.
    Tables may be absent in minimal test schemas, so lookups are defensive.
    """
    try:
        row = conn.execute(
            "SELECT internal_payload FROM question_internal_evidence "
            "WHERE question_id=? AND field_name='answer' AND internal_payload IS NOT NULL "
            "AND trim(internal_payload)<>'' ORDER BY created_at DESC, id DESC LIMIT 1",
            (question_id,),
        ).fetchone()
        if row is not None and row[0]:
            return row[0]
    except sqlite3.OperationalError:
        pass
    try:
        row = conn.execute(
            "SELECT source_answer FROM content_item_math_validation_evidence "
            "WHERE question_id=? AND source_answer IS NOT NULL AND trim(source_answer)<>'' "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (question_id,),
        ).fetchone()
        if row is not None and row[0]:
            return row[0]
    except sqlite3.OperationalError:
        pass
    row = conn.execute(
        "SELECT answer FROM questions WHERE id=? AND answer IS NOT NULL AND trim(answer)<>''",
        (question_id,),
    ).fetchone()
    return None if row is None else row[0]


def stable_hash(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _base_evidence(question_id: str, source_document_id: str | None, *, coverage: str, unsupported_reason: str | None = None) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "question_id": question_id,
        "source_document_id": source_document_id,
        "coverage": coverage,
        "unsupported_reason": unsupported_reason,
    }
    return evidence


def _merge_evidence(base: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(base)
    if extra:
        merged.update(extra)
    return merged


def write_result(
    conn: sqlite3.Connection,
    *,
    question_id: str,
    verification_type: str,
    status: str,
    evidence: dict,
    computed_answer: str | None = None,
    validator_id: str = "automatic_verification_runner",
    validator_version: str = VALIDATOR_VERSION,
    input_hash: str,
) -> None:
    conn.execute(
        """INSERT INTO question_verifications
        (id, question_id, verification_type, validator_id, validator_version,
         status, computed_answer, evidence_json, input_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            question_id,
            verification_type,
            validator_id,
            validator_version,
            status,
            computed_answer,
            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            input_hash,
        ),
    )


class SourceDocumentResolver:
    def __init__(self, root: Path):
        self.root = root
        self._docx_cache: dict[str, dict[str, ParagraphFragment]] = {}

    def load(
        self,
        source_document_id: str,
        relative_path: str | None,
        file_type: str | None,
        parse_status: str | None,
        expected_file_hash: str | None,
    ):
        resolved = self.root / (relative_path or "")
        if not relative_path:
            return {
                "status": "unsupported",
                "coverage": "none",
                "unsupported_reason": "source_document_relative_path_missing",
                "path": str(resolved),
                "fragments": {},
            }
        if file_type not in SUPPORTED_SOURCE_FILE_TYPES:
            return {
                "status": "unsupported",
                "coverage": "none",
                "unsupported_reason": f"unsupported_source_document_type:{file_type or 'unknown'}",
                "path": str(resolved),
                "fragments": {},
            }
        if parse_status != "parsed":
            return {
                "status": "unsupported",
                "coverage": "none",
                "unsupported_reason": f"source_document_parse_status:{parse_status or 'unknown'}",
                "path": str(resolved),
                "fragments": {},
            }
        if not resolved.is_file():
            return {
                "status": "unsupported",
                "coverage": "none",
                "unsupported_reason": "source_document_missing",
                "path": str(resolved),
                "fragments": {},
            }
        actual_file_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if not expected_file_hash or actual_file_hash != expected_file_hash:
            return {
                "status": "unsupported",
                "coverage": "none",
                "unsupported_reason": "source_file_hash_mismatch",
                "path": str(resolved),
                "fragments": {},
            }
        cache_key = f"{source_document_id}:{resolved}"
        if cache_key not in self._docx_cache:
            fragments = extract_paragraphs(resolved)
            self._docx_cache[cache_key] = {
                f"{source_document_id}-p{item.index:03d}": item for item in fragments
            }
        return {
            "status": "loaded",
            "coverage": "full",
            "unsupported_reason": None,
            "path": str(resolved),
            "fragments": self._docx_cache[cache_key],
        }


def _source_document_row(conn: sqlite3.Connection, question_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT q.source_document_id, sd.relative_path, sd.file_hash, sd.file_type, sd.parse_status
        FROM questions q
        LEFT JOIN source_documents sd ON sd.id = q.source_document_id
        WHERE q.id=?
        """,
        (question_id,),
    ).fetchone()


def _provenance(conn: sqlite3.Connection, question_id: str) -> dict[str, list[str]]:
    provenance: dict[str, list[str]] = {}
    for item in conn.execute(
        "SELECT field_name, source_fragment_id FROM question_source_fragments WHERE question_id=? ORDER BY rowid",
        (question_id,),
    ):
        provenance.setdefault(item["field_name"], []).append(item["source_fragment_id"])
    return provenance


def _fidelity_coverage(findings: list[Any]) -> str:
    if findings and all(item.field_name in {"stem", "options", "answer", "analysis"} for item in findings):
        return "full"
    return "partial"


def _canonical_fragment_id(source_document_id: str, fragment_id: str) -> str:
    if fragment_id.startswith(f"{source_document_id}-p"):
        return fragment_id
    if "-p" in fragment_id:
        return f"{source_document_id}-p{fragment_id.rsplit('-p', 1)[-1]}"
    return fragment_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Run controlled automatic verification")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument(
        "--question-id",
        dest="question_ids",
        action="append",
        default=[],
        help="Verify only this question ID; repeat the option for multiple questions.",
    )
    parser.add_argument(
        "--question-id-file",
        type=Path,
        default=None,
        help="Read question IDs (one per line) and verify them in addition to --question-id.",
    )
    args = parser.parse_args()
    if args.question_id_file is not None:
        lines = [line.strip() for line in args.question_id_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        args.question_ids.extend(lines)
    resolver = SourceDocumentResolver(ROOT)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        values: list[object] = []
        if args.question_ids:
            # Explicit enumeration is an explicit scope override: status
            # filters would otherwise drop pending questions from the set.
            filters = [f"id IN ({','.join('?' for _ in args.question_ids)})"]
            values.extend(args.question_ids)
        else:
            filters = [
                "quality_status IN ('needs_review', 'blocked')",
                "review_status <> 'rejected'",
            ]
        rows = conn.execute(
            f"""
            SELECT id, stem, options_json, answer, analysis, question_type, source_document_id
            FROM questions
            WHERE {' AND '.join(filters)}
            ORDER BY CAST(source_question_no AS INTEGER)
            """,
            values,
        ).fetchall()
        with conn:
            for row in rows:
                input_hash = refresh_current_snapshot(conn, row["id"])
                source_answer = _source_answer(conn, row["id"])
                source_row = _source_document_row(conn, row["id"])
                source_document_id = row["source_document_id"]
                source_info = resolver.load(
                    source_document_id or "",
                    None if source_row is None else source_row["relative_path"],
                    None if source_row is None else source_row["file_type"],
                    None if source_row is None else source_row["parse_status"],
                    None if source_row is None else source_row["file_hash"],
                )

                if source_info["status"] != "loaded":
                    unsupported_reason = source_info["unsupported_reason"]
                    common_evidence = _merge_evidence(
                        _base_evidence(row["id"], source_document_id, coverage=source_info["coverage"], unsupported_reason=unsupported_reason),
                        {"source_path": source_info["path"]},
                    )
                    write_result(
                        conn,
                        question_id=row["id"],
                        verification_type="source_fidelity",
                        status="unsupported",
                        evidence=common_evidence,
                        input_hash=input_hash,
                    )
                else:
                    provenance = {
                        field_name: [
                            _canonical_fragment_id(source_document_id or "", fragment_id)
                            for fragment_id in fragment_ids
                        ]
                        for field_name, fragment_ids in _provenance(conn, row["id"]).items()
                    }
                    fidelity = check_question_fields(
                        stem=row["stem"],
                        options_json=row["options_json"],
                        answer=row["answer"],
                        analysis=row["analysis"],
                        question_type=row["question_type"],
                        fragments=source_info["fragments"],
                        provenance=provenance,
                    )
                    write_result(
                        conn,
                        question_id=row["id"],
                        verification_type="source_fidelity",
                        status="pass" if fidelity_all_pass(fidelity) else "fail",
                        evidence=_merge_evidence(
                            _base_evidence(row["id"], source_document_id, coverage=_fidelity_coverage(fidelity)),
                            {"findings": [asdict(item) for item in fidelity], "source_path": source_info["path"]},
                        ),
                        input_hash=input_hash,
                    )

                asset_paths = [
                    str(ROOT / item[0])
                    for item in conn.execute(
                        "SELECT relative_path FROM question_assets WHERE question_id=? AND asset_type='image'",
                        (row["id"],),
                    )
                    if item[0]
                ]
                consistency = check_candidate_consistency(
                    question_type=row["question_type"],
                    options_json=row["options_json"],
                    answer=source_answer or "",
                    asset_paths=asset_paths,
                )
                write_result(
                    conn,
                    question_id=row["id"],
                    verification_type="structural_consistency",
                    status="pass" if consistency_all_pass(consistency) else "fail",
                    evidence=_merge_evidence(
                        _base_evidence(row["id"], source_document_id, coverage="full"),
                        {"findings": [item.__dict__ for item in consistency]},
                    ),
                    input_hash=input_hash,
                )

                math_result = validate_math(
                    {
                        "source_question_no": conn.execute(
                            "SELECT source_question_no FROM questions WHERE id=?", (row["id"],)
                        ).fetchone()[0],
                        "stem": row["stem"],
                        "options": json.loads(row["options_json"]),
                        "question_type": row["question_type"],
                        "answer": source_answer,
                    }
                )
                math_validator_id = getattr(math_result, "validator_id", None)
                if math_result.status == "pass":
                    matches = _math_answers_match(math_result.computed_answer, source_answer)
                    if math_validator_id in SELF_COMPARING_VALIDATORS:
                        # These validators compare against the source answer
                        # themselves (set-aware, unit-aligned); their pass is
                        # authoritative and the runner records the comparison.
                        math_status = "pass"
                    else:
                        math_status = "pass" if matches else "fail"
                    math_evidence = {
                        "validator_id": math_validator_id,
                        "validator_evidence": math_result.evidence,
                        "computed_answer": math_result.computed_answer,
                        "stored_answer": source_answer,
                        "normalized_match": matches,
                    }
                    math_coverage = "full"
                    unsupported_reason = None
                else:
                    math_status = math_result.status
                    math_evidence = {
                        "validator_id": math_validator_id,
                        "reason": math_result.evidence,
                    }
                    math_coverage = "none" if math_result.status == "unsupported" else "partial"
                    unsupported_reason = math_result.evidence if math_result.status == "unsupported" else None
                write_result(
                    conn,
                    question_id=row["id"],
                    verification_type="mathematical_independent",
                    status=math_status,
                    evidence=_merge_evidence(
                        _base_evidence(row["id"], source_document_id, coverage=math_coverage, unsupported_reason=unsupported_reason),
                        math_evidence,
                    ),
                    computed_answer=math_result.computed_answer,
                    input_hash=input_hash,
                )
                textbook_scope = validate_textbook_scope(conn, row["id"])
                write_result(
                    conn,
                    question_id=row["id"],
                    verification_type="textbook_scope",
                    status=textbook_scope.status,
                    evidence=_merge_evidence(
                        _base_evidence(
                            row["id"],
                            source_document_id,
                            coverage="none" if textbook_scope.status == "unsupported" else "full",
                            unsupported_reason=(textbook_scope.evidence if isinstance(textbook_scope.evidence, str) else None)
                            if textbook_scope.status == "unsupported"
                            else None,
                        ),
                        textbook_scope.evidence if isinstance(textbook_scope.evidence, dict) else {"detail": textbook_scope.evidence},
                    ),
                    validator_id=TEXTBOOK_SCOPE_VALIDATOR_ID,
                    validator_version=TEXTBOOK_SCOPE_VALIDATOR_VERSION,
                    input_hash=input_hash,
                )
                asset_status, asset_evidence = validate_asset_semantics(conn, row["id"])
                write_result(
                    conn,
                    question_id=row["id"],
                    verification_type="asset_semantics",
                    status=asset_status,
                    evidence=_merge_evidence(
                        _base_evidence(
                            row["id"],
                            source_document_id,
                            coverage="none" if asset_status == "unsupported" else "full",
                            unsupported_reason=asset_evidence.get("reason") if asset_status == "unsupported" else None,
                        ),
                        asset_evidence,
                    ),
                    validator_id=ASSET_SEMANTICS_VALIDATOR_ID,
                    validator_version=ASSET_SEMANTICS_VALIDATOR_VERSION,
                    input_hash=input_hash,
                )
                apply_automatic_admission(conn, row["id"])
        summary = conn.execute(
            "SELECT verification_type, status, COUNT(*) FROM question_verifications GROUP BY verification_type, status ORDER BY verification_type, status"
        ).fetchall()
        for item in summary:
            print("|".join(str(value) for value in item))
        print(f"VERIFIED_PENDING_QUESTIONS={len(rows)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
