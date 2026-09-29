"""Read-only P1-2e audit for the controlled legacy-content conversion chain.

The teaching-content chain is valid only while its original, archive, and DOCX
conversion bytes are readable and hash-bound.  This audit neither regenerates
files nor changes the development database.  It records whether the approved
Microsoft Word COM converter is available to recover a failed conversion chain.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
import json
import os
import sqlite3
import subprocess


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
DEFAULT_REPORT = ROOT / "output" / "audits" / "p1-2e_conversion_prerequisite_audit.json"
SCHEMA = "p1-2e-controlled-content-conversion-prerequisite-audit-v1"
TARGET_TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
TARGET_NODE_ID = "bsd-math-8x-2026-node-01-section-02"
WORD_CLSID = "{000209FF-0000-0000-C000-000000000046}"


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _database_sha256(path: Path) -> str:
    return _sha256(path)


def _file_probe(path: str, expected_hash: str) -> dict[str, Any]:
    candidate = Path(path)
    result: dict[str, Any] = {
        "path": str(candidate),
        "expected_sha256": expected_hash,
        "exists": candidate.is_file(),
        "readable": False,
        "sha256": None,
        "matches_expected": False,
        "error": None,
    }
    if not candidate.is_file():
        result["error"] = "missing"
        return result
    try:
        actual = _sha256(candidate)
    except OSError as exc:
        result["error"] = f"unreadable:{type(exc).__name__}:{exc.errno}"
        return result
    result["readable"] = True
    result["sha256"] = actual
    result["matches_expected"] = actual.casefold() == str(expected_hash).casefold()
    if not result["matches_expected"]:
        result["error"] = "hash_mismatch"
    return result


def _word_com_registered() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        for key in (
            rf"Word.Application\\CLSID",
            rf"CLSID\\{WORD_CLSID}",
            rf"Wow6432Node\\CLSID\\{WORD_CLSID}",
        ):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key):
                    return True
            except FileNotFoundError:
                continue
    except OSError:
        return False
    return False


def _pandoc_input_formats(
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    try:
        completed = command_runner(
            ["pandoc", "--list-input-formats"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "input_formats": [], "error": type(exc).__name__}
    formats = sorted(
        line.strip() for line in (completed.stdout or "").splitlines() if line.strip()
    )
    return {
        "available": completed.returncode == 0,
        "input_formats": formats,
        "supports_doc": "doc" in formats,
        "supports_docx": "docx" in formats,
        "error": None if completed.returncode == 0 else f"exit:{completed.returncode}",
    }


def build_conversion_prerequisite_audit(
    database_path: str | Path,
    *,
    word_com_probe: Callable[[], bool] = _word_com_registered,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Inspect the current controlled-content triple without any DB writes."""
    database = Path(database_path).resolve()
    before_hash = _database_sha256(database)
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        rows = connection.execute(
            """SELECT s.id AS segment_id, s.content_type, src.id AS source_id,
                      src.original_path, src.original_sha256,
                      src.archive_path, src.archive_sha256,
                      src.converted_path, src.converted_sha256
                 FROM current_controlled_content_segments s
                 JOIN controlled_content_sources src ON src.id=s.source_id
                WHERE s.textbook_id=? AND s.curriculum_node_id=?
                ORDER BY s.content_type, s.id""",
            (TARGET_TEXTBOOK_ID, TARGET_NODE_ID),
        ).fetchall()
    finally:
        connection.close()
    after_hash = _database_sha256(database)
    sources: list[dict[str, Any]] = []
    for row in rows:
        original = _file_probe(row["original_path"], row["original_sha256"])
        archive = _file_probe(row["archive_path"], row["archive_sha256"])
        converted = _file_probe(row["converted_path"], row["converted_sha256"])
        sources.append(
            {
                "segment_id": row["segment_id"],
                "content_type": row["content_type"],
                "source_id": row["source_id"],
                "original": original,
                "archive": archive,
                "converted": converted,
                "triple_current": all(
                    item["readable"] and item["matches_expected"]
                    for item in (original, archive, converted)
                ),
            }
        )
    word_com_registered = bool(word_com_probe())
    pandoc = _pandoc_input_formats(command_runner)
    chain_current = bool(sources) and all(item["triple_current"] for item in sources)
    blockers: list[str] = []
    recovery_blockers: list[str] = []
    if len(sources) != 2:
        blockers.append("target_controlled_content_segment_count_invalid")
    for source in sources:
        if not source["original"]["readable"] or not source["original"]["matches_expected"]:
            blockers.append(f"original_not_current:{source['source_id']}")
        if not source["archive"]["readable"] or not source["archive"]["matches_expected"]:
            blockers.append(f"archive_not_current:{source['source_id']}")
        if not source["converted"]["readable"] or not source["converted"]["matches_expected"]:
            blockers.append(f"converted_not_current:{source['source_id']}")
    if not word_com_registered:
        recovery_blockers.append("microsoft_word_com_not_registered")
    if pandoc.get("available") and not pandoc.get("supports_doc"):
        recovery_blockers.append("pandoc_cannot_replace_word_com_for_legacy_doc_fidelity")
    status = "ready" if chain_current else "blocked"
    return {
        "schema": SCHEMA,
        "purpose": "read_only_conversion_prerequisite_audit",
        "database": {
            "path": str(database),
            "sha256_before": before_hash,
            "sha256_after": after_hash,
            "unchanged": before_hash == after_hash,
        },
        "target": {
            "textbook_id": TARGET_TEXTBOOK_ID,
            "curriculum_node_id": TARGET_NODE_ID,
        },
        "controlled_content_chain_current": chain_current,
        "word_com": {
            "required_converter": "Microsoft Word COM",
            "registered": word_com_registered,
        },
        "pandoc": pandoc,
        "sources": sources,
        "status": status,
        "blockers": sorted(set(blockers)),
        "recovery_blockers": sorted(set(recovery_blockers)),
        "recovery_boundary": {
            "allowed_after_word_com_restored": [
                "rerun_p1_2b_controlled_content_validation",
                "create_append_only_controlled_content_source_revision",
                "rerun_p1_3c_q013_mapping_approval",
            ],
            "forbidden": [
                "manual_hash_update",
                "manual_converted_path_update",
                "pandoc_or_text_only_conversion_substitution",
                "question_or_document_approval",
            ],
        },
    }


def write_conversion_prerequisite_audit(report: dict[str, Any], path: str | Path) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination
