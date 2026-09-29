"""Append-only registration of trusted local teaching-content source files."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Iterable, Sequence

SCHEMA_VERSION = "content-source-registration-v1"
IMPORTER_ID = "content-source-registration"
IMPORTER_VERSION = "1.1.0"
FILE_TYPE_BY_EXTENSION = {
    ".doc": "doc", ".docx": "docx", ".pdf": "pdf", ".zip": "zip",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".bmp": "image",
}


class ContentSourceRegistrationError(RuntimeError):
    """Raised if an incremental source registration request is unsafe."""


@dataclass(frozen=True)
class SourceRegistrationResult:
    import_run_id: str
    mode: str
    requested_files: int
    registered_source_records: int
    existing_source_records: int
    registered_versions: int
    existing_versions: int
    unsupported_files: int
    source_documents_created: int
    source_heads_advanced: int
    change_events_created: int

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA_VERSION,
            "import_run_id": self.import_run_id,
            "mode": self.mode,
            "requested_files": self.requested_files,
            "registered_source_records": self.registered_source_records,
            "existing_source_records": self.existing_source_records,
            "registered_versions": self.registered_versions,
            "existing_versions": self.existing_versions,
            "unsupported_files": self.unsupported_files,
            "source_documents_created": self.source_documents_created,
            "source_heads_advanced": self.source_heads_advanced,
            "change_events_created": self.change_events_created,
        }


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest().upper()


def digest_text(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def digest_file(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest().upper()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _git_revision(workspace: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=10, check=False,
        )
    except OSError:
        return None
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and len(value) == 40 else None


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}:{digest_text(canonical(value))}"


def _event_id(*, event_type: str, entity_type: str, entity_id: str, operation: str, after_hash: str | None, transaction_id: str) -> str:
    return _stable_id("event", {
        "event_type": event_type, "entity_type": entity_type, "entity_id": entity_id,
        "operation": operation, "after_hash": after_hash, "transaction_id": transaction_id,
    })


def _insert_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    operation: str,
    reason: str,
    actor: str,
    import_run_id: str | None,
    transaction_id: str,
    before_state: object | None = None,
    after_state: object | None = None,
    before_hash: str | None = None,
    after_hash: str | None = None,
    git_revision: str | None = None,
    tool_id: str = IMPORTER_ID,
    tool_version: str = IMPORTER_VERSION,
) -> tuple[str, bool]:
    event_id = _event_id(
        event_type=event_type, entity_type=entity_type, entity_id=entity_id,
        operation=operation, after_hash=after_hash, transaction_id=transaction_id,
    )
    before_json = canonical(before_state) if before_state is not None else None
    after_json = canonical(after_state) if after_state is not None else None
    cursor = connection.execute(
        """INSERT OR IGNORE INTO content_change_ledger(
            id,event_type,entity_type,entity_id,operation,before_state_json,after_state_json,
            before_hash,after_hash,reason,actor,tool_id,tool_version,import_run_id,transaction_id,git_revision
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, event_type, entity_type, entity_id, operation, before_json, after_json,
            before_hash, after_hash, reason, actor, tool_id, tool_version,
            import_run_id, transaction_id, git_revision,
        ),
    )
    if cursor.rowcount:
        return event_id, True
    # Runtime provenance belongs to the first successful write and must not make a
    # logically identical retry collide.  Keep it out of the idempotency comparison
    # and never update the existing append-only ledger row.
    existing = connection.execute(
        """SELECT event_type,entity_type,entity_id,operation,before_state_json,after_state_json,
                  before_hash,after_hash,reason,import_run_id,transaction_id
             FROM content_change_ledger WHERE id=?""", (event_id,)
    ).fetchone()
    expected = (
        event_type, entity_type, entity_id, operation, before_json, after_json, before_hash,
        after_hash, reason, import_run_id, transaction_id,
    )
    if existing is None or tuple(existing) != expected:
        raise ContentSourceRegistrationError("change_ledger_id_collision")
    return event_id, False


def list_baseline_files(source_root: str | Path) -> tuple[Path, ...]:
    root = Path(source_root).resolve(strict=True)
    if not root.is_dir():
        raise ContentSourceRegistrationError("source_root_not_directory")
    return tuple(sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: path.as_posix().casefold()))


