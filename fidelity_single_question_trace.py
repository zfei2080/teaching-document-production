"""Trace one explicit, already-imported DOC question through the fidelity pipeline.

This diagnostic is intentionally read-only.  It opens SQLite with ``mode=ro``
and ``query_only``, accepts one ``question_id``, and writes the full evidence
only to the ignored local evidence directory.  Its public summary contains no
question text, answer, analysis, image bytes, or source absolute path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from fidelity_sample_audit import DEFAULT_DB, DEFAULT_SOURCE_ROOT, readonly_connection, resolve_under_root
from source_content_question_segmentation import QuestionSegmentationError, SourceBlock, segment_questions
from word_com_content_extractor import WordComContentExtractionError, extract_document


ROOT = Path(__file__).resolve().parent
DEFAULT_EVIDENCE_DIR = ROOT / "data" / "dev" / "golden-samples" / "fidelity-002"
DEFAULT_PUBLIC_DIR = ROOT / "docs" / "fidelity_audit"
ALLOWED_STATUSES = {"保留", "未提取", "未绑定", "已丢失", "无法比较"}


class FidelityTraceError(RuntimeError):
    """Raised when a single-question trace cannot preserve its safety boundary."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def redact_error(exc: BaseException) -> str:
    """Keep HRESULT/type information while excluding a potentially sensitive path."""
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return f"{type(exc).__name__}:{text[:300]}"


