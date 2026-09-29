"""Import a source-hashed curriculum catalog into the isolated development DB.

This importer is deliberately narrow: it accepts an explicit JSON manifest and a
local source file whose SHA-256 must match the manifest.  It never infers course
nodes from question text or from a convenience scope list.  A successful import
is recorded as ``validated`` rather than approved, so it cannot by itself make
any question eligible for delivery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
DEFAULT_DB = ROOT / "data" / "dev" / "teaching_docs_dev.db"
IMPORTER_ID = "controlled_catalog_import"
IMPORTER_VERSION = "v1"


class ManifestError(ValueError):
    """The catalog manifest is incomplete, inconsistent, or not reproducible."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_hash(value: object) -> str:
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def load_manifest(path: Path) -> tuple[dict[str, Any], Path, str]:
    raw = path.read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"清单不是有效 UTF-8 JSON：{exc}") from exc
    if not isinstance(manifest, dict):
        raise ManifestError("清单根节点必须是对象")
    return manifest, path.parent.resolve(), sha256_bytes(raw)


def _required(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"缺少或无效字段：{label}.{key}")
    return value.strip()


def validate_manifest(manifest: dict[str, Any], manifest_dir: Path) -> dict[str, Any]:
    if manifest.get("schema_version") != "controlled-catalog-manifest-v1":
        raise ManifestError("仅支持 schema_version=controlled-catalog-manifest-v1")
    catalog = manifest.get("catalog")
    if not isinstance(catalog, dict):
        raise ManifestError("缺少 catalog 对象")

    required_catalog = ("textbook_id", "name", "subject", "catalog_version", "source_reference", "source_file", "source_sha256")
    for key in required_catalog:
        _required(catalog, key, "catalog")

    source_file = (manifest_dir / catalog["source_file"]).resolve()
    try:
        source_file.relative_to(manifest_dir)
    except ValueError as exc:
        raise ManifestError("catalog.source_file 不得指向清单目录之外") from exc
    if not source_file.is_file():
        raise ManifestError(f"教材来源文件不存在：{catalog['source_file']}")
    actual_source_hash = sha256_bytes(source_file.read_bytes())
    if actual_source_hash != catalog["source_sha256"]:
        raise ManifestError("教材来源文件 SHA-256 与清单不一致")

    nodes = manifest.get("nodes")
    knowledge_points = manifest.get("knowledge_points", [])
    relations = manifest.get("curriculum_knowledge_points", [])
    if not isinstance(nodes, list) or not nodes:
        raise ManifestError("nodes 必须是非空数组")
    if not isinstance(knowledge_points, list) or not isinstance(relations, list):
        raise ManifestError("knowledge_points 和 curriculum_knowledge_points 必须是数组")

    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ManifestError("nodes 中每项必须是对象")
        for key in ("id", "stage", "grade_level", "node_type", "name"):
            _required(node, key, "node")
        if node["id"] in node_ids:
            raise ManifestError(f"课程节点 ID 重复：{node['id']}")
        if node["stage"] not in {"小学", "初中", "高中"}:
            raise ManifestError(f"课程节点学段无效：{node['id']}")
        if node["node_type"] not in {"term", "chapter", "unit", "topic"}:
            raise ManifestError(f"课程节点类型无效：{node['id']}")
        if not isinstance(node.get("sequence", 0), int):
            raise ManifestError(f"课程节点 sequence 必须为整数：{node['id']}")
        node_ids.add(node["id"])
    for node in nodes:
        parent_id = node.get("parent_id")
        if parent_id is not None and parent_id not in node_ids:
            raise ManifestError(f"课程节点父节点不在同一清单中：{node['id']}")

    knowledge_point_ids: set[str] = set()
    for point in knowledge_points:
        if not isinstance(point, dict):
            raise ManifestError("knowledge_points 中每项必须是对象")
        for key in ("id", "canonical_name", "knowledge_type", "stage_scope", "version"):
            _required(point, key, "knowledge_point")
        if point["id"] in knowledge_point_ids:
            raise ManifestError(f"知识点 ID 重复：{point['id']}")
        if point["knowledge_type"] not in {"concept", "formula", "property", "criterion", "method", "model", "error_pattern"}:
            raise ManifestError(f"知识点类型无效：{point['id']}")
        knowledge_point_ids.add(point["id"])
    for relation in relations:
        if not isinstance(relation, dict):
            raise ManifestError("curriculum_knowledge_points 中每项必须是对象")
        node_id = _required(relation, "curriculum_node_id", "curriculum_knowledge_point")
        point_id = _required(relation, "knowledge_point_id", "curriculum_knowledge_point")
        if node_id not in node_ids or point_id not in knowledge_point_ids:
            raise ManifestError("课程节点—知识点关联引用了清单外 ID")
        if relation.get("relation_type", "primary") not in {"primary", "secondary", "prerequisite"}:
            raise ManifestError("课程节点—知识点关联类型无效")

    return {"catalog": catalog, "nodes": nodes, "knowledge_points": knowledge_points, "relations": relations}


