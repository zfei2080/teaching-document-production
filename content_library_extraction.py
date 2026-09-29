"""Word COM source-block import into the append-only teaching-content database."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
from typing import Any

from source_library_intake import (
    _git_revision,
    _insert_event,
    _stable_id,
    canonical,
    digest_file,
    digest_text,
)
from word_com_content_extractor import extract_document as _extract_document_com
from docx_content_extractor import extract_document as _extract_document_docx


def extract_document(path: str | Path) -> dict[str, Any]:
    """Dispatch read-only extraction: ``.docx`` via pure Python, ``.doc`` via Word COM."""
    source = Path(path)
    if source.suffix.lower() == ".docx":
        return _extract_document_docx(source)
    return _extract_document_com(source)

SCHEMA = "content-library-word-extraction-import-v1"
IMPORTER_ID = "content-library-word-extraction"
IMPORTER_VERSION = "1.0.0"


class ContentLibraryExtractionError(RuntimeError):
    """Raised when a source block extraction is not safe to persist."""


@dataclass(frozen=True)
class ExtractionImportResult:
    source_version_id: str
    import_run_id: str
    extraction_run_id: str
    status: str
    source_blocks: int
    source_assets: int
    change_events_created: int

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "source_version_id": self.source_version_id,
            "import_run_id": self.import_run_id,
            "extraction_run_id": self.extraction_run_id,
            "status": self.status,
            "source_blocks": self.source_blocks,
            "source_assets": self.source_assets,
            "change_events_created": self.change_events_created,
        }


def _require_current_source_version(
    connection: sqlite3.Connection, *, source_version_id: str, source_root: Path
) -> tuple[Path, sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """SELECT v.id, v.original_relative_path, v.original_sha256, v.file_type,
                  d.id AS source_document_id
             FROM content_source_versions v
             JOIN content_source_version_heads h
               ON h.source_document_id=v.source_document_id
              AND h.current_source_version_id=v.id
             JOIN source_documents d ON d.id=v.source_document_id
            WHERE v.id=?""",
        (source_version_id,),
    ).fetchone()
    if row is None:
        raise ContentLibraryExtractionError("source_version_is_not_current_extractable_head")
    path = (source_root / row["original_relative_path"]).resolve()
    try:
        path.relative_to(source_root.resolve())
    except ValueError as exc:
        raise ContentLibraryExtractionError("source_path_escapes_root") from exc
    if not path.is_file():
        raise ContentLibraryExtractionError("source_file_missing")
    if digest_file(path) != row["original_sha256"]:
        raise ContentLibraryExtractionError("source_hash_drift_before_word_extraction")
    if path.suffix.lower().lstrip(".") != row["file_type"]:
        raise ContentLibraryExtractionError("source_file_type_drift_before_word_extraction")
    return path, row


def _existing_result(connection: sqlite3.Connection, *, source_version_id: str) -> ExtractionImportResult | None:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """SELECT er.id AS extraction_run_id, er.import_run_id,
                  (SELECT COUNT(*) FROM source_content_blocks b WHERE b.extraction_run_id=er.id) AS source_blocks,
                  (SELECT COUNT(*) FROM source_content_assets a WHERE a.extraction_run_id=er.id) AS source_assets
             FROM content_extraction_runs er
             JOIN content_import_runs ir ON ir.id=er.import_run_id
            WHERE er.source_version_id=? AND ir.importer_id=? AND ir.importer_version=?
            ORDER BY er.created_at DESC, er.id DESC LIMIT 1""",
        (source_version_id, IMPORTER_ID, IMPORTER_VERSION),
    ).fetchone()
    if row is None:
        return None
    return ExtractionImportResult(
        source_version_id=source_version_id, import_run_id=row["import_run_id"],
        extraction_run_id=row["extraction_run_id"], status="already_extracted",
        source_blocks=row["source_blocks"], source_assets=row["source_assets"], change_events_created=0,
    )


def _write_manifest(manifest: dict[str, Any], *, manifest_root: Path, source_version_id: str) -> tuple[Path, str]:
    payload = canonical(manifest)
    digest = digest_text(payload)
    target = manifest_root / f"{source_version_id.replace(':', '_')}.{digest}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_text(encoding="utf-8") != payload:
            raise ContentLibraryExtractionError("extraction_manifest_path_hash_collision")
    else:
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        temporary.replace(target)
    return target, digest


def _run_id(source_version_id: str) -> str:
    return _stable_id("content-import-run", {
        "mode": "extract", "source_version_id": source_version_id,
        "importer_id": IMPORTER_ID, "importer_version": IMPORTER_VERSION,
    })


def _create_run(
    connection: sqlite3.Connection, *, source_version_id: str, workspace: Path, actor: str
) -> tuple[str, str, int]:
    run_id = _run_id(source_version_id)
    selection = {"schema": SCHEMA, "source_version_ids": [source_version_id]}
    selection_hash = digest_text(canonical(selection))
    transaction_id = _stable_id("transaction", {"run_id": run_id, "operation": "word_extraction"})
    revision = _git_revision(workspace)
    event_id, created = _insert_event(
        connection, event_type="import_run_started", entity_type="content_import_runs", entity_id=run_id,
        operation="create", reason="create explicit Word source extraction batch", actor=actor,
        import_run_id=None, transaction_id=transaction_id, after_state=selection, after_hash=selection_hash,
        git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
    )
    connection.execute(
        """INSERT OR IGNORE INTO content_import_runs(
            id,mode,selection_json,selection_sha256,importer_id,importer_version,created_change_event_id
        ) VALUES(?,?,?,?,?,?,?)""",
        (run_id, "extract", canonical(selection), selection_hash, IMPORTER_ID, IMPORTER_VERSION, event_id),
    )
    state_id = f"{run_id}:started"
    state_event_id, state_created = _insert_event(
        connection, event_type="import_run_started", entity_type="content_import_run_events", entity_id=state_id,
        operation="status_change", reason="start explicit Word source extraction batch", actor=actor,
        import_run_id=run_id, transaction_id=transaction_id, after_state={"status": "started"},
        git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
    )
    connection.execute(
        """INSERT OR IGNORE INTO content_import_run_events(
            id,import_run_id,status,totals_json,change_event_id
        ) VALUES(?,?,?,?,?)""",
        (state_id, run_id, "started", "{}", state_event_id),
    )
    return run_id, transaction_id, int(created) + int(state_created)


def import_current_word_source(
    connection: sqlite3.Connection,
    *,
    source_version_id: str,
    source_root: str | Path,
    workspace: str | Path,
    manifest_root: str | Path,
    actor: str = "codex",
) -> ExtractionImportResult:
    """Use Word COM read-only extraction for one explicitly selected current source version."""
    root = Path(source_root).resolve(strict=True)
    work = Path(workspace).resolve(strict=True)
    manifests = Path(manifest_root).resolve()
    if not root.is_dir() or not work.is_dir():
        raise ContentLibraryExtractionError("extraction_root_invalid")
    connection.execute("PRAGMA foreign_keys=ON")
    source_path, source_row = _require_current_source_version(
        connection, source_version_id=source_version_id, source_root=root,
    )
    existing = _existing_result(connection, source_version_id=source_version_id)
    if existing is not None:
        return existing
    profile = extract_document(source_path)
    if str(profile["source_sha256"]).upper() != source_row["original_sha256"]:
        raise ContentLibraryExtractionError("word_com_profile_source_hash_mismatch")
    manifest_path, manifest_hash = _write_manifest(
        profile, manifest_root=manifests, source_version_id=source_version_id,
    )
    blocks = profile["blocks"]
    assets = profile["assets"]
    events_created = 0
    with connection:
        run_id, transaction_id, created = _create_run(
            connection, source_version_id=source_version_id, workspace=work, actor=actor,
        )
        events_created += created
        revision = _git_revision(work)
        extraction_id = _stable_id("content-extraction", {
            "source_version_id": source_version_id, "manifest_sha256": manifest_hash,
            "importer_id": IMPORTER_ID, "importer_version": IMPORTER_VERSION,
        })
        extraction_event_id, extraction_created = _insert_event(
            connection, event_type="extraction_started", entity_type="content_extraction_runs",
            entity_id=extraction_id, operation="create", reason="persist read-only Word COM extraction manifest",
            actor=actor, import_run_id=run_id, transaction_id=transaction_id,
            after_state={"engine_id": profile["engine_id"], "engine_version": profile["engine_version"], "manifest_path": str(manifest_path)},
            after_hash=manifest_hash, git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
        )
        events_created += int(extraction_created)
        connection.execute(
            """INSERT INTO content_extraction_runs(
                id,source_version_id,import_run_id,engine_id,engine_version,profile_schema,
                extraction_manifest_path,extraction_manifest_sha256,created_change_event_id
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (extraction_id, source_version_id, run_id, profile["engine_id"], profile["engine_version"],
             profile["schema"], str(manifest_path), manifest_hash, extraction_event_id),
        )
        for block in blocks:
            block_id = _stable_id("source-block", {
                "extraction_run_id": extraction_id, "ordinal": block["ordinal"], "raw_sha256": block["raw_sha256"],
            })
            block_event_id, block_created = _insert_event(
                connection, event_type="content_created", entity_type="source_content_blocks", entity_id=block_id,
                operation="create", reason="persist source-anchored Word content block", actor=actor,
                import_run_id=run_id, transaction_id=transaction_id,
                after_state={"ordinal": block["ordinal"], "kind": block["kind"], "locator": block["locator"]},
                after_hash=block["raw_sha256"].upper(), git_revision=revision,
                tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
            )
            events_created += int(block_created)
            connection.execute(
                """INSERT INTO source_content_blocks(
                    id,extraction_run_id,ordinal,block_kind,parent_block_id,locator_json,raw_text,normalized_text,
                    raw_sha256,normalized_sha256,created_change_event_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (block_id, extraction_id, block["ordinal"], block["kind"], None, canonical(block["locator"]),
                 block["raw_text"], block["normalized_text"], block["raw_sha256"].upper(),
                 block["normalized_sha256"].upper(), block_event_id),
            )
        for asset in assets:
            asset_id = _stable_id("source-asset", {
                "extraction_run_id": extraction_id, "ordinal": asset["ordinal"], "locator": asset["locator"],
            })
            package_reference = asset.get("package_reference")
            asset_sha = asset.get("asset_sha256")
            extraction_status = "extracted" if package_reference else "referenced"
            asset_event_id, asset_created = _insert_event(
                connection, event_type="content_created", entity_type="source_content_assets", entity_id=asset_id,
                operation="create", reason="persist source-anchored Word asset reference", actor=actor,
                import_run_id=run_id, transaction_id=transaction_id,
                after_state={"ordinal": asset["ordinal"], "kind": asset["kind"], "locator": asset["locator"],
                             "package_reference": package_reference, "extraction_status": extraction_status},
                after_hash=asset_sha, git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
            )
            events_created += int(asset_created)
            connection.execute(
                """INSERT INTO source_content_assets(
                    id,extraction_run_id,ordinal,asset_kind,locator_json,package_reference,asset_sha256,
                    extraction_status,created_change_event_id
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (asset_id, extraction_id, asset["ordinal"], asset["kind"], canonical(asset["locator"]),
                 package_reference, asset_sha, extraction_status, asset_event_id),
            )
        lifecycle_id = _stable_id("source-lifecycle", {
            "source_version_id": source_version_id, "status": "extracted", "extraction_run_id": extraction_id,
        })
        lifecycle_event_id, lifecycle_created = _insert_event(
            connection, event_type="source_status_changed", entity_type="content_source_lifecycle_events",
            entity_id=lifecycle_id, operation="status_change", reason="read-only Word COM extraction completed",
            actor=actor, import_run_id=run_id, transaction_id=transaction_id,
            after_state={"status": "extracted", "retry_eligible": True}, after_hash=manifest_hash,
            git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
        )
        events_created += int(lifecycle_created)
        connection.execute(
            """INSERT INTO content_source_lifecycle_events(
                id,source_version_id,status,reason,retry_eligible,change_event_id
            ) VALUES(?,?,?,?,?,?)""",
            (lifecycle_id, source_version_id, "extracted", "read-only Word COM extraction completed", 1, lifecycle_event_id),
        )
        totals = {"source_blocks": len(blocks), "source_assets": len(assets), "source_version_id": source_version_id}
        complete_id = f"{run_id}:completed"
        complete_event_id, complete_created = _insert_event(
            connection, event_type="import_run_finished", entity_type="content_import_run_events", entity_id=complete_id,
            operation="status_change", reason="complete explicit Word source extraction batch", actor=actor,
            import_run_id=run_id, transaction_id=transaction_id,
            after_state={"status": "completed", "totals": totals}, after_hash=digest_text(canonical(totals)),
            git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
        )
        events_created += int(complete_created)
        connection.execute(
            """INSERT INTO content_import_run_events(
                id,import_run_id,status,totals_json,change_event_id
            ) VALUES(?,?,?,?,?)""",
            (complete_id, run_id, "completed", canonical(totals), complete_event_id),
        )
    return ExtractionImportResult(
        source_version_id=source_version_id, import_run_id=run_id, extraction_run_id=extraction_id,
        status="extracted", source_blocks=len(blocks), source_assets=len(assets),
        change_events_created=events_created,
    )