def hash_text(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest().upper()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def status(value: str) -> str:
    if value not in ALLOWED_STATUSES:
        raise FidelityTraceError(f"invalid_stage_status:{value}")
    return value


def question_row(conn: sqlite3.Connection, question_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT q.id, q.source_question_no, q.question_type, q.stem, q.options_json,
               q.source_fragment_id, sd.relative_path, sd.file_hash, sd.file_type,
               ci.id AS content_item_id, ci.source_version_id, ci.student_payload_json,
               er.id AS extraction_run_id
          FROM questions q
          JOIN source_documents sd ON sd.id=q.source_document_id
          LEFT JOIN content_item_question_links l ON l.question_id=q.id
          LEFT JOIN content_items ci ON ci.id=l.content_item_id
          LEFT JOIN content_extraction_runs er ON er.source_version_id=ci.source_version_id
         WHERE q.id=?
         ORDER BY er.created_at DESC, er.id DESC
         LIMIT 1
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        raise FidelityTraceError("question_not_found")
    if row["file_type"] != "doc":
        raise FidelityTraceError("question_source_is_not_doc")
    if not row["source_version_id"] or not row["extraction_run_id"]:
        raise FidelityTraceError("question_missing_content_extraction_chain")
    return dict(row)


def stored_snapshot(conn: sqlite3.Connection, question: dict[str, Any]) -> dict[str, Any]:
    extraction_run_id = question["extraction_run_id"]
    blocks = [dict(row) for row in conn.execute(
        "SELECT id, ordinal, block_kind, locator_json, raw_text, raw_sha256 "
        "FROM source_content_blocks WHERE extraction_run_id=? ORDER BY ordinal", (extraction_run_id,)
    )]
    assets = [dict(row) for row in conn.execute(
        "SELECT id, ordinal, asset_kind, locator_json, extraction_status "
        "FROM source_content_assets WHERE extraction_run_id=? ORDER BY ordinal", (extraction_run_id,)
    )]
    evidence = [dict(row) for row in conn.execute(
        """SELECT e.source_content_asset_id, e.source_block_id, e.option_label, e.position,
                  e.evidence_sha256, a.asset_kind, a.locator_json, b.ordinal AS block_ordinal,
                  b.locator_json AS block_locator_json
             FROM question_source_asset_evidence e
             JOIN source_content_assets a ON a.id=e.source_content_asset_id
             JOIN source_content_blocks b ON b.id=e.source_block_id
            WHERE e.question_id=? ORDER BY e.position, e.id""", (question["id"],)
    )]
    question_asset_count = conn.execute(
        "SELECT COUNT(*) FROM question_assets WHERE question_id=?", (question["id"],)
    ).fetchone()[0]
    fragment = None
    if table_exists(conn, "source_fragments") and question["source_fragment_id"]:
        fragment_row = conn.execute(
            "SELECT id, question_number, raw_hash, raw_text FROM source_fragments WHERE id=?",
            (question["source_fragment_id"],),
        ).fetchone()
        fragment = dict(fragment_row) if fragment_row else None
    return {
        "blocks": blocks, "assets": assets, "asset_evidence": evidence,
        "question_asset_count": question_asset_count, "source_fragment": fragment,
    }


def live_word_snapshot(source_path: Path, *, attempts: int) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    for index in range(attempts):
        try:
            return extract_document(source_path), errors
        except (OSError, WordComContentExtractionError) as exc:
            errors.append(redact_error(exc))
            if index + 1 < attempts:
                time.sleep(1)
    return None, errors


def asset_range(asset: dict[str, Any]) -> tuple[int, int] | None:
    locator = asset.get("locator")
    if locator is None and isinstance(asset.get("locator_json"), str):
        locator = json.loads(asset["locator_json"])
    if not isinstance(locator, dict):
        return None
    start, end = locator.get("range_start"), locator.get("range_end")
    return (start, end) if isinstance(start, int) and isinstance(end, int) and end > start else None


def candidate_snapshot(live: dict[str, Any], question_number: str) -> dict[str, Any]:
    blocks = [SourceBlock(str(item["ordinal"]), item["ordinal"], item["raw_text"]) for item in live["blocks"]]
    try:
        candidates = segment_questions(blocks)
    except QuestionSegmentationError as exc:
        return {"status": status("无法比较"), "error": redact_error(exc), "candidate": None, "asset_ranges_in_candidate": 0}
    candidate = next((item for item in candidates if item.source_question_no == question_number), None)
    if candidate is None:
        return {"status": status("已丢失"), "error": None, "candidate": None, "asset_ranges_in_candidate": 0}
    ordinal_set = {int(value) for value in candidate.source_block_ids}
    bounds = []
    for item in live["blocks"]:
        if item["ordinal"] in ordinal_set:
            start = item["locator"].get("range_start")
            end = item["locator"].get("range_end")
            if isinstance(start, int) and isinstance(end, int):
                bounds.append((start, end))
    count = sum(
        1 for asset in live["assets"]
        if (rng := asset_range(asset)) is not None and any(start <= rng[0] and rng[1] <= end for start, end in bounds)
    )
    return {
        "status": status("保留"), "error": None, "candidate": candidate,
        "asset_ranges_in_candidate": count,
    }


def stage_result(name: str, state: str, **details: Any) -> dict[str, Any]:
    return {"stage": name, "conclusion": status(state), **details}


def trace_question(conn: sqlite3.Connection, question_id: str, source_root: Path, *, word_attempts: int) -> dict[str, Any]:
    question = question_row(conn, question_id)
    stored = stored_snapshot(conn, question)
    source_path = resolve_under_root(source_root, str(question["relative_path"]))
    if not source_path.is_file():
        raise FidelityTraceError("registered_source_file_missing")
    source_hash_matches = sha256_file(source_path) == str(question["file_hash"]).upper()
    live, word_errors = live_word_snapshot(source_path, attempts=word_attempts) if source_hash_matches else (None, ["source_hash_mismatch"])
    stages: list[dict[str, Any]] = []
    if live is None:
        stages.append(stage_result("A_word_doc_read", "无法比较", text=False, boundaries=False, formulas=False, objects=False, bindings=False, errors=word_errors))
        stages.append(stage_result("B_candidate_segmentation", "无法比较", text=False, boundaries=False, formulas=False, objects=False, bindings=False, reason="word_snapshot_unavailable"))
    else:
        stages.append(stage_result("A_word_doc_read", "保留", text=bool(live["blocks"]), boundaries=True, formulas=sum(1 for item in live["assets"] if item["kind"] == "formula"), objects=len(live["assets"]), bindings=False, block_count=len(live["blocks"]), table_cell_count=sum(1 for item in live["blocks"] if item["kind"] == "table_cell")))
        candidate = candidate_snapshot(live, str(question["source_question_no"]))
        stages.append(stage_result("B_candidate_segmentation", candidate["status"], text=candidate["candidate"] is not None, boundaries=candidate["candidate"] is not None, formulas="无法比较", objects=candidate["asset_ranges_in_candidate"], bindings=candidate["asset_ranges_in_candidate"] > 0, segmentation_error=candidate["error"]))
    db_state = "未绑定" if stored["asset_evidence"] and stored["question_asset_count"] == 0 else "保留"
    stages.append(stage_result("C_database_storage_materialization", db_state, text=bool(question["stem"]), boundaries=bool(question["source_question_no"]), formulas="无法比较", source_object_evidence=len(stored["asset_evidence"]), stored_source_assets=len(stored["assets"]), question_assets=stored["question_asset_count"], bindings=len(stored["asset_evidence"]) > 0, source_fragment=stored["source_fragment"] is not None))
    try:
        payload = json.loads(question["student_payload_json"] or "{}")
        payload_text = isinstance(payload, dict) and payload.get("stem") == question["stem"]
        payload_refs = json.dumps(payload, ensure_ascii=False).count("source_asset_ids")
        output_state = "未绑定" if stored["asset_evidence"] and stored["question_asset_count"] == 0 else "保留"
        stages.append(stage_result("D_database_to_existing_intermediate_output", output_state, text=payload_text, boundaries=payload_text, formulas="无法比较", object_references=payload_refs, renderable_assets=stored["question_asset_count"], bindings=payload_refs > 0, renderer="not_covered_by_existing_safe_entrypoint"))
    except json.JSONDecodeError:
        stages.append(stage_result("D_database_to_existing_intermediate_output", "已丢失", text=False, boundaries=False, formulas="无法比较", object_references=0, renderable_assets=stored["question_asset_count"], bindings=False, renderer="student_payload_json_invalid"))
    first_proven = next((item["stage"] for item in stages if item["conclusion"] in {"未提取", "未绑定", "已丢失"}), None)
    return {
        "schema": "fidelity-002-single-question-trace-v1", "question_id": question["id"],
        "source_version_id": question["source_version_id"], "source_relative_path": question["relative_path"],
        "source_question_no": question["source_question_no"], "source_file_sha256": str(question["file_hash"]).upper(),
        "source_hash_matches": source_hash_matches, "word_attempts": word_attempts, "word_errors": word_errors,
        "first_proven_issue": first_proven, "stages": stages,
        "local_evidence": {"question": question, "stored": stored, "live_word": live},
    }


def public_record(trace: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in trace.items() if key != "local_evidence"}


def render_markdown(trace: dict[str, Any], *, local_path: Path, local_hash: str) -> str:
    lines = [
        "# FIDELITY-002 单题阶段追踪摘要",
        "",
        "本摘要不含题干、答案、解析、图片、资产字节或源文件绝对路径。",
        "",
        f"- `question_id`：`{trace['question_id']}`",
        f"- `source_version_id`：`{trace['source_version_id']}`",
        f"- 源文件相对路径：`{trace['source_relative_path']}`",
        f"- 源题号：`{trace['source_question_no']}`",
        f"- 源文件 SHA-256 已匹配：`{trace['source_hash_matches']}`",
        f"- 最早已证实问题：`{trace['first_proven_issue'] or '无法证明'}`",
        f"- 本机详细证据：`{local_path}`；SHA-256：`{local_hash}`",
        "",
        "| 阶段 | 结论 | 文本 | 对象/引用 | 说明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in trace["stages"]:
        object_count = item.get("objects", item.get("source_object_evidence", item.get("object_references", 0)))
        detail = item.get("reason") or item.get("renderer") or "-"
        lines.append(f"| {item['stage']} | {item['conclusion']} | {item.get('text')} | {object_count} | {detail} |")
    return "\n".join(lines) + "\n"


def write_outputs(trace: dict[str, Any], evidence_dir: Path, public_path: Path, markdown_path: Path | None = None) -> tuple[Path, str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    local_path = evidence_dir / f"{hash_text(trace['question_id'])[:16]}.json"
    local_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    local_hash = sha256_file(local_path)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text(json.dumps(public_record(trace), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(trace, local_path=local_path, local_hash=local_hash), encoding="utf-8")
    return local_path, local_hash


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question-id", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC_DIR / "FIDELITY-002_single_question_trace.json")
    parser.add_argument("--markdown", type=Path, default=DEFAULT_PUBLIC_DIR / "FIDELITY-002_single_question_trace.md")
    parser.add_argument("--word-attempts", type=int, default=2, choices=(1, 2))
    args = parser.parse_args()
    source_root = args.source_root.resolve(strict=True)
    try:
        source_root.relative_to(ROOT)
    except ValueError as exc:
        raise FidelityTraceError("source_root_outside_workspace") from exc
    before = sha256_file(args.db)
    conn = readonly_connection(args.db)
    try:
        trace = trace_question(conn, args.question_id, source_root, word_attempts=args.word_attempts)
    finally:
        conn.close()
    after = sha256_file(args.db)
    if after != before:
        raise FidelityTraceError("database_changed_during_readonly_trace")
    local_path, local_hash = write_outputs(trace, args.evidence_dir, args.public, args.markdown)
    print(json.dumps({"question_id": trace["question_id"], "first_proven_issue": trace["first_proven_issue"], "database_sha256": after, "local_evidence_path": str(local_path), "local_evidence_sha256": local_hash, "public_summary_path": str(args.public), "markdown_summary_path": str(args.markdown)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
