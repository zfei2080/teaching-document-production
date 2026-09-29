"""Run one isolated DOC asset-pipeline proof against an explicit trial database.

The proof never writes the production database.  A verified asset can be
rendered only after a real materializer has returned bytes, written them inside
the ignored trial directory, and inserted a matching ``question_assets`` row.
This module deliberately does not synthesize media from locator metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fidelity_sample_audit import DEFAULT_DB, DEFAULT_SOURCE_ROOT, readonly_connection, resolve_under_root
from fidelity_single_question_trace import candidate_snapshot, redact_error, sha256_file
from word_com_content_extractor import WordComContentExtractionError, extract_document


ROOT = Path(__file__).resolve().parent
DEFAULT_TRIAL_ROOT = ROOT / "data" / "dev" / "golden-samples" / "asset-pipeline-proof-001"
ALLOWED_STAGE_STATUSES = frozenset({"保留", "未提取", "未绑定", "已丢失", "无法比较"})


class AssetPipelineProofError(RuntimeError):
    """Raised when the proof would leave its isolated, single-question boundary."""


def stage(conclusion: str, **fields: Any) -> dict[str, Any]:
    if conclusion not in ALLOWED_STAGE_STATUSES:
        raise AssetPipelineProofError(f"invalid_stage_conclusion:{conclusion}")
    return {"conclusion": conclusion, **fields}


def ensure_trial_path(path: Path, *, trial_root: Path = DEFAULT_TRIAL_ROOT) -> Path:
    resolved = path.resolve()
    root = trial_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AssetPipelineProofError("trial_path_outside_ignored_root") from exc
    if resolved == DEFAULT_DB.resolve():
        raise AssetPipelineProofError("production_database_cannot_be_trial_target")
    return resolved


def prepare_trial_database(production_db: Path, trial_db: Path) -> tuple[str, str]:
    target = ensure_trial_path(trial_db)
    if production_db.resolve() != DEFAULT_DB.resolve():
        raise AssetPipelineProofError("unexpected_production_database")
    before = sha256_file(production_db)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(production_db, target)
    copied = sha256_file(target)
    if copied != before:
        raise AssetPipelineProofError("trial_database_copy_hash_mismatch")
    return before, copied


def readonly_question_snapshot(question_id: str) -> dict[str, Any]:
    conn = readonly_connection(DEFAULT_DB)
    try:
        row = conn.execute(
            """SELECT q.id, q.source_question_no, q.question_type, q.stem, q.source_fragment_id,
                      sd.relative_path, sd.file_hash, ci.source_version_id,
                      er.id AS extraction_run_id
                 FROM questions q
                 JOIN source_documents sd ON sd.id=q.source_document_id
                 JOIN content_item_question_links link ON link.question_id=q.id
                 JOIN content_items ci ON ci.id=link.content_item_id
                 JOIN content_extraction_runs er ON er.source_version_id=ci.source_version_id
                WHERE q.id=? AND sd.file_type='doc'
                ORDER BY er.created_at DESC, er.id DESC LIMIT 1""",
            (question_id,),
        ).fetchone()
        if row is None:
            raise AssetPipelineProofError("explicit_doc_question_not_found")
        evidence = conn.execute(
            """SELECT e.source_content_asset_id, e.source_block_id, e.position,
                      a.asset_kind, a.extraction_status, a.package_reference, a.asset_sha256, a.locator_json, b.ordinal AS block_ordinal,
                      b.locator_json AS block_locator_json
                 FROM question_source_asset_evidence e
                 JOIN source_content_assets a ON a.id=e.source_content_asset_id
                 JOIN source_content_blocks b ON b.id=e.source_block_id
                WHERE e.question_id=? ORDER BY e.position, e.id""",
            (question_id,),
        ).fetchall()
        return {"question": dict(row), "evidence": [dict(item) for item in evidence]}
    finally:
        conn.close()


def trial_question_asset_count(trial_db: Path, question_id: str) -> int:
    conn = sqlite3.connect(trial_db)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM question_assets WHERE question_id=?", (question_id,)).fetchone()[0])
    finally:
        conn.close()


def record_trial_run(trial_db: Path, question_id: str, result: dict[str, Any]) -> str:
    """Prove the copied database is writable without altering question data or assets."""
    run_id = f"asset-pipeline-proof:{uuid.uuid4()}"
    conn = sqlite3.connect(trial_db)
    try:
        with conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS asset_pipeline_proof_runs (
                    id TEXT PRIMARY KEY, question_id TEXT NOT NULL, result_json TEXT NOT NULL
                )"""
            )
            conn.execute(
                "INSERT INTO asset_pipeline_proof_runs (id, question_id, result_json) VALUES (?, ?, ?)",
                (run_id, question_id, json.dumps(result, ensure_ascii=False, sort_keys=True)),
            )
    finally:
        conn.close()
    return run_id