def import_catalog(conn: sqlite3.Connection, manifest_path: Path) -> dict[str, str | int]:
    manifest, manifest_dir, manifest_hash = load_manifest(manifest_path)
    data = validate_manifest(manifest, manifest_dir)
    catalog = data["catalog"]
    textbook_id = catalog["textbook_id"]
    catalog_version = catalog["catalog_version"]
    release_id = f"catalog-release:{textbook_id}:{catalog_version}"
    import_id = f"catalog-import:{canonical_json_hash({'manifest_hash': manifest_hash, 'textbook_id': textbook_id, 'catalog_version': catalog_version})[:24]}"

    existing = conn.execute("SELECT 1 FROM catalog_releases WHERE id=?", (release_id,)).fetchone()
    if existing:
        raise ManifestError("该教材版本的目录发布已存在；拒绝静默覆盖")

    with conn:
        conn.execute(
            """INSERT INTO textbooks (id, name, subject, publisher, catalog_version)
               VALUES (?, ?, ?, ?, ?)""",
            (textbook_id, catalog["name"], catalog["subject"], catalog.get("publisher"), catalog_version),
        )
        for node in data["nodes"]:
            conn.execute(
                """INSERT INTO curriculum_nodes
                   (id, textbook_id, parent_id, stage, grade_level, node_type, name, sequence, catalog_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (node["id"], textbook_id, node.get("parent_id"), node["stage"], node["grade_level"], node["node_type"], node["name"], node.get("sequence", 0), catalog_version),
            )
        for point in data["knowledge_points"]:
            conn.execute(
                """INSERT INTO knowledge_points
                   (id, canonical_name, knowledge_type, stage_scope, definition_text, formulas_json,
                    properties_json, conditions_json, common_errors_json, version, review_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (point["id"], point["canonical_name"], point["knowledge_type"], point["stage_scope"], point.get("definition_text"), json.dumps(point.get("formulas", []), ensure_ascii=False), json.dumps(point.get("properties", []), ensure_ascii=False), json.dumps(point.get("conditions", []), ensure_ascii=False), json.dumps(point.get("common_errors", []), ensure_ascii=False), point["version"]),
            )
        for relation in data["relations"]:
            conn.execute(
                "INSERT INTO curriculum_knowledge_points VALUES (?, ?, ?)",
                (relation["curriculum_node_id"], relation["knowledge_point_id"], relation.get("relation_type", "primary")),
            )
        conn.execute(
            """INSERT INTO controlled_import_runs
               (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status)
               VALUES (?, 'catalog', ?, ?, ?, ?, ?, 'validated')""",
            (import_id, catalog["source_reference"], catalog["source_sha256"], manifest_hash, IMPORTER_ID, IMPORTER_VERSION),
        )
        conn.execute(
            """INSERT INTO catalog_releases
               (id, textbook_id, catalog_version, source_reference, source_hash, status)
               VALUES (?, ?, ?, ?, ?, 'draft')""",
            (release_id, textbook_id, catalog_version, catalog["source_reference"], catalog["source_sha256"]),
        )
        conn.execute("INSERT INTO catalog_release_imports VALUES (?, ?)", (release_id, import_id))
    return {"release_id": release_id, "import_id": import_id, "nodes": len(data["nodes"]), "knowledge_points": len(data["knowledge_points"])}


def main() -> None:
    parser = argparse.ArgumentParser(description="导入经哈希校验的受控教材目录（只写开发库）")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        result = import_catalog(conn, args.manifest)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
