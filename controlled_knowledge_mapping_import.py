"""Import source-bound knowledge points and node relations as pending evidence.

This is deliberately separate from catalog import: an approved curriculum
catalog does not imply approved instructional semantics.  Importing only writes
validated provenance and pending knowledge points; the audit gate is the sole
path to approval.
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
IMPORTER_ID = "controlled_knowledge_mapping_import"
IMPORTER_VERSION = "v1"


class KnowledgeManifestError(ValueError):
    """Knowledge manifest is incomplete, non-reproducible, or out of scope."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_hash(value: object) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeManifestError(f"knowledge manifest must be UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise KnowledgeManifestError("knowledge manifest root must be an object")
    return value, sha256_bytes(raw)


def required(value: dict[str, Any], key: str, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise KnowledgeManifestError(f"missing or invalid field: {context}.{key}")
    return item.strip()


def _validate_manifest(
    value: dict[str, Any], manifest_dir: Path
) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, str]]]:
    if value.get("schema_version") != "controlled-knowledge-mapping-manifest-v1":
        raise KnowledgeManifestError("unsupported schema_version; expected controlled-knowledge-mapping-manifest-v1")
    source = value.get("source")
    if not isinstance(source, dict):
        raise KnowledgeManifestError("missing source object")
    source_data = {
        "reference": required(source, "reference", "source"),
        "file": required(source, "file", "source"),
        "sha256": required(source, "sha256", "source"),
    }
    if len(source_data["sha256"]) != 64 or any(ch not in "0123456789abcdef" for ch in source_data["sha256"].lower()):
        raise KnowledgeManifestError("source.sha256 must be a SHA-256 hexadecimal digest")
    source_file = (manifest_dir / source_data["file"]).resolve()
    try:
        source_file.relative_to(manifest_dir)
    except ValueError as exc:
        raise KnowledgeManifestError("source.file must not point outside the manifest directory") from exc
    if not source_file.is_file():
        raise KnowledgeManifestError("knowledge source file does not exist")
    if sha256_bytes(source_file.read_bytes()) != source_data["sha256"]:
        raise KnowledgeManifestError("knowledge source file SHA-256 does not match manifest")
    points = value.get("knowledge_points")
    relations = value.get("curriculum_knowledge_points")
    if not isinstance(points, list) or not points or not isinstance(relations, list) or not relations:
        raise KnowledgeManifestError("knowledge_points and curriculum_knowledge_points must be non-empty lists")
    point_ids: set[str] = set()
    allowed_types = {"concept", "formula", "property", "criterion", "method", "model", "error_pattern"}
    for point in points:
        if not isinstance(point, dict):
            raise KnowledgeManifestError("each knowledge point must be an object")
        for key in ("id", "canonical_name", "knowledge_type", "stage_scope", "version"):
            required(point, key, "knowledge_point")
        if point["id"] in point_ids:
            raise KnowledgeManifestError(f"duplicate knowledge point: {point['id']}")
        if point["knowledge_type"] not in allowed_types:
            raise KnowledgeManifestError(f"invalid knowledge point type: {point['id']}")
        point_ids.add(point["id"])
    pairs: set[tuple[str, str]] = set()
    clean_relations: list[dict[str, str]] = []
    for relation in relations:
        if not isinstance(relation, dict):
            raise KnowledgeManifestError("each curriculum knowledge relation must be an object")
        node_id = required(relation, "curriculum_node_id", "curriculum_knowledge_point")
        point_id = required(relation, "knowledge_point_id", "curriculum_knowledge_point")
        relation_type = relation.get("relation_type", "primary")
        if relation_type not in {"primary", "secondary", "prerequisite"}:
            raise KnowledgeManifestError("invalid curriculum knowledge relation type")
        if point_id not in point_ids:
            raise KnowledgeManifestError("curriculum knowledge relation references a point outside this manifest")
        if (node_id, point_id) in pairs:
            raise KnowledgeManifestError("duplicate curriculum knowledge relation")
        pairs.add((node_id, point_id))
        clean_relations.append({"curriculum_node_id": node_id, "knowledge_point_id": point_id, "relation_type": relation_type})
    return source_data, points, clean_relations