def render_verified_html(
    trial_db: Path, question_id: str, output_path: Path, *, trial_root: Path = DEFAULT_TRIAL_ROOT
) -> dict[str, Any]:
    """Render only verified, checksum-matching trial assets; never claim fallback success."""
    output = ensure_trial_path(output_path, trial_root=trial_root)
    conn = sqlite3.connect(trial_db)
    try:
        rows = conn.execute(
            "SELECT relative_path, checksum, status FROM question_assets WHERE question_id=? ORDER BY position, id",
            (question_id,),
        ).fetchall()
    finally:
        conn.close()
    valid: list[Path] = []
    for relative_path, checksum, status in rows:
        candidate = (ROOT / str(relative_path or "")).resolve()
        if status != "verified" or not checksum or not candidate.is_file() or sha256_file(candidate) != str(checksum).upper():
            return {"rendered": False, "reason": "verified_asset_contract_not_satisfied", "asset_rows": len(rows)}
        valid.append(candidate)
    if not valid:
        return {"rendered": False, "reason": "no_verified_assets", "asset_rows": 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    references = [Path(Path(path).relative_to(output.parent)).as_posix() for path in valid]
    body = "\n".join(f'<img src="{html.escape(reference)}" alt="source asset">' for reference in references)
    output.write_text(f"<!doctype html><meta charset=\"utf-8\"><body>{body}</body>\n", encoding="utf-8")
    return {"rendered": True, "reason": "verified_assets_referenced", "asset_rows": len(valid), "output_sha256": sha256_file(output)}


def public_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Return only identifiers, counts, hashes and status, never source content."""
    return {
        "schema": "asset-pipeline-proof-001-public-v1",
        "question_id": result["question_id"],
        "source_version_id": result["source_version_id"],
        "source_identifier": result["source_file_sha256"][:16],
        "source_question_no": result["source_question_no"],
        "source_hash_matches": result["source_hash_matches"],
        "source_asset_evidence_count": result["source_asset_evidence_count"],
        "source_asset_kinds": result["source_asset_kinds"],
        "stages": result["stages"],
        "trial_question_assets": result["trial_question_assets"],
        "render": result["render"],
        "production_db_sha256_before": result["production_db_sha256_before"],
        "production_db_sha256_after": result["production_db_sha256_after"],
        "trial_db_sha256": result["trial_db_sha256"],
        "trial_write_run_id": result["trial_write_run_id"],
    }


def run_proof(question_id: str, trial_db: Path, output_path: Path) -> dict[str, Any]:
    before, copied = prepare_trial_database(DEFAULT_DB, trial_db)
    snapshot = readonly_question_snapshot(question_id)
    question = snapshot["question"]
    evidence = snapshot["evidence"]
    source_path = resolve_under_root(DEFAULT_SOURCE_ROOT, str(question["relative_path"]))
    source_hash_matches = source_path.is_file() and sha256_file(source_path) == str(question["file_hash"]).upper()
    stages: dict[str, dict[str, Any]] = {}
    live: dict[str, Any] | None = None
    try:
        if not source_hash_matches:
            raise AssetPipelineProofError("registered_source_hash_mismatch")
        live = extract_document(source_path)
        stages["A_word_doc_read"] = stage(
            "保留", block_count=len(live["blocks"]), table_cells=sum(item["kind"] == "table_cell" for item in live["blocks"]),
            object_count=len(live["assets"]), object_kinds=sorted({str(item["kind"]) for item in live["assets"]}),
        )
    except (AssetPipelineProofError, OSError, WordComContentExtractionError) as exc:
        stages["A_word_doc_read"] = stage("无法比较", error=redact_error(exc), block_count=0, object_count=0)
    if live is None:
        stages["B_candidate_segmentation"] = stage("无法比较", reason="word_snapshot_unavailable", candidate_found=False, bound_objects=0)
    else:
        candidate = candidate_snapshot(live, str(question["source_question_no"]))
        stages["B_candidate_segmentation"] = stage(
            candidate["status"], candidate_found=candidate["candidate"] is not None,
            bound_objects=candidate["asset_ranges_in_candidate"], error=candidate["error"],
        )
    existing_assets = trial_question_asset_count(trial_db, question_id)
    unresolved_source_assets = sum(
        item["extraction_status"] == "referenced" and not item["package_reference"] and not item["asset_sha256"]
        for item in evidence
    )
    stages["C_trial_asset_materialization"] = stage(
        "未绑定" if evidence and existing_assets == 0 else "保留",
        source_object_evidence=len(evidence), existing_question_assets=existing_assets,
        materialized_assets=0, unresolved_source_assets=unresolved_source_assets,
        reason="source_asset_references_have_no_package_reference_or_asset_sha256",
    )
    render = render_verified_html(trial_db, question_id, output_path)
    stages["D_minimal_output"] = stage(
        "无法比较" if not render["rendered"] else "保留", rendered=render["rendered"], reason=render["reason"], asset_rows=render["asset_rows"],
    )
    after = sha256_file(DEFAULT_DB)
    if after != before:
        raise AssetPipelineProofError("production_database_changed")
    result = {
        "question_id": question_id, "source_version_id": question["source_version_id"],
        "source_relative_path": question["relative_path"], "source_question_no": question["source_question_no"],
        "source_file_sha256": str(question["file_hash"]).upper(),
        "source_hash_matches": source_hash_matches, "source_asset_evidence_count": len(evidence),
        "source_asset_kinds": sorted({str(item["asset_kind"]) for item in evidence}), "stages": stages,
        "trial_question_assets": existing_assets, "render": render, "production_db_sha256_before": before,
        "production_db_sha256_after": after, "trial_db_copy_sha256": copied,
    }
    result["trial_write_run_id"] = record_trial_run(trial_db, question_id, public_summary({
        **result, "trial_db_sha256": "pending", "trial_write_run_id": "pending",
    }))
    result["trial_db_sha256"] = sha256_file(trial_db)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question-id", required=True, help="One explicit already-imported DOC question id.")
    parser.add_argument("--trial-db", type=Path, default=DEFAULT_TRIAL_ROOT / "teaching_docs_trial.db")
    parser.add_argument("--output", type=Path, default=DEFAULT_TRIAL_ROOT / "output" / "proof.html")
    parser.add_argument("--summary", type=Path, default=DEFAULT_TRIAL_ROOT / "asset-pipeline-proof-summary.json")
    args = parser.parse_args()
    ensure_trial_path(args.trial_db)
    ensure_trial_path(args.output)
    summary = ensure_trial_path(args.summary)
    result = run_proof(args.question_id, args.trial_db, args.output)
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(public_summary(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": str(summary), **public_summary(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
