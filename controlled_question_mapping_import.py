"""Import source-bound question-to-curriculum mappings into the development DB.

A mapping manifest is explicit: it names existing questions, one existing textbook
release, concrete active curriculum nodes, and existing approved knowledge points.
The import is only ``validated`` and all mappings remain ``pending``; a later
source-bound audit is required before they can become eligible for scope approval.
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
IMPORTER_ID = "controlled_question_mapping_import"
IMPORTER_VERSION = "v1"


class MappingManifestError(ValueError):
    """The mapping manifest is not reproducible or violates scope boundaries."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_hash(value: object) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def load_manifest(path: Path) -> tuple[dict[str, Any], Path, str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MappingManifestError(f"mapping manifest must be UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise MappingManifestError("mapping manifest root must be an object")
    return value, path.parent.resolve(), sha256_bytes(raw)


def required(value: dict[str, Any], key: str, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise MappingManifestError(f"missing or invalid field: {context}.{key}")
    return item.strip()


def _validate_manifest(value: dict[str, Any], manifest_dir: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    if value.get("schema_version") != "controlled-question-mapping-manifest-v1":
        raise MappingManifestError("unsupported schema_version; expected controlled-question-mapping-manifest-v1")
    source = value.get("source")
    if not isinstance(source, dict):
        raise MappingManifestError("missing source object")
    source_data = {
        "reference": required(source, "reference", "source"),
        "file": required(source, "file", "source"),
        "sha256": required(source, "sha256", "source"),
    }
    if len(source_data["sha256"]) != 64 or any(ch not in "0123456789abcdef" for ch in source_data["sha256"].lower()):
        raise MappingManifestError("source.sha256 must be a SHA-256 hexadecimal digest")
    source_file = (manifest_dir / source_data["file"]).resolve()
    try:
        source_file.relative_to(manifest_dir)
    except ValueError as exc:
        raise MappingManifestError("source.file must not point outside the manifest directory") from exc
    if not source_file.is_file():
        raise MappingManifestError("question mapping source file does not exist")
    if sha256_bytes(source_file.read_bytes()) != source_data["sha256"]:
        raise MappingManifestError("question mapping source file SHA-256 does not match manifest")
    mappings = value.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise MappingManifestError("mappings must be a non-empty list")
    seen_questions: set[str] = set()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise MappingManifestError("each mapping must be an object")
        for key in ("question_id", "textbook_id", "curriculum_node_id"):
            required(mapping, key, "mapping")
        if mapping["question_id"] in seen_questions:
            raise MappingManifestError(f"question appears more than once: {mapping['question_id']}")
        seen_questions.add(mapping["question_id"])
        knowledge_points = mapping.get("knowledge_points")
        if not isinstance(knowledge_points, list) or not knowledge_points:
            raise MappingManifestError(f"mapping must name knowledge_points: {mapping['question_id']}")
        point_ids: set[str] = set()
        for point in knowledge_points:
            if not isinstance(point, dict):
                raise MappingManifestError("each knowledge point mapping must be an object")
            point_id = required(point, "knowledge_point_id", "knowledge_point")
            relation_type = point.get("relation_type", "primary")
            if relation_type not in {"primary", "secondary"}:
                raise MappingManifestError(f"invalid question knowledge-point relation type: {relation_type}")
            if point_id in point_ids:
                raise MappingManifestError(f"duplicate knowledge point in question mapping: {point_id}")
            point_ids.add(point_id)
    return source_data, mappings


def _validate_against_database(conn: sqlite3.Connection, mappings: list[dict[str, Any]]) -> None:
    for mapping in mappings:
        question = conn.execute("SELECT stage FROM questions WHERE id=?", (mapping["question_id"],)).fetchone()
        if question is None:
            raise MappingManifestError(f"question does not exist: {mapping['question_id']}")
        node = conn.execute(
            """SELECT n.textbook_id, n.stage, n.catalog_version, n.status,
                      t.catalog_version, cr.status
               FROM curriculum_nodes n
               JOIN textbooks t ON t.id=n.textbook_id
               LEFT JOIN catalog_releases cr
                 ON cr.textbook_id=n.textbook_id AND cr.catalog_version=n.catalog_version
               WHERE n.id=?""",
            (mapping["curriculum_node_id"],),
        ).fetchone()
        if node is None:
            raise MappingManifestError(f"curriculum node does not exist: {mapping['curriculum_node_id']}")
        if node[0] != mapping["textbook_id"]:
            raise MappingManifestError("textbook_id must match the curriculum node textbook")
        if node[1] != question[0]:
            raise MappingManifestError("question stage must match the curriculum node stage")
        if node[2] != node[4]:
            raise MappingManifestError("curriculum node catalog version must match textbook catalog version")
        if node[3] != "active":
            raise MappingManifestError("curriculum node must be active")
        if node[5] != "approved":
            raise MappingManifestError("mapping requires an approved catalog release for the target textbook version")
        for point in mapping["knowledge_points"]:
            point_id = point["knowledge_point_id"]
            allowed = conn.execute(
                """SELECT 1
                   FROM knowledge_points kp
                   JOIN curriculum_knowledge_points ckp ON ckp.knowledge_point_id=kp.id
                   WHERE kp.id=? AND kp.review_status='approved' AND ckp.curriculum_node_id=?""",
                (point_id, mapping["curriculum_node_id"]),
            ).fetchone()
            if allowed is None:
                raise MappingManifestError("knowledge point must be approved and explicitly linked to the curriculum node")


def import_question_mappings(conn: sqlite3.Connection, manifest_path: Path) -> dict[str, str | int]:
    manifest, manifest_dir, manifest_hash = load_manifest(manifest_path)
    source, mappings = _validate_manifest(manifest, manifest_dir)
    _validate_against_database(conn, mappings)
    mapping_hash = canonical_json_hash({"source": source, "mappings": mappings})
    import_id = f"question-mapping-import:{mapping_hash[:24]}"
    existing = conn.execute("SELECT 1 FROM controlled_import_runs WHERE id=?", (import_id,)).fetchone()
    if existing:
        raise MappingManifestError("identical mapping import already exists; refusing silent overwrite")

    with conn:
        conn.execute(
            """INSERT INTO controlled_import_runs
               (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status)
               VALUES (?, 'question_mapping', ?, ?, ?, ?, ?, 'validated')""",
            (import_id, source["reference"], source["sha256"], manifest_hash, IMPORTER_ID, IMPORTER_VERSION),
        )
        for mapping in mappings:
            question_id = mapping["question_id"]
            textbook_id = mapping["textbook_id"]
            node_id = mapping["curriculum_node_id"]
            conn.execute(
                """INSERT INTO question_textbooks
                   (question_id, textbook_id, curriculum_node_id, fit_status)
                   VALUES (?, ?, ?, 'pending')""",
                (question_id, textbook_id, node_id),
            )
            conn.execute(
                """INSERT INTO question_textbook_imports
                   (question_id, textbook_id, curriculum_node_id, import_run_id, mapping_hash)
                   VALUES (?, ?, ?, ?, ?)""",
                (question_id, textbook_id, node_id, import_id, mapping_hash),
            )
            for point in mapping["knowledge_points"]:
                point_id = point["knowledge_point_id"]
                relation_type = point.get("relation_type", "primary")
                conn.execute(
                    "INSERT INTO question_knowledge_points (question_id, knowledge_point_id, relation_type) VALUES (?, ?, ?)",
                    (question_id, point_id, relation_type),
                )
                conn.execute(
                    """INSERT INTO question_knowledge_point_imports
                       (question_id, knowledge_point_id, import_run_id, mapping_hash)
                       VALUES (?, ?, ?, ?)""",
                    (question_id, point_id, import_id, mapping_hash),
                )
    return {"import_id": import_id, "mapping_hash": mapping_hash, "mappings": len(mappings), "status": "validated"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import validated pending controlled question mappings")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        print(json.dumps(import_question_mappings(conn, args.manifest), ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
