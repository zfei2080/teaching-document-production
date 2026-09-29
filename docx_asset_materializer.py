"""Materialize source media from .docx packages into question_assets for one isolated database.

Read-only with respect to the protected development database. Media bytes are
written only inside an explicit ignored trial root, and ``question_assets`` /
``question_source_asset_evidence`` rows are inserted only into the explicitly
selected trial database. A question asset is marked ``verified`` only when its
bytes exist on disk and their SHA-256 matches the recorded package asset hash.
All inserts happen in a single transaction that rolls back on any error, so a
failed run never leaves partially materialized rows.
"""
from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from source_library_intake import _git_revision, _insert_event, _stable_id

ROOT = Path(__file__).resolve().parent
PROTECTED_DATABASE = (ROOT / "data" / "dev" / "teaching_docs_dev.db").resolve()
TOOL_ID = "docx-asset-materializer"
TOOL_VERSION = "1.0.0"


class AssetMaterializationError(RuntimeError):
    """Raised when the materializer would leave its isolated database boundary."""


def _package_bytes(source_path: Path, package_reference: str) -> bytes | None:
    entry = str(package_reference).lstrip("/")
    with zipfile.ZipFile(source_path) as archive:
        try:
            return archive.read(entry)
        except KeyError:
            return None


def _write_verified_media(
    source_path: Path, asset_sha256: str, package_reference: str, asset_root: Path
) -> tuple[Path, bool]:
    """Write one media file; return (target, was_new). Raises on sha mismatch."""
    blob = _package_bytes(source_path, package_reference)
    if blob is None:
        return None, False
    digest = hashlib.sha256(blob).hexdigest()
    if digest != asset_sha256:
        raise AssetMaterializationError("media_sha256_mismatch")
    suffix = Path(package_reference).suffix or ".bin"
    target = asset_root / (digest + suffix)
    if target.exists():
        return target, False
    target.write_bytes(blob)
    return target, True


