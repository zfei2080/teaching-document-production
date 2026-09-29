"""Normalize historical approved catalog import runs back to validated.

This one-shot tool is intentionally narrow and fail-closed. It only updates a
controlled catalog import run when exactly one explicitly linked release/audit
triple proves that the import should remain `validated` while the release stays
`approved`.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "dev" / "teaching_docs_dev.db"


class NormalizationError(ValueError):
    """Raised when the target import run is missing, ambiguous, or ineligible."""


def _fetch_target_rows(conn: sqlite3.Connection, import_run_id: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT cir.id AS import_run_id,
                  cir.import_kind,
                  cir.status AS import_status,
                  cir.source_reference AS import_source_reference,
                  cir.source_hash AS import_source_hash,
                  cri.catalog_release_id,
                  cr.status AS release_status,
                  cr.source_reference AS release_source_reference,
                  cr.source_hash AS release_source_hash,
                  ca.id AS audit_id,
                  ca.status AS audit_status,
                  ca.source_reference AS audit_source_reference,
                  ca.source_hash AS audit_source_hash
           FROM controlled_import_runs cir
           LEFT JOIN catalog_release_imports cri
             ON cri.import_run_id = cir.id
           LEFT JOIN catalog_releases cr
             ON cr.id = cri.catalog_release_id
           LEFT JOIN catalog_audits ca
             ON ca.catalog_release_id = cr.id
            AND ca.import_run_id = cir.id
           WHERE cir.id = ?""",
        (import_run_id,),
    ).fetchall()


def normalize_catalog_import_status(conn: sqlite3.Connection, import_run_id: str) -> dict[str, Any]:
    rows = _fetch_target_rows(conn, import_run_id)
    if not rows:
        raise NormalizationError("controlled import run does not exist")

    statuses = {row["import_status"] for row in rows}
    if statuses != {"approved"}:
        raise NormalizationError("target import run must currently be approved")
    kinds = {row["import_kind"] for row in rows}
    if kinds != {"catalog"}:
        raise NormalizationError("target import run must be a catalog import")

    explicit_release_ids = {row["catalog_release_id"] for row in rows if row["catalog_release_id"] is not None}
    if len(explicit_release_ids) != 1:
        raise NormalizationError("target import run must be linked to exactly one catalog release")

    row = rows[0]
    if row["release_status"] != "approved":
        raise NormalizationError("linked catalog release must be approved")
    if row["audit_id"] is None:
        raise NormalizationError("linked catalog release must have an approved catalog audit")
    if row["audit_status"] != "approved":
        raise NormalizationError("linked catalog release must have an approved catalog audit")

    identities = {
        (row["release_source_reference"], row["release_source_hash"]),
        (row["audit_source_reference"], row["audit_source_hash"]),
        (row["import_source_reference"], row["import_source_hash"]),
    }
    if len(identities) != 1:
        raise NormalizationError("release, audit, and import source identity must match exactly")

    if len(rows) != 1:
        raise NormalizationError("target import run must have exactly one matching release-audit association")

    with conn:
        cursor = conn.execute(
            "UPDATE controlled_import_runs SET status='validated' WHERE id=? AND status='approved'",
            (import_run_id,),
        )
    if cursor.rowcount != 1:
        raise NormalizationError("normalization did not update exactly one import run")

    return {
        "import_run_id": import_run_id,
        "catalog_release_id": row["catalog_release_id"],
        "audit_id": row["audit_id"],
        "from_status": "approved",
        "to_status": "validated",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize one historical approved catalog import run back to validated"
    )
    parser.add_argument("import_run_id", help="controlled_import_runs.id to normalize")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        result = normalize_catalog_import_status(conn, args.import_run_id)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