def _normalize_explicit_paths(source_root: Path, paths: Iterable[str | Path]) -> tuple[Path, ...]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    for value in paths:
        candidate = Path(value)
        full = (source_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if not full.is_file() or not _inside(full, source_root):
            raise ContentSourceRegistrationError("explicit_source_path_invalid")
        if full not in seen:
            seen.add(full)
            resolved.append(full)
    if not resolved:
        raise ContentSourceRegistrationError("explicit_source_paths_required")
    return tuple(sorted(resolved, key=lambda path: path.as_posix().casefold()))


def _source_document_id(relative_path: str) -> str:
    return _stable_id("source-document", {"relative_path": relative_path})


def _source_version_id(source_document_id: str, file_hash: str) -> str:
    return _stable_id("source-version", {"source_document_id": source_document_id, "sha256": file_hash})


def _ensure_run(
    connection: sqlite3.Connection,
    *,
    mode: str,
    relative_paths: Sequence[str],
    workspace: Path,
    actor: str,
) -> tuple[str, int]:
    if mode not in {"baseline", "delta", "extract", "retry"}:
        raise ContentSourceRegistrationError("import_mode_invalid")
    selection = {"schema": SCHEMA_VERSION, "mode": mode, "relative_paths": list(relative_paths)}
    selection_hash = digest_text(canonical(selection))
    run_id = _stable_id("content-import-run", {
        "mode": mode, "selection_sha256": selection_hash,
        "importer_id": IMPORTER_ID, "importer_version": IMPORTER_VERSION,
    })
    transaction_id = _stable_id("transaction", {"run_id": run_id, "operation": "registration"})
    revision = _git_revision(workspace)
    event_id, event_created = _insert_event(
        connection, event_type="import_run_started", entity_type="content_import_runs", entity_id=run_id,
        operation="create", reason="create explicit source registration batch", actor=actor,
        import_run_id=None, transaction_id=transaction_id, after_state=selection,
        after_hash=selection_hash, git_revision=revision,
    )
    connection.execute(
        """INSERT OR IGNORE INTO content_import_runs(
            id,mode,selection_json,selection_sha256,importer_id,importer_version,created_change_event_id
        ) VALUES(?,?,?,?,?,?,?)""",
        (run_id, mode, canonical(selection), selection_hash, IMPORTER_ID, IMPORTER_VERSION, event_id),
    )
    started_event_id, started_created = _insert_event(
        connection, event_type="import_run_started", entity_type="content_import_run_events",
        entity_id=f"{run_id}:started", operation="status_change", reason="start explicit source registration batch",
        actor=actor, import_run_id=run_id, transaction_id=transaction_id,
        after_state={"status": "started"}, git_revision=revision,
    )
    connection.execute(
        """INSERT OR IGNORE INTO content_import_run_events(
            id,import_run_id,status,totals_json,change_event_id
        ) VALUES(?,?,?,?,?)""",
        (f"{run_id}:started", run_id, "started", "{}", started_event_id),
    )
    return run_id, int(event_created) + int(started_created)


def _intake_record_id(relative_path: str, file_hash: str) -> str:
    return _stable_id("source-intake-record", {"relative_path": relative_path, "sha256": file_hash})


def register_sources(
    connection: sqlite3.Connection,
    *,
    source_root: str | Path,
    mode: str,
    explicit_paths: Iterable[str | Path] | None,
    workspace: str | Path,
    actor: str = "codex",
) -> SourceRegistrationResult:
    """Register a baseline or explicit delta batch without any implicit rescan.

    `mode='baseline'` is the sole caller-authorized whole-root traversal. Every
    file receives a generic, immutable inventory record, even where the legacy
    `source_documents` table cannot represent the file type (for example ZIP).
    Later modes require explicit paths and never traverse the complete root.
    """
    root = Path(source_root).resolve(strict=True)
    work = Path(workspace).resolve(strict=True)
    if not root.is_dir() or not work.is_dir():
        raise ContentSourceRegistrationError("registration_root_invalid")
    if mode == "baseline":
        if explicit_paths is not None:
            raise ContentSourceRegistrationError("baseline_does_not_accept_explicit_paths")
        files = list_baseline_files(root)
    else:
        if explicit_paths is None:
            raise ContentSourceRegistrationError("incremental_paths_required")
        files = _normalize_explicit_paths(root, explicit_paths)
    relative_paths = tuple(path.relative_to(root).as_posix() for path in files)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.row_factory = None
    records_registered = records_existing = registered = existing = unsupported = documents_created = heads_advanced = change_events = 0
    with connection:
        run_id, created = _ensure_run(
            connection, mode=mode, relative_paths=relative_paths, workspace=work, actor=actor,
        )
        change_events += created
        transaction_id = _stable_id("transaction", {"run_id": run_id, "operation": "registration"})
        revision = _git_revision(work)
        for path, relative_path in zip(files, relative_paths):
            detected_type = FILE_TYPE_BY_EXTENSION.get(path.suffix.lower(), "other")
            source_supported = detected_type in {"doc", "docx", "pdf", "image"}
            file_hash = digest_file(path)
            file_size = path.stat().st_size
            intake_id = _intake_record_id(relative_path, file_hash)
            intake_row = connection.execute(
                "SELECT id FROM content_source_intake_records WHERE id=?", (intake_id,)
            ).fetchone()
            document_id: str | None = None
            if source_supported:
                document_id = _source_document_id(relative_path)
                doc_before = connection.execute("SELECT id FROM source_documents WHERE id=?", (document_id,)).fetchone()
                if doc_before is None:
                    connection.execute(
                        """INSERT INTO source_documents(
                            id,relative_path,file_hash,file_type,source_label,copyright_status,parse_status,
                            original_relative_path,original_file_hash,trusted_source,intake_manifest_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            document_id, relative_path, file_hash, detected_type, "trusted-local-source",
                            "authorized", "pending", relative_path, file_hash, 1,
                            canonical({"schema": SCHEMA_VERSION, "first_registration_run_id": run_id}),
                        ),
                    )
                    documents_created += 1
                elif connection.execute(
                    "SELECT relative_path FROM source_documents WHERE id=?", (document_id,)
                ).fetchone()[0] != relative_path:
                    raise ContentSourceRegistrationError("source_document_path_identity_collision")
            if intake_row is None:
                intake_event_id, intake_event_created = _insert_event(
                    connection, event_type="source_registered", entity_type="content_source_intake_records",
                    entity_id=intake_id, operation="create", reason="register trusted source file inventory record",
                    actor=actor, import_run_id=run_id, transaction_id=transaction_id,
                    after_state={
                        "relative_path": relative_path, "detected_file_type": detected_type,
                        "file_size_bytes": file_size, "supported_for_extraction": source_supported,
                    }, after_hash=file_hash, git_revision=revision,
                )
                change_events += int(intake_event_created)
                connection.execute(
                    """INSERT INTO content_source_intake_records(
                        id,original_relative_path,original_sha256,file_size_bytes,detected_file_type,
                        supported_for_extraction,source_document_id,registered_import_run_id,created_change_event_id
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (intake_id, relative_path, file_hash, file_size, detected_type, int(source_supported),
                     document_id, run_id, intake_event_id),
                )
                intake_lifecycle_id = _stable_id("source-intake-lifecycle", {
                    "intake_record_id": intake_id, "status": "registered" if source_supported else "unsupported",
                })
                intake_state_event_id, intake_state_created = _insert_event(
                    connection, event_type="source_status_changed", entity_type="content_source_intake_lifecycle_events",
                    entity_id=intake_lifecycle_id, operation="status_change",
                    reason="source file registered for extraction" if source_supported else "file type is not yet supported for extraction",
                    actor=actor, import_run_id=run_id, transaction_id=transaction_id,
                    after_state={
                        "status": "registered" if source_supported else "unsupported",
                        "retry_eligible": source_supported,
                    }, after_hash=file_hash, git_revision=revision,
                )
                change_events += int(intake_state_created)
                connection.execute(
                    """INSERT INTO content_source_intake_lifecycle_events(
                        id,intake_record_id,status,reason,retry_eligible,change_event_id
                    ) VALUES(?,?,?,?,?,?)""",
                    (intake_lifecycle_id, intake_id, "registered" if source_supported else "unsupported",
                     "source file registered for extraction" if source_supported else "file type is not yet supported for extraction",
                     int(source_supported), intake_state_event_id),
                )
                records_registered += 1
            else:
                records_existing += 1
            if not source_supported:
                unsupported += 1
                continue
            assert document_id is not None
            version_id = _source_version_id(document_id, file_hash)
            row = connection.execute("SELECT id FROM content_source_versions WHERE id=?", (version_id,)).fetchone()
            if row is None:
                event_id, event_created = _insert_event(
                    connection, event_type="source_registered", entity_type="content_source_versions",
                    entity_id=version_id, operation="create", reason="register trusted extractable source file revision",
                    actor=actor, import_run_id=run_id, transaction_id=transaction_id,
                    after_state={"relative_path": relative_path, "file_type": detected_type, "file_size_bytes": file_size},
                    after_hash=file_hash, git_revision=revision,
                )
                change_events += int(event_created)
                connection.execute(
                    """INSERT INTO content_source_versions(
                        id,source_document_id,original_relative_path,original_sha256,file_size_bytes,file_type,
                        trusted_source,registered_import_run_id,created_change_event_id,intake_record_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (version_id, document_id, relative_path, file_hash, file_size, detected_type, 1, run_id, event_id, intake_id),
                )
                lifecycle_id = _stable_id("source-lifecycle", {"source_version_id": version_id, "status": "registered"})
                state_event_id, state_event_created = _insert_event(
                    connection, event_type="source_status_changed", entity_type="content_source_lifecycle_events",
                    entity_id=lifecycle_id, operation="status_change", reason="extractable source revision registered",
                    actor=actor, import_run_id=run_id, transaction_id=transaction_id,
                    after_state={"status": "registered", "retry_eligible": True}, after_hash=file_hash,
                    git_revision=revision,
                )
                change_events += int(state_event_created)
                connection.execute(
                    """INSERT OR IGNORE INTO content_source_lifecycle_events(
                        id,source_version_id,status,reason,retry_eligible,change_event_id
                    ) VALUES(?,?,?,?,?,?)""",
                    (lifecycle_id, version_id, "registered", "extractable source revision registered", 1, state_event_id),
                )
                registered += 1
            else:
                existing += 1
            head = connection.execute(
                "SELECT current_source_version_id FROM content_source_version_heads WHERE source_document_id=?",
                (document_id,),
            ).fetchone()
            if head is None or head[0] != version_id:
                head_event_id, head_event_created = _insert_event(
                    connection, event_type="source_superseded" if head else "source_registered",
                    entity_type="content_source_version_heads", entity_id=document_id,
                    operation="supersede" if head else "create",
                    reason="advance current extractable source revision head", actor=actor, import_run_id=run_id,
                    transaction_id=transaction_id,
                    before_state={"current_source_version_id": head[0]} if head else None,
                    after_state={"current_source_version_id": version_id}, after_hash=file_hash,
                    git_revision=revision,
                )
                change_events += int(head_event_created)
                connection.execute(
                    """INSERT INTO content_source_version_heads(
                        source_document_id,current_source_version_id,change_event_id
                    ) VALUES(?,?,?)
                    ON CONFLICT(source_document_id) DO UPDATE SET
                        current_source_version_id=excluded.current_source_version_id,
                        change_event_id=excluded.change_event_id,
                        updated_at=CURRENT_TIMESTAMP""",
                    (document_id, version_id, head_event_id),
                )
                heads_advanced += 1
        totals = {
            "requested_files": len(files), "registered_source_records": records_registered,
            "existing_source_records": records_existing, "registered_versions": registered,
            "existing_versions": existing, "unsupported_files": unsupported,
            "source_documents_created": documents_created, "source_heads_advanced": heads_advanced,
        }
        complete_row_id = f"{run_id}:completed"
        complete_event_id, complete_event_created = _insert_event(
            connection, event_type="import_run_finished", entity_type="content_import_run_events",
            entity_id=complete_row_id, operation="status_change", reason="complete explicit source registration batch",
            actor=actor, import_run_id=run_id, transaction_id=transaction_id,
            after_state={"status": "completed", "totals": totals}, after_hash=digest_text(canonical(totals)),
            git_revision=revision,
        )
        change_events += int(complete_event_created)
        connection.execute(
            """INSERT OR IGNORE INTO content_import_run_events(
                id,import_run_id,status,totals_json,change_event_id
            ) VALUES(?,?,?,?,?)""",
            (complete_row_id, run_id, "completed", canonical(totals), complete_event_id),
        )
    return SourceRegistrationResult(
        import_run_id=run_id, mode=mode, requested_files=len(files),
        registered_source_records=records_registered, existing_source_records=records_existing,
        registered_versions=registered, existing_versions=existing, unsupported_files=unsupported,
        source_documents_created=documents_created, source_heads_advanced=heads_advanced,
        change_events_created=change_events,
    )
