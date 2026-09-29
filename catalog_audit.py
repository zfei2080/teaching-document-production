"""Record a reproducible, source-bound audit before approving a catalog release.

The audit is intentionally narrow: it verifies the catalog release, its linked
controlled import, the immutable source identity, and a review manifest with
explicit findings.  It never approves questions or mappings.
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
AUDITOR_ID = "catalog_audit"
AUDIT_METHOD = "controlled-catalog-audit-v1"


class CatalogAuditError(ValueError):
    """The catalog audit evidence is incomplete or does not bind its source."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_audit_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogAuditError(f"audit manifest must be UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogAuditError("audit manifest root must be an object")
    return value, sha256_bytes(raw)


def required(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise CatalogAuditError(f"missing or invalid audit field: {key}")
    return result.strip()


def record_catalog_audit(conn: sqlite3.Connection, audit_manifest_path: Path) -> dict[str, str]:
    manifest, manifest_hash = load_audit_manifest(audit_manifest_path)
    if manifest.get("schema_version") != "controlled-catalog-audit-v1":
        raise CatalogAuditError("unsupported schema_version; expected controlled-catalog-audit-v1")
    release_id = required(manifest, "catalog_release_id")
    import_id = required(manifest, "import_run_id")
    source_reference = required(manifest, "source_reference")
    source_hash = required(manifest, "source_sha256")
    auditor_id = required(manifest, "auditor_id")
    findings = manifest.get("findings")
    if not isinstance(findings, list) or not findings:
        raise CatalogAuditError("findings must be a non-empty list")
    for finding in findings:
        if not isinstance(finding, dict):
            raise CatalogAuditError("each finding must be an object")
        if finding.get("status") not in {"pass", "fail"}:
            raise CatalogAuditError("each finding status must be pass or fail")
        required(finding, "check")
        required(finding, "evidence")
    if any(item["status"] != "pass" for item in findings):
        raise CatalogAuditError("a catalog audit may be recorded only when every finding passes")

    release = conn.execute(
        "SELECT source_reference, source_hash, status FROM catalog_releases WHERE id=?", (release_id,)
    ).fetchone()
    if release is None:
        raise CatalogAuditError("catalog release does not exist")
    import_run = conn.execute(
        "SELECT import_kind, source_reference, source_hash, status FROM controlled_import_runs WHERE id=?", (import_id,)
    ).fetchone()
    if import_run is None:
        raise CatalogAuditError("controlled catalog import does not exist")
    linked = conn.execute(
        "SELECT 1 FROM catalog_release_imports WHERE catalog_release_id=? AND import_run_id=?", (release_id, import_id)
    ).fetchone()
    if linked is None:
        raise CatalogAuditError("audit import run is not linked to the catalog release")
    if import_run[0] != "catalog" or import_run[3] != "validated":
        raise CatalogAuditError("audit requires a validated catalog import run")
    if release[2] != "draft":
        raise CatalogAuditError("only a draft catalog release may be approved")
    if (release[0], release[1]) != (source_reference, source_hash) or (import_run[1], import_run[2]) != (source_reference, source_hash):
        raise CatalogAuditError("audit source identity must exactly match the release and controlled import")

    audit_id = f"catalog-audit:{uuid.uuid4()}"
    with conn:
        conn.execute(
            """INSERT INTO catalog_audits
               (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id,
                audit_method, source_reference, source_hash, status, findings_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)""",
            (audit_id, release_id, import_id, manifest_hash, auditor_id, AUDIT_METHOD,
             source_reference, source_hash, json.dumps(findings, ensure_ascii=False, sort_keys=True)),
        )
        conn.execute("UPDATE catalog_releases SET status='approved' WHERE id=?", (release_id,))
    return {"audit_id": audit_id, "catalog_release_id": release_id, "status": "approved"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a source-bound approved catalog audit")
    parser.add_argument("audit_manifest", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        print(json.dumps(record_catalog_audit(conn, args.audit_manifest), ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