def materialize_question_assets(
    *,
    database: str | Path,
    source_root: str | Path,
    asset_root: str | Path,
    workspace: str | Path,
    actor: str = "docx-asset-materializer",
) -> dict[str, Any]:
    """Bind media-bearing source assets to the questions that reference their paragraphs."""
    database = Path(database).resolve()
    if database == PROTECTED_DATABASE:
        raise AssetMaterializationError("protected_development_database_forbidden")
    source_root = Path(source_root).resolve(strict=True)
    asset_root = Path(asset_root).resolve()
    asset_root.mkdir(parents=True, exist_ok=True)
    revision = _git_revision(workspace)
    transaction_id = _stable_id("transaction", {"operation": "docx_asset_materialization", "tool_version": TOOL_VERSION})

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            version_paths = {
                row["id"]: source_root / row["original_relative_path"] for row in connection.execute(
                    "SELECT id, original_relative_path FROM content_source_versions WHERE file_type='docx'"
                )
            }
            run_sources = {
                row["id"]: row["source_version_id"] for row in connection.execute(
                    "SELECT id, source_version_id FROM content_extraction_runs"
                )
            }
            # Media-bearing drawing assets indexed by (extraction_run_id, paragraph_index).
            asset_by_paragraph: dict[tuple[str, int], list[sqlite3.Row]] = {}
            for row in connection.execute(
                """SELECT a.id AS asset_id, a.extraction_run_id, a.package_reference, a.asset_sha256,
                          json_extract(a.locator_json, '$.paragraph_index') AS paragraph_index
                     FROM source_content_assets a
                    WHERE a.package_reference IS NOT NULL AND a.asset_sha256 IS NOT NULL
                      AND json_extract(a.locator_json, '$.paragraph_index') IS NOT NULL"""
            ):
                key = (row["extraction_run_id"], row["paragraph_index"])
                asset_by_paragraph.setdefault(key, []).append(row)

            stats = {
                "questions": 0, "questions_with_images": 0, "question_assets_inserted": 0,
                "asset_evidence_inserted": 0, "media_files_written": 0, "media_missing_skipped": 0,
            }
            for qrow in connection.execute("SELECT id FROM questions ORDER BY id"):
                question_id = qrow["id"]
                blocks = connection.execute(
                    """SELECT b.id AS block_id, b.extraction_run_id,
                              json_extract(b.locator_json, '$.paragraph_index') AS paragraph_index
                         FROM (
                            SELECT e.source_block_id AS block_id
                              FROM content_item_evidence e
                              JOIN content_item_question_links l ON l.content_item_id=e.content_item_id
                             WHERE l.question_id=?
                            UNION
                            SELECT e.source_block_id
                              FROM question_internal_evidence e
                             WHERE e.question_id=?
                         ) u
                         JOIN source_content_blocks b ON b.id=u.block_id
                        WHERE b.block_kind='paragraph'
                          AND json_extract(b.locator_json, '$.paragraph_index') IS NOT NULL""",
                    (question_id, question_id),
                ).fetchall()
                stats["questions"] += 1
                bound_by_asset: dict[str, sqlite3.Row] = {}
                for block in blocks:
                    key = (block["extraction_run_id"], block["paragraph_index"])
                    for asset in asset_by_paragraph.get(key, []):
                        bound_by_asset.setdefault(asset["asset_id"], asset)
                if not bound_by_asset:
                    continue
                # Deduplicate by media digest so one image used twice in a question
                # yields one question_asset and one stable asset id.
                deduped: list[sqlite3.Row] = []
                seen_digests: set[str] = set()
                for asset in sorted(bound_by_asset.values(), key=lambda item: item["asset_id"]):
                    if asset["asset_sha256"] in seen_digests:
                        continue
                    seen_digests.add(asset["asset_sha256"])
                    deduped.append(asset)
                stats["questions_with_images"] += 1
                run_id_ = blocks[0]["extraction_run_id"]
                source_path = version_paths.get(run_sources.get(run_id_))
                if source_path is None or not source_path.is_file():
                    raise AssetMaterializationError("source_docx_missing_for_asset_binding")
                position = 0
                for asset in deduped:
                    target, media_is_new = _write_verified_media(
                        source_path, asset["asset_sha256"], asset["package_reference"], asset_root,
                    )
                    if target is None:
                        stats["media_missing_skipped"] += 1
                        continue
                    if media_is_new:
                        stats["media_files_written"] += 1
                    relative_path = target.relative_to(ROOT).as_posix()
                    qa_id = _stable_id("question-asset", {
                        "question_id": question_id, "asset_sha256": asset["asset_sha256"],
                    })
                    qa_event_id, _ = _insert_event(
                        connection, event_type="content_created", entity_type="question_assets", entity_id=qa_id,
                        operation="create", reason="materialize verified source image asset", actor=actor,
                        import_run_id=None, transaction_id=transaction_id,
                        after_state={"question_id": question_id, "asset_type": "image", "relative_path": relative_path,
                                     "position": position, "status": "verified"},
                        after_hash=asset["asset_sha256"].upper(), git_revision=revision,
                        tool_id=TOOL_ID, tool_version=TOOL_VERSION,
                    )
                    rowcount = connection.execute(
                        """INSERT OR IGNORE INTO question_assets(
                            id,question_id,asset_type,relative_path,source_fragment_id,position,checksum,status
                        ) VALUES(?,?,?,?,?,?,?,?)""",
                        (qa_id, question_id, "image", relative_path, None, position,
                         asset["asset_sha256"].upper(), "verified"),
                    ).rowcount
                    stats["question_assets_inserted"] += rowcount
                    # Bind only to the paragraph block that actually contains this image.
                    matching_blocks = [
                        block for block in blocks if block["paragraph_index"] == asset["paragraph_index"]
                    ]
                    for block in matching_blocks:
                        evidence_id = _stable_id("question-asset-evidence", {
                            "question_id": question_id, "asset_id": asset["asset_id"], "block_id": block["block_id"],
                        })
                        ev_event_id, _ = _insert_event(
                            connection, event_type="content_created", entity_type="question_source_asset_evidence",
                            entity_id=evidence_id, operation="create",
                            reason="bind question to materialized source image", actor=actor,
                            import_run_id=None, transaction_id=transaction_id,
                            after_state={"question_id": question_id, "source_content_asset_id": asset["asset_id"],
                                         "source_block_id": block["block_id"], "position": position},
                            after_hash=asset["asset_sha256"].upper(), git_revision=revision,
                            tool_id=TOOL_ID, tool_version=TOOL_VERSION,
                        )
                        ev_rowcount = connection.execute(
                            """INSERT OR IGNORE INTO question_source_asset_evidence(
                                id,question_id,source_content_asset_id,source_block_id,option_label,position,
                                evidence_sha256,created_change_event_id
                            ) VALUES(?,?,?,?,?,?,?,?)""",
                            (evidence_id, question_id, asset["asset_id"], block["block_id"], None, position,
                             asset["asset_sha256"].upper(), ev_event_id),
                        ).rowcount
                        stats["asset_evidence_inserted"] += ev_rowcount
                    position += 1
            return stats
    finally:
        connection.close()
