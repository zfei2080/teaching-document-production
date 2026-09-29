"""Approve and materialize audited, exact DOCX media adjacency evidence.

This is not visual-semantic approval.  It only extracts byte-identical internal
media for manifest instances already proved to be exact question-stem adjacency.
Image meaning remains subject to the separate ``asset_semantics`` validator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
import zipfile
from pathlib import Path
from typing import Any

from controlled_asset_adjacency_import import DEFAULT_DB, ROOT, sha256_bytes

AUDIT_METHOD = "controlled-asset-adjacency-audit-v1"


class AssetAdjacencyAuditError(ValueError):
    """The audit is incomplete, evidence has drifted, or media cannot be proved."""


def load_audit_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetAdjacencyAuditError(f"audit manifest must be UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AssetAdjacencyAuditError("audit manifest root must be an object")
    return value, sha256_bytes(raw)


def required(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise AssetAdjacencyAuditError(f"missing or invalid audit field: {key}")
    return item.strip()


def findings(value: dict[str, Any]) -> list[dict[str, Any]]:
    items = value.get("findings")
    if not isinstance(items, list) or not items:
        raise AssetAdjacencyAuditError("findings must be a non-empty list")
    for item in items:
        if not isinstance(item, dict) or item.get("status") not in {"pass", "fail"}:
            raise AssetAdjacencyAuditError("each finding must be an object with pass/fail status")
        required(item, "check")
        required(item, "evidence")
    if any(item["status"] != "pass" for item in items):
        raise AssetAdjacencyAuditError("an adjacency audit can be recorded only when every finding passes")
    return items


def _safe_media_bytes(source_path: Path, media_path: str, expected_hash: str) -> bytes:
    if not media_path.startswith("word/media/") or ".." in Path(media_path).parts:
        raise AssetAdjacencyAuditError("media path is outside permitted DOCX media package boundary")
    try:
        with zipfile.ZipFile(source_path) as package:
            value = package.read(media_path)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise AssetAdjacencyAuditError(f"cannot retrieve archived internal media: {exc}") from exc
    if hashlib.sha256(value).hexdigest() != expected_hash:
        raise AssetAdjacencyAuditError("archived media checksum does not match audited adjacency evidence")
    return value


def record_asset_adjacency_audit(conn: sqlite3.Connection, audit_manifest_path: Path) -> dict[str, str | int]:
    manifest, audit_manifest_hash = load_audit_manifest(audit_manifest_path)
    if manifest.get("schema_version") != AUDIT_METHOD:
        raise AssetAdjacencyAuditError(f"unsupported schema_version; expected {AUDIT_METHOD}")
    import_id = required(manifest, "import_run_id")
    manifest_hash = required(manifest, "manifest_hash")
    source_document_id = required(manifest, "source_document_id")
    source_hash = required(manifest, "source_sha256")
    auditor_id = required(manifest, "auditor_id")
    audit_findings = findings(manifest)

    run = conn.execute(
        """SELECT aii.source_document_id, aii.source_hash, aii.manifest_hash, aii.status,
                  sd.relative_path, sd.file_hash
           FROM asset_adjacency_imports aii JOIN source_documents sd ON sd.id=aii.source_document_id
           WHERE aii.id=?""",
        (import_id,),
    ).fetchone()
    if run is None or run[3] != "validated":
        raise AssetAdjacencyAuditError("audit requires a validated adjacency import")
    if tuple(run[:3]) != (source_document_id, source_hash, manifest_hash) or run[5] != source_hash:
        raise AssetAdjacencyAuditError("audit source and manifest identity must exactly match the validated import")
    source_path = ROOT / run[4]
    if not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != source_hash:
        raise AssetAdjacencyAuditError("source document is missing or no longer matches adjacency evidence")

    assigned = conn.execute(
        """SELECT instance_id, question_id, source_fragment_id, media_path, media_sha256, filename
           FROM asset_adjacency_instances
           WHERE import_run_id=? AND assignment_status='assigned'
             AND relationship_status='resolved_internal'""",
        (import_id,),
    ).fetchall()
    total_assigned = conn.execute(
        "SELECT COUNT(*) FROM asset_adjacency_instances WHERE import_run_id=? AND assignment_status='assigned'",
        (import_id,),
    ).fetchone()[0]
    if not assigned or len(assigned) != total_assigned:
        raise AssetAdjacencyAuditError("every assigned instance must be resolved internal media before approval")
    if conn.execute("SELECT 1 FROM asset_adjacency_audits WHERE import_run_id=?", (import_id,)).fetchone():
        raise AssetAdjacencyAuditError("adjacency import already has an audit; refusing silent overwrite")

    extracted: list[tuple[Any, bytes, Path]] = []
    for row in assigned:
        if not row[3] or not row[4]:
            raise AssetAdjacencyAuditError("assigned internal instance lacks media identity")
        data = _safe_media_bytes(source_path, row[3], row[4])
        suffix = Path(row[5] or row[3]).suffix.lower() or ".bin"
        relative = Path("data") / "dev" / "controlled-assets" / source_hash / f"{row[0]}{suffix}"
        extracted.append((row, data, ROOT / relative))

    audit_id = f"asset-adjacency-audit:{uuid.uuid4()}"
    with conn:
        conn.execute(
            """INSERT INTO asset_adjacency_audits
               (id, import_run_id, manifest_hash, audit_manifest_hash, auditor_id, audit_method,
                source_document_id, source_hash, status, findings_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)""",
            (audit_id, import_id, manifest_hash, audit_manifest_hash, auditor_id, AUDIT_METHOD,
             source_document_id, source_hash, json.dumps(audit_findings, ensure_ascii=False, sort_keys=True)),
        )
        conn.execute("UPDATE asset_adjacency_imports SET status='approved' WHERE id=?", (import_id,))
        for row, data, target in extracted:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            asset_id = f"asset-adjacency:{import_id}:{row[0]}"
            conn.execute(
                """INSERT INTO question_assets
                   (id, question_id, asset_type, relative_path, source_fragment_id, position, checksum, status)
                   VALUES (?, ?, 'image', ?, ?, 0, ?, 'pending')""",
                (asset_id, row[1], target.relative_to(ROOT).as_posix(), row[2], row[4]),
            )
            conn.execute(
                "INSERT INTO question_asset_adjacency_links (question_asset_id, import_run_id, instance_id) VALUES (?, ?, ?)",
                (asset_id, import_id, row[0]),
            )
            conn.execute("UPDATE question_assets SET status='verified' WHERE id=?", (asset_id,))
    return {"audit_id": audit_id, "import_run_id": import_id, "verified_assets": len(extracted), "status": "approved"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve and materialize source-bound asset adjacency evidence")
    parser.add_argument("audit_manifest", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        print(json.dumps(record_asset_adjacency_audit(conn, args.audit_manifest), ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