def _validate_database(conn: sqlite3.Connection, release_id: str, points: list[dict[str, Any]], relations: list[dict[str, str]]) -> tuple[str, str]:
    release = conn.execute("SELECT textbook_id, catalog_version, source_reference, source_hash, status FROM catalog_releases WHERE id=?", (release_id,)).fetchone()
    if release is None or release[4] != "approved":
        raise KnowledgeManifestError("knowledge mapping requires an approved catalog release")
    textbook_id, catalog_version = release[0], release[1]
    if conn.execute("""SELECT 1 FROM controlled_import_runs cir
                     JOIN catalog_release_imports cri ON cri.import_run_id=cir.id
                     JOIN catalog_audits ca ON ca.catalog_release_id=cri.catalog_release_id AND ca.import_run_id=cir.id
                     WHERE cri.catalog_release_id=? AND cir.import_kind='catalog'
                       AND cir.status='validated' AND ca.status='approved'""", (release_id,)).fetchone() is None:
        raise KnowledgeManifestError("approved catalog release lacks a validated, source-bound controlled catalog import audit")
    for point in points:
        if conn.execute("SELECT 1 FROM knowledge_points WHERE id=?", (point["id"],)).fetchone():
            raise KnowledgeManifestError(f"knowledge point already exists; refusing overwrite: {point['id']}")
    for relation in relations:
        node = conn.execute("SELECT textbook_id, catalog_version, status FROM curriculum_nodes WHERE id=?", (relation["curriculum_node_id"],)).fetchone()
        if node is None or node[0] != textbook_id or node[1] != catalog_version or node[2] != "active":
            raise KnowledgeManifestError("curriculum knowledge relation must target an active node in the approved catalog release")
    return textbook_id, catalog_version


def import_knowledge_mapping(conn: sqlite3.Connection, manifest_path: Path) -> dict[str, str | int]:
    manifest, manifest_hash = load_manifest(manifest_path)
    release_id = required(manifest, "catalog_release_id", "manifest")
    source, points, relations = _validate_manifest(manifest, manifest_path.parent.resolve())
    textbook_id, _ = _validate_database(conn, release_id, points, relations)
    import_id = f"knowledge-mapping-import:{canonical_json_hash({'catalog_release_id': release_id, 'source': source, 'knowledge_points': points, 'curriculum_knowledge_points': relations})[:24]}"
    if conn.execute("SELECT 1 FROM controlled_knowledge_import_runs WHERE id=?", (import_id,)).fetchone():
        raise KnowledgeManifestError("identical knowledge mapping import already exists; refusing silent overwrite")
    with conn:
        conn.execute("INSERT INTO controlled_knowledge_import_runs (id, textbook_id, catalog_release_id, source_reference, source_hash, manifest_hash, importer_id, importer_version, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'validated')", (import_id, textbook_id, release_id, source["reference"], source["sha256"], manifest_hash, IMPORTER_ID, IMPORTER_VERSION))
        for point in points:
            conn.execute("INSERT INTO knowledge_points (id, canonical_name, knowledge_type, stage_scope, definition_text, formulas_json, properties_json, conditions_json, common_errors_json, version, review_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')", (point["id"], point["canonical_name"], point["knowledge_type"], point["stage_scope"], point.get("definition_text"), json.dumps(point.get("formulas", []), ensure_ascii=False), json.dumps(point.get("properties", []), ensure_ascii=False), json.dumps(point.get("conditions", []), ensure_ascii=False), json.dumps(point.get("common_errors", []), ensure_ascii=False), point["version"]))
            conn.execute("INSERT INTO knowledge_point_imports VALUES (?, ?, ?)", (point["id"], import_id, canonical_json_hash(point)))
        for relation in relations:
            conn.execute("INSERT INTO curriculum_knowledge_points VALUES (?, ?, ?)", (relation["curriculum_node_id"], relation["knowledge_point_id"], relation["relation_type"]))
            conn.execute("INSERT INTO curriculum_knowledge_point_imports VALUES (?, ?, ?, ?)", (relation["curriculum_node_id"], relation["knowledge_point_id"], import_id, canonical_json_hash(relation)))
    return {"import_id": import_id, "catalog_release_id": release_id, "knowledge_points": len(points), "relations": len(relations), "status": "validated"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import pending source-bound knowledge mappings")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        print(json.dumps(import_knowledge_mapping(conn, args.manifest), ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
