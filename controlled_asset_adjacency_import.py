"""Import a source-bound question-to-media adjacency manifest as evidence only.

Validated adjacency evidence never materializes ``question_assets``.  A later
source-bound audit is required, and that audit can materialize only exact,
resolved-internal, assigned media instances as verified assets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
DEFAULT_DB = ROOT / "data" / "dev" / "teaching_docs_dev.db"
MANIFEST_SCHEMA = "question-asset-adjacency-manifest-v1"


class AssetAdjacencyManifestError(ValueError):
    """The adjacency evidence is incomplete, changed, or unsafe to import."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_hash(value: object) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetAdjacencyManifestError(f"adjacency manifest must be UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AssetAdjacencyManifestError("adjacency manifest root must be an object")
    return value, sha256_bytes(raw)


def required(value: dict[str, Any], key: str, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise AssetAdjacencyManifestError(f"missing or invalid field: {context}.{key}")
    return item.strip()


def _source_document(conn: sqlite3.Connection, source_name: str, source_hash: str) -> str:
    rows = conn.execute(
        "SELECT id FROM source_documents WHERE file_hash=? AND relative_path LIKE ?",
        (source_hash, f"%/{source_name}"),
    ).fetchall()
    if len(rows) != 1:
        raise AssetAdjacencyManifestError("manifest source must identify exactly one imported source document")
    return rows[0][0]


def validate_manifest(value: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    if value.get("schema") != MANIFEST_SCHEMA:
        raise AssetAdjacencyManifestError(f"unsupported schema; expected {MANIFEST_SCHEMA}")
    source = value.get("source")
    generator = value.get("generator")
    instances = value.get("instances")
    if not isinstance(source, dict) or not isinstance(generator, dict):
        raise AssetAdjacencyManifestError("manifest source and generator must be objects")
    source_data = {"file": required(source, "file", "source"), "sha256": required(source, "sha256", "source")}
    if len(source_data["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in source_data["sha256"].lower()):
        raise AssetAdjacencyManifestError("source.sha256 must be hexadecimal SHA-256")
    generator_data = {"id": required(generator, "id", "generator"), "version": required(generator, "version", "generator")}
    if not isinstance(instances, list):
        raise AssetAdjacencyManifestError("instances must be a list")
    expected = value.get("manifest_sha256")
    copy = dict(value)
    copy.pop("manifest_sha256", None)
    if not isinstance(expected, str) or expected != canonical_json_hash(copy):
        raise AssetAdjacencyManifestError("manifest_sha256 does not match canonical manifest payload")
    seen: set[str] = set()
    for row in instances:
        if not isinstance(row, dict):
            raise AssetAdjacencyManifestError("each instance must be an object")
        instance_id = required(row, "instance_id", "instance")
        if instance_id in seen:
            raise AssetAdjacencyManifestError(f"duplicate instance_id: {instance_id}")
        seen.add(instance_id)
        status = row.get("assignment_status")
        if status not in {"assigned", "unassigned", "ambiguous"}:
            raise AssetAdjacencyManifestError("invalid assignment_status")
        question_no = row.get("source_question_no")
        if status == "assigned" and (not isinstance(question_no, str) or not question_no.strip()):
            raise AssetAdjacencyManifestError("assigned instance requires source_question_no")
        if status != "assigned" and question_no is not None:
            raise AssetAdjacencyManifestError("unassigned/ambiguous instance must not name a question")
        for key in ("assignment_reason", "package_part", "relationship_id", "relationship_status", "host_kind", "host_location"):
            required(row, key, "instance")
        if row.get("relationship_status") == "resolved_internal":
            for key in ("media_path", "media_sha256"):
                required(row, key, "instance")
        layout = row.get("drawing_layout", [])
        if not isinstance(layout, list):
            raise AssetAdjacencyManifestError("drawing_layout must be a list")
    return {**source_data, **generator_data}, instances


def import_asset_adjacency(conn: sqlite3.Connection, manifest_path: Path) -> dict[str, str | int]:
    manifest, file_hash = load_manifest(manifest_path)
    data, instances = validate_manifest(manifest)
    source_document_id = _source_document(conn, data["file"], data["sha256"])
    manifest_hash = manifest["manifest_sha256"]
    run_id = f"asset-adjacency-import:{manifest_hash[:24]}"
    if conn.execute("SELECT 1 FROM asset_adjacency_imports WHERE id=? OR manifest_hash=?", (run_id, manifest_hash)).fetchone():
        raise AssetAdjacencyManifestError("identical adjacency evidence already exists; refusing silent overwrite")

    question_bindings: dict[str, tuple[str, str]] = {}
    for row in instances:
        question_no = row.get("source_question_no")
        if question_no is None:
            continue
        found = conn.execute(
            "SELECT id FROM questions WHERE source_document_id=? AND source_question_no=?",
            (source_document_id, question_no),
        ).fetchall()
        if len(found) != 1:
            raise AssetAdjacencyManifestError("assigned instance must map to exactly one imported source question")
        fragment = conn.execute(
            """SELECT sf.id FROM source_fragments sf
               JOIN question_source_fragments qsf ON qsf.source_fragment_id=sf.id
               WHERE qsf.question_id=? AND qsf.field_name='stem'
                 AND sf.source_document_id=? AND sf.paragraph_index=?
                 AND qsf.source_hash=sf.raw_hash""",
            (found[0][0], source_document_id, row.get("paragraph_index")),
        ).fetchall()
        if len(fragment) != 1:
            raise AssetAdjacencyManifestError("assigned instance must map to exactly one provenance-locked stem fragment")
        question_bindings[row["instance_id"]] = (found[0][0], fragment[0][0])

    with conn:
        conn.execute(
            """INSERT INTO asset_adjacency_imports
               (id, source_document_id, source_hash, manifest_hash, generator_id, generator_version, status)
               VALUES (?, ?, ?, ?, ?, ?, 'validated')""",
            (run_id, source_document_id, data["sha256"], manifest_hash, data["id"], data["version"]),
        )
        for row in instances:
            conn.execute(
                """INSERT INTO asset_adjacency_instances
                   (import_run_id, instance_id, assignment_status, question_id, source_fragment_id,
                    assignment_reason, package_part, relationship_id, relationship_status, relationship_target,
                    media_path, filename, media_sha256, host_kind, host_location, paragraph_index,
                    drawing_layout_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, row["instance_id"], row["assignment_status"],
                 question_bindings.get(row["instance_id"], (None, None))[0],
                 question_bindings.get(row["instance_id"], (None, None))[1],
                 row["assignment_reason"], row["package_part"], row["relationship_id"], row["relationship_status"],
                 row.get("relationship_target"), row.get("media_path"), row.get("filename"), row.get("media_sha256"),
                 row["host_kind"], row["host_location"], row.get("paragraph_index"),
                 json.dumps(row.get("drawing_layout", []), ensure_ascii=False, sort_keys=True)),
            )
    return {"import_id": run_id, "manifest_file_hash": file_hash, "manifest_hash": manifest_hash,
            "instances": len(instances), "status": "validated"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import validated, non-materialized adjacency evidence")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        print(json.dumps(import_asset_adjacency(conn, args.manifest), ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
