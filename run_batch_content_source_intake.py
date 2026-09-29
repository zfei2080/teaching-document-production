"""Run the approved local source-intake pipeline for explicitly selected Word files.

This CLI deliberately orchestrates existing registration, read-only Word COM extraction,
and candidate import modules.  It does not create questions, change validation rules, or
promote blocked candidates.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import sys
from typing import Iterable, Sequence

from content_library_extraction import import_current_word_source
from content_question_candidate_import import import_question_candidates
from source_library_intake import ContentSourceRegistrationError, digest_file, register_sources

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = ROOT / "<local-scratch>"
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
SUPPORTED_WORD_EXTENSIONS = frozenset({".doc", ".docx"})
REPORT_SCHEMA = "batch-content-source-intake-v1"


class BatchContentSourceIntakeError(RuntimeError):
    """Raised before intake when an explicit request is unsafe or incomplete."""


class FilePipelineStageError(RuntimeError):
    """Preserves the failed pipeline stage while retaining the original exception."""

    def __init__(self, stage: str, cause: Exception) -> None:
        self.stage = stage
        self.cause = cause
        super().__init__(str(cause))


@dataclass(frozen=True)
class SourceVersion:
    source_version_id: str | None
    file_type: str | None
    intake_registered_in_current_run: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Register explicitly authorized local source files, extract supported Word "
            "documents read-only through Word COM, and import source-derived question candidates."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument(
        "--file", action="append", dest="files", metavar="RELATIVE_PATH",
        help="One path relative to --source-root; repeatable.",
    )
    mode.add_argument(
        "--file-list", type=Path, metavar="UTF8_TEXT_FILE",
        help="UTF-8 text file containing one relative source path per line.",
    )
    mode.add_argument(
        "--all", action="store_true",
        help="Request the existing explicit whole-root baseline registration; requires --confirm-all.",
    )
    parser.add_argument(
        "--confirm-all", action="store_true",
        help="Second explicit confirmation required with --all.",
    )
    parser.add_argument(
        "--continue-on-error", action="store_true",
        help="Continue with remaining selected files after a per-file pipeline failure.",
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--actor", default="codex")
    parser.add_argument(
        "--report", type=Path,
        help="Optional output path for the complete JSON report; no report file is created by default.",
    )
    return parser


def _safe_relative_path(value: str | Path, *, source_root: Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or candidate.drive:
        raise BatchContentSourceIntakeError("source_path_must_be_relative")
    resolved = (source_root / candidate).resolve()
    try:
        relative = resolved.relative_to(source_root)
    except ValueError as exc:
        raise BatchContentSourceIntakeError("source_path_escapes_source_root") from exc
    if not resolved.is_file():
        raise BatchContentSourceIntakeError("source_file_missing")
    return relative


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = path.as_posix().casefold()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return tuple(result)


def _read_file_list(file_list: Path, *, source_root: Path) -> tuple[Path, ...]:
    try:
        content = file_list.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise BatchContentSourceIntakeError("file_list_missing") from exc
    except UnicodeDecodeError as exc:
        raise BatchContentSourceIntakeError("file_list_must_be_utf8") from exc
    except OSError as exc:
        raise BatchContentSourceIntakeError(f"file_list_unreadable: {exc}") from exc
    values = [line.strip() for line in content.splitlines()]
    selected = [
        _safe_relative_path(value, source_root=source_root)
        for value in values
        if value and not value.startswith("#")
    ]
    if not selected:
        raise BatchContentSourceIntakeError("file_list_has_no_source_paths")
    return _unique_paths(selected)


def _validate_roots(source_root: Path, database: Path) -> tuple[Path, Path]:
    try:
        root = source_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BatchContentSourceIntakeError("source_root_missing") from exc
    if not root.is_dir():
        raise BatchContentSourceIntakeError("source_root_not_directory")
    try:
        db = database.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BatchContentSourceIntakeError("database_missing") from exc
    if not db.is_file():
        raise BatchContentSourceIntakeError("database_not_file")
    return root, db


def _selection(arguments: argparse.Namespace, *, source_root: Path) -> tuple[str, tuple[Path, ...] | None]:
    if arguments.all:
        if not arguments.confirm_all:
            raise BatchContentSourceIntakeError("--all requires --confirm-all; no source directory scan was performed")
        return "all", None
    if arguments.confirm_all:
        raise BatchContentSourceIntakeError("--confirm-all is only valid with --all")
    if arguments.files:
        return "files", _unique_paths(
            _safe_relative_path(value, source_root=source_root) for value in arguments.files
        )
    if arguments.file_list is not None:
        return "file_list", _read_file_list(arguments.file_list, source_root=source_root)
    raise BatchContentSourceIntakeError("one of --file, --file-list, or --all --confirm-all is required")


def _relative_paths_from_registration(connection: sqlite3.Connection, import_run_id: str) -> tuple[Path, ...]:
    row = connection.execute(
        "SELECT selection_json FROM content_import_runs WHERE id=?", (import_run_id,)
    ).fetchone()
    if row is None:
        raise BatchContentSourceIntakeError("registration_run_selection_missing")
    try:
        payload = json.loads(row[0])
        relative_paths = payload["relative_paths"]
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise BatchContentSourceIntakeError("registration_run_selection_invalid") from exc
    if not isinstance(relative_paths, list) or not all(isinstance(value, str) for value in relative_paths):
        raise BatchContentSourceIntakeError("registration_run_selection_invalid")
    return tuple(Path(value) for value in relative_paths)


def _source_version_for_path(
    connection: sqlite3.Connection,
    *,
    source_root: Path,
    relative_path: Path,
    registration_run_id: str,
) -> SourceVersion:
    full_path = source_root / relative_path
    source_hash = digest_file(full_path)
    intake = connection.execute(
        """SELECT supported_for_extraction, registered_import_run_id
             FROM content_source_intake_records
             WHERE original_relative_path=? AND original_sha256=?""",
        (relative_path.as_posix(), source_hash),
    ).fetchone()
    if intake is None:
        raise BatchContentSourceIntakeError("registered_source_inventory_record_missing")
    row = connection.execute(
        """SELECT v.id, v.file_type, v.registered_import_run_id
             FROM content_source_versions v
             JOIN content_source_version_heads h
               ON h.source_document_id=v.source_document_id
              AND h.current_source_version_id=v.id
             WHERE v.original_relative_path=? AND v.original_sha256=?""",
        (relative_path.as_posix(), source_hash),
    ).fetchone()
    if row is None:
        return SourceVersion(None, None, intake[1] == registration_run_id)
    return SourceVersion(str(row[0]), str(row[1]), intake[1] == registration_run_id)


def _empty_file_result(relative_path: Path) -> dict[str, object]:
    return {
        "relative_path": relative_path.as_posix(),
        "source_version_id": None,
        "registration_status": "not_started",
        "extraction_status": "not_started",
        "import_status": "not_started",
        "structured_questions": 0,
        "math_validation_status_counts": {},
        "content_eligible": 0,
        "blocked": 0,
        "unsupported": False,
        "error": None,
    }


def _mark_failure(result: dict[str, object], *, stage: str, exc: BaseException) -> None:
    result["status"] = "failed"
    result["error"] = {
        "stage": stage,
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "retry_recommended": stage in {"registration", "extraction", "import"},
    }


def _source_summary(connection: sqlite3.Connection, source_version_id: str) -> dict[str, object]:
    math_counts = {
        str(status): int(count)
        for status, count in connection.execute(
            """SELECT evidence.validation_status, COUNT(*)
                 FROM content_item_math_validation_evidence evidence
                 JOIN content_items item ON item.id=evidence.content_item_id
                 WHERE item.source_version_id=?
                 GROUP BY evidence.validation_status
                 ORDER BY evidence.validation_status""",
            (source_version_id,),
        )
    }
    question_counts = connection.execute(
        """SELECT COUNT(*),
                  SUM(CASE WHEN question.quality_status='pending' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN question.quality_status='blocked' THEN 1 ELSE 0 END)
             FROM content_items item
             LEFT JOIN content_item_question_links link ON link.content_item_id=item.id
             LEFT JOIN questions question ON question.id=link.question_id
             WHERE item.source_version_id=?""",
        (source_version_id,),
    ).fetchone()
    return {
        "structured_questions": int(question_counts[0] or 0),
        "math_validation_status_counts": math_counts,
        "content_eligible": int(question_counts[1] or 0),
        "blocked": int(question_counts[2] or 0),
    }


def _foreign_key_check(connection: sqlite3.Connection) -> dict[str, object]:
    violations = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
    return {
        "passed": not violations,
        "violation_count": len(violations),
        "violations": violations[:20],
        "truncated": len(violations) > 20,
    }


def _process_registered_path(
    connection: sqlite3.Connection,
    *,
    source_root: Path,
    relative_path: Path,
    registration_run_id: str,
    workspace: Path,
    manifest_root: Path,
    actor: str,
    result: dict[str, object] | None = None,
) -> dict[str, object]:
    if result is None:
        result = _empty_file_result(relative_path)
    try:
        version = _source_version_for_path(
            connection,
            source_root=source_root,
            relative_path=relative_path,
            registration_run_id=registration_run_id,
        )
    except Exception as exc:
        raise FilePipelineStageError("extraction", exc) from exc
    result["registration_status"] = "registered" if version.intake_registered_in_current_run else "reused"
    result["source_version_id"] = version.source_version_id
    if relative_path.suffix.lower() not in SUPPORTED_WORD_EXTENSIONS:
        result.update({
            "status": "unsupported",
            "unsupported": True,
            "extraction_status": "not_applicable",
            "import_status": "not_applicable",
            "error": {
                "stage": "extraction",
                "exception_type": "UnsupportedSourceFileType",
                "message": "Only .doc and .docx are supported by this Word COM intake pipeline.",
                "retry_recommended": False,
            },
        })
        return result
    if version.source_version_id is None:
        raise BatchContentSourceIntakeError("extractable_word_source_version_missing")
    try:
        extraction = import_current_word_source(
            connection,
            source_version_id=version.source_version_id,
            source_root=source_root,
            workspace=workspace,
            manifest_root=manifest_root,
            actor=actor,
        )
    except Exception as exc:
        raise FilePipelineStageError("extraction", exc) from exc
    result["extraction_status"] = extraction.status
    try:
        imported = import_question_candidates(
            connection,
            source_version_id=version.source_version_id,
            source_root=source_root,
            workspace=workspace,
            actor=actor,
        )
    except Exception as exc:
        raise FilePipelineStageError("import", exc) from exc
    result["import_status"] = imported.status
    try:
        result.update(_source_summary(connection, version.source_version_id))
    except Exception as exc:
        raise FilePipelineStageError("verification", exc) from exc
    result["status"] = "completed"
    return result


def _process_explicit_paths(
    connection: sqlite3.Connection,
    *,
    paths: Sequence[Path],
    source_root: Path,
    workspace: Path,
    manifest_root: Path,
    actor: str,
    continue_on_error: bool,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for path in paths:
        result = _empty_file_result(path)
        try:
            registration = register_sources(
                connection,
                source_root=source_root,
                mode="delta",
                explicit_paths=[path.as_posix()],
                workspace=workspace,
                actor=actor,
            )
            result = _process_registered_path(
                connection,
                source_root=source_root,
                relative_path=path,
                registration_run_id=registration.import_run_id,
                workspace=workspace,
                manifest_root=manifest_root,
                actor=actor,
                result=result,
            )
        except Exception as exc:  # Preserve exact failed stage in the audit result.
            if isinstance(exc, FilePipelineStageError):
                stage, original = exc.stage, exc.cause
            else:
                stage, original = "registration", exc
            _mark_failure(result, stage=stage, exc=original)
            results.append(result)
            if not continue_on_error:
                break
        else:
            results.append(result)
    return results


def _process_all_paths(
    connection: sqlite3.Connection,
    *,
    source_root: Path,
    workspace: Path,
    manifest_root: Path,
    actor: str,
    continue_on_error: bool,
) -> tuple[list[dict[str, object]], str | None]:
    registration = register_sources(
        connection,
        source_root=source_root,
        mode="baseline",
        explicit_paths=None,
        workspace=workspace,
        actor=actor,
    )
    paths = _relative_paths_from_registration(connection, registration.import_run_id)
    results: list[dict[str, object]] = []
    for path in paths:
        result = _empty_file_result(path)
        try:
            result = _process_registered_path(
                connection,
                source_root=source_root,
                relative_path=path,
                registration_run_id=registration.import_run_id,
                workspace=workspace,
                manifest_root=manifest_root,
                actor=actor,
                result=result,
            )
        except Exception as exc:
            if isinstance(exc, FilePipelineStageError):
                stage, original = exc.stage, exc.cause
            else:
                stage, original = "extraction", exc
            _mark_failure(result, stage=stage, exc=original)
            results.append(result)
            if not continue_on_error:
                break
        else:
            results.append(result)
    return results, registration.import_run_id


def _batch_summary(results: Sequence[dict[str, object]], *, requested_files: int) -> dict[str, object]:
    math_counts: Counter[str] = Counter()
    source_version_ids: list[str] = []
    new_versions = reused_versions = 0
    for result in results:
        math_counts.update(result["math_validation_status_counts"])
        version = result["source_version_id"]
        if isinstance(version, str):
            source_version_ids.append(version)
            if result["registration_status"] == "registered":
                new_versions += 1
            elif result["registration_status"] == "reused":
                reused_versions += 1
    successful = sum(result.get("status") == "completed" for result in results)
    unsupported = sum(result.get("status") == "unsupported" for result in results)
    failed = sum(result.get("status") == "failed" for result in results)
    return {
        "requested_files": requested_files,
        "processed_files": len(results),
        "successful_files": successful,
        "failed_files": failed,
        "unsupported_files": unsupported,
        "source_versions_new": new_versions,
        "source_versions_reused": reused_versions,
        "source_versions_new_or_reused": new_versions + reused_versions,
        "structured_questions": sum(int(result["structured_questions"]) for result in results),
        "math_validation_status_counts": dict(sorted(math_counts.items())),
        "content_eligible": sum(int(result["content_eligible"]) for result in results),
        "blocked": sum(int(result["blocked"]) for result in results),
        "source_version_ids": source_version_ids,
    }


def _summary_text(summary: dict[str, object], foreign_key_check: dict[str, object]) -> str:
    return (
        "Batch intake: requested={requested_files}, processed={processed_files}, "
        "successful={successful_files}, failed={failed_files}, unsupported={unsupported_files}; "
        "structured_questions={structured_questions}, content_eligible={content_eligible}, "
        "blocked={blocked}; foreign_key_check={foreign_key}."
    ).format(**summary, foreign_key="passed" if foreign_key_check["passed"] else "failed")


def run(arguments: argparse.Namespace) -> tuple[dict[str, object], int]:
    source_root, database = _validate_roots(arguments.source_root, arguments.database)
    request_mode, explicit_paths = _selection(arguments, source_root=source_root)
    manifest_root = database.parent / "content-extraction-manifests"
    results: list[dict[str, object]] = []
    registration_run_id: str | None = None
    fatal_error: dict[str, object] | None = None
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if request_mode == "all":
            try:
                results, registration_run_id = _process_all_paths(
                    connection,
                    source_root=source_root,
                    workspace=ROOT,
                    manifest_root=manifest_root,
                    actor=arguments.actor,
                    continue_on_error=arguments.continue_on_error,
                )
                requested_files = len(_relative_paths_from_registration(connection, registration_run_id))
            except Exception as exc:
                requested_files = 0
                fatal_error = {
                    "stage": "registration",
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                    "retry_recommended": True,
                }
        else:
            assert explicit_paths is not None
            results = _process_explicit_paths(
                connection,
                paths=explicit_paths,
                source_root=source_root,
                workspace=ROOT,
                manifest_root=manifest_root,
                actor=arguments.actor,
                continue_on_error=arguments.continue_on_error,
            )
            requested_files = len(explicit_paths)
        foreign_key_check = _foreign_key_check(connection)
    finally:
        connection.close()
    summary = _batch_summary(results, requested_files=requested_files)
    report = {
        "schema": REPORT_SCHEMA,
        "request": {
            "mode": request_mode,
            "source_root": str(source_root),
            "database": str(database),
            "actor": arguments.actor,
            "continue_on_error": bool(arguments.continue_on_error),
            "registration_import_run_id": registration_run_id,
        },
        "files": results,
        "summary": {**summary, "foreign_key_check": foreign_key_check},
        "fatal_error": fatal_error,
        "text_summary": _summary_text(summary, foreign_key_check),
    }
    failed = summary["failed_files"] > 0 or fatal_error is not None or not foreign_key_check["passed"]
    return report, 1 if failed else 0


def _failure_report(arguments: argparse.Namespace, exc: BaseException) -> dict[str, object]:
    message = str(exc)
    return {
        "schema": REPORT_SCHEMA,
        "request": {"actor": arguments.actor},
        "files": [],
        "summary": {
            "requested_files": 0,
            "processed_files": 0,
            "successful_files": 0,
            "failed_files": 0,
            "unsupported_files": 0,
            "source_versions_new": 0,
            "source_versions_reused": 0,
            "source_versions_new_or_reused": 0,
            "structured_questions": 0,
            "math_validation_status_counts": {},
            "content_eligible": 0,
            "blocked": 0,
            "source_version_ids": [],
            "foreign_key_check": None,
        },
        "fatal_error": {
            "stage": "preflight",
            "exception_type": type(exc).__name__,
            "message": message,
            "retry_recommended": False,
        },
        "text_summary": f"Batch intake was refused before processing: {message}",
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        report, exit_code = run(arguments)
    except (BatchContentSourceIntakeError, ContentSourceRegistrationError, OSError, sqlite3.Error) as exc:
        report = _failure_report(arguments, exc)
        exit_code = 2
    if arguments.report is not None:
        _write_report(arguments.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
