"""Run the temporary P1-2b controlled-source conversion and fidelity exercise.

This runner does not touch protected SQLite databases and emits only temporary
archive/conversion files plus a read-only audit JSON.  The source list is an
explicit allowlist of known local originals; parent-folder labels never assign
teaching roles or delivery eligibility.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import sys

from controlled_content_evidence import build_controlled_content_evidence, write_evidence_manifest


ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "<local-scratch>" / "知识点"
TEMP_ROOT = ROOT / "data" / "dev" / "p1-2b-temporary"
AUDIT_PATH = ROOT / "output" / "audits" / "p1-2b_controlled_content_validation.json"
PROTECTED_DATABASES = (ROOT / "question_bank.db", ROOT / "data" / "dev" / "teaching_docs_dev.db")


SOURCE_SPECS = (
    (
        Path("北师版初中数学重难点讲义+巩固练习（基础+提高）")
        / "【4】初二下册-数学北师大版"
        / "58等腰三角形（基础）"
        / "等腰三角形（基础）知识讲解.doc",
        "knowledge_explanation",
        "basic-knowledge-explanation",
        True,
    ),
    (
        Path("北师版初中数学重难点讲义+巩固练习（基础+提高）")
        / "【4】初二下册-数学北师大版"
        / "58等腰三角形（基础）"
        / "等腰三角形（基础）巩固练习.doc",
        "consolidation_practice",
        "basic-consolidation-practice",
        True,
    ),
    (
        Path("北师版初中数学重难点讲义+巩固练习（基础+提高）")
        / "【4】初二下册-数学北师大版"
        / "59等腰三角形（提高）"
        / "等腰三角形（提高）知识讲解.doc",
        "knowledge_explanation",
        "advanced-knowledge-explanation",
        False,
    ),
    (
        Path("北师版初中数学重难点讲义+巩固练习（基础+提高）")
        / "【4】初二下册-数学北师大版"
        / "59等腰三角形（提高）"
        / "等腰三角形（提高）巩固练习.doc",
        "consolidation_practice",
        "advanced-consolidation-practice",
        False,
    ),
)


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        return {
            "questions": connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            "question_textbooks": connection.execute("SELECT COUNT(*) FROM question_textbooks").fetchone()[0],
            "question_knowledge_points": connection.execute("SELECT COUNT(*) FROM question_knowledge_points").fetchone()[0],
            "question_verifications": connection.execute("SELECT COUNT(*) FROM question_verifications").fetchone()[0],
            "teaching_documents": connection.execute("SELECT COUNT(*) FROM teaching_documents").fetchone()[0],
            "quality_reports": connection.execute("SELECT COUNT(*) FROM quality_reports").fetchone()[0],
            "question_usage": connection.execute("SELECT COUNT(*) FROM question_usage").fetchone()[0],
        }
    finally:
        connection.close()


def _find_source(relative_path: Path) -> Path:
    source_root = SOURCE_ROOT.resolve()
    candidate = (source_root / relative_path).resolve()
    try:
        candidate.relative_to(source_root)
    except ValueError as exc:
        raise RuntimeError(f"source_path_outside_root:{relative_path}") from exc
    if candidate.suffix.lower() != ".doc" or not candidate.is_file():
        raise RuntimeError(f"source_file_missing_or_not_doc:{relative_path}")
    return candidate


def _load_existing_records() -> list[dict[str, object]]:
    if not AUDIT_PATH.is_file():
        raise RuntimeError("existing_p1_2b_audit_missing_for_current_controlled_sources")
    try:
        payload = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("existing_p1_2b_audit_unreadable") from exc
    if payload.get("schema") != "p1-2b-controlled-content-run-v1":
        raise RuntimeError("existing_p1_2b_audit_schema_invalid")
    records = payload.get("records")
    if not isinstance(records, list):
        raise RuntimeError("existing_p1_2b_audit_records_invalid")
    return [record for record in records if isinstance(record, dict)]


def _reuse_current_record(
    existing_records: list[dict[str, object]],
    *,
    source: Path,
    relative_path: Path,
    content_type: str,
    source_key: str,
) -> dict[str, object]:
    source_hash = _sha256(source)
    matches: list[dict[str, object]] = []
    for record in existing_records:
        if record.get("content_type") != content_type:
            continue
        manifest = record.get("manifest")
        if not isinstance(manifest, dict):
            continue
        source_info = manifest.get("source")
        conversion = manifest.get("conversion")
        if not isinstance(source_info, dict) or not isinstance(conversion, dict):
            continue
        converted = conversion.get("converted")
        if not isinstance(converted, dict):
            continue
        if str(source_info.get("path", "")) != str(source):
            continue
        if str(source_info.get("sha256", "")).casefold() != source_hash.casefold():
            continue
        converted_path = Path(str(converted.get("path", "")))
        converted_hash = str(converted.get("sha256", ""))
        if not converted_path.is_file() or _sha256(converted_path).casefold() != converted_hash.casefold():
            continue
        if record.get("status") != "candidate_content_only" or manifest.get("delivery_eligible") is not False:
            continue
        matches.append(record)
    if len(matches) != 1:
        raise RuntimeError(f"current_controlled_source_record_not_unique_or_not_current:{source_key}:{len(matches)}")
    record = dict(matches[0])
    record.update(
        {
            "source_key": source_key,
            "source_relative_path": str(relative_path),
            "source": str(source),
            "reused_current_verified_record": True,
        }
    )
    return record


def main() -> int:
    before_hashes = {str(path): _sha256(path) for path in PROTECTED_DATABASES}
    before_counts = _counts(ROOT / "data" / "dev" / "teaching_docs_dev.db")
    converter = (sys.executable, str((ROOT / "word_com_doc_converter.py").resolve()))
    existing_records = _load_existing_records()
    records = []
    for relative_path, content_type, source_key, reuse_current_record in SOURCE_SPECS:
        source = _find_source(relative_path)
        if reuse_current_record:
            records.append(
                _reuse_current_record(
                    existing_records,
                    source=source,
                    relative_path=relative_path,
                    content_type=content_type,
                    source_key=source_key,
                )
            )
            continue
        evidence = build_controlled_content_evidence(
            source,
            content_type=content_type,
            allowed_root=SOURCE_ROOT,
            archive_root=TEMP_ROOT / "archive",
            conversion_root=TEMP_ROOT / "converted",
            converter_command=converter,
        )
        manifest_path = TEMP_ROOT / "manifests" / f"{source_key}.json"
        write_evidence_manifest(evidence, manifest_path)
        records.append(
            {
                "source_key": source_key,
                "source_relative_path": str(relative_path),
                "source": str(source),
                "content_type": content_type,
                "status": evidence.status,
                "manifest": evidence.manifest,
                "reused_current_verified_record": False,
            }
        )

    after_hashes = {str(path): _sha256(path) for path in PROTECTED_DATABASES}
    after_counts = _counts(ROOT / "data" / "dev" / "teaching_docs_dev.db")
    report = {
        "schema": "p1-2b-controlled-content-run-v1",
        "purpose": "temporary_validation_only",
        "records": records,
        "protected_databases": {
            "before_hashes": before_hashes,
            "after_hashes": after_hashes,
            "hashes_unchanged": before_hashes == after_hashes,
            "before_counts": before_counts,
            "after_counts": after_counts,
            "counts_unchanged": before_counts == after_counts,
        },
        "delivery_or_approval_performed": False,
    }
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"audit={AUDIT_PATH}")
    print("protected_databases_unchanged=" + str(report["protected_databases"]["hashes_unchanged"]))
    print("records=" + ",".join(record["status"] for record in records))
    return 0 if report["protected_databases"]["hashes_unchanged"] and report["protected_databases"]["counts_unchanged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
