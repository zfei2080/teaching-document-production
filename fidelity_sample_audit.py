"""Read-only FIDELITY-001 source-to-database sample audit.

The tool deliberately has no database-write option.  It reads a fixed,
reproducible sample, compares database-held fields with the current trusted
Word source through the existing source-evidence records, and writes detailed
local evidence outside Git.  The Git-safe JSON summary contains identifiers,
hashes, paths, and outcomes only; it never contains source-question text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from source_content_question_segmentation import QuestionSegmentationError, SourceBlock, segment_questions
from word_com_content_extractor import WordComContentExtractionError, extract_document


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "dev" / "teaching_docs_dev.db"
DEFAULT_SOURCE_ROOT = ROOT / "<local-scratch>"
DEFAULT_EVIDENCE_DIR = ROOT / "data" / "dev" / "golden-samples" / "fidelity-001"
DEFAULT_SUMMARY = ROOT / "docs" / "fidelity_audit" / "FIDELITY-001_sample20.json"
DEFAULT_MARKDOWN = ROOT / "docs" / "fidelity_audit" / "FIDELITY-001_sample20.md"
SEED = 20260804
SAMPLE_SIZE = 20
OUTCOMES = ("一致", "可接受差异", "疑似损耗", "无法比较")


class FidelitySampleAuditError(RuntimeError):
    """Raised when the readonly audit cannot establish a safe boundary."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def normalized(value: str | None) -> str:
    return "".join((value or "").replace("\u00a0", " ").split())


def resolve_under_root(root: Path, relative_path: str) -> Path:
    """Resolve a database relative path without allowing a path escape."""
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise FidelitySampleAuditError("source_path_escapes_configured_root") from exc
    return candidate


def readonly_connection(path: Path) -> sqlite3.Connection:
    resolved = path.resolve(strict=True)
    conn = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def choose_sample(rows: Iterable[dict[str, Any]], *, seed: int = SEED, size: int = SAMPLE_SIZE) -> list[dict[str, Any]]:
    """Choose a stable, coverage-oriented sample without using validation status.

    The strata are source/evidence facts: source asset evidence, formula asset
    evidence, question type, answer-evidence absence, and ordinary controls.
    A row may qualify for several strata but is selected once only.
    """
    candidates = [dict(row) for row in rows]
    if len(candidates) < size:
        raise FidelitySampleAuditError(f"sample_pool_too_small:{len(candidates)}<{size}")
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    plans = (
        ("source_asset", 4, lambda row: int(row["source_asset_count"]) > 0),
        ("math_symbol", 3, lambda row: int(row["math_signal_count"]) > 0),
        ("choice", 3, lambda row: row["question_type"] == "选择题"),
        ("fill", 2, lambda row: row["question_type"] == "填空题"),
        ("solution", 2, lambda row: row["question_type"] in {"解答题", "计算题"}),
        ("answer_evidence_missing", 3, lambda row: int(row["answer_evidence_count"]) == 0),
        ("ordinary_control", 3, lambda row: int(row["source_asset_count"]) == 0 and int(row["math_signal_count"]) == 0),
    )
    for stratum, wanted, predicate in plans:
        eligible = [row for row in candidates if row["id"] not in selected_ids and predicate(row)]
        eligible.sort(key=lambda row: row["id"])
        rng.shuffle(eligible)
        for row in eligible[:wanted]:
            row["sample_stratum"] = stratum
            selected.append(row)
            selected_ids.add(row["id"])
    if len(selected) < size:
        remaining = [row for row in candidates if row["id"] not in selected_ids]
        remaining.sort(key=lambda row: row["id"])
        rng.shuffle(remaining)
        for row in remaining[: size - len(selected)]:
            row["sample_stratum"] = "coverage_fill"
            selected.append(row)
            selected_ids.add(row["id"])
    if len(selected) != size:
        raise FidelitySampleAuditError(f"sample_selection_failed:{len(selected)}")
    return selected


def source_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return only questions with structured source-content evidence."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT q.id, q.question_type, q.source_document_id, q.source_question_no,
                   sd.relative_path, sd.file_hash, sd.file_type,
                   COUNT(DISTINCT qsae.id) AS source_asset_count,
                   COUNT(DISTINCT CASE WHEN sca.asset_kind='formula' THEN qsae.id END) AS formula_asset_count,
                   MAX(CASE WHEN cie.evidence_text LIKE '%=%'
                                  OR cie.evidence_text LIKE '%√%'
                                  OR cie.evidence_text LIKE '%π%'
                                  OR cie.evidence_text LIKE '%∠%'
                                  OR cie.evidence_text LIKE '%^%'
                            THEN 1 ELSE 0 END) AS math_signal_count,
                   COUNT(DISTINCT CASE WHEN qie.field_name='answer' THEN qie.id END) AS answer_evidence_count
              FROM questions q
              JOIN source_documents sd ON sd.id=q.source_document_id
              JOIN content_item_question_links ciql ON ciql.question_id=q.id
              JOIN content_items ci ON ci.id=ciql.content_item_id
              JOIN content_source_versions csv ON csv.id=ci.source_version_id
              JOIN content_extraction_runs cer ON cer.source_version_id=csv.id
              JOIN source_content_blocks scb ON scb.extraction_run_id=cer.id
              JOIN content_item_evidence cie
                ON cie.content_item_id=ci.id AND cie.source_block_id=scb.id
              LEFT JOIN question_source_asset_evidence qsae ON qsae.question_id=q.id
              LEFT JOIN source_content_assets sca ON sca.id=qsae.source_content_asset_id
              LEFT JOIN question_internal_evidence qie ON qie.question_id=q.id
             WHERE sd.trusted_source=1 AND sd.file_type IN ('doc', 'docx')
             GROUP BY q.id
             HAVING COUNT(DISTINCT cie.id)>0
             ORDER BY q.id
            """
        ).fetchall()
    ]


def database_question(conn: sqlite3.Connection, question_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT q.id, q.stem, q.options_json, q.answer, q.analysis, q.question_type,
               q.source_document_id, q.source_question_no, q.source_fragment_id,
               sd.relative_path, sd.file_hash, sd.file_type,
               ci.id AS content_item_id, ci.source_version_id, ci.student_payload_json
          FROM questions q
          JOIN source_documents sd ON sd.id=q.source_document_id
          LEFT JOIN content_item_question_links ciql ON ciql.question_id=q.id
          LEFT JOIN content_items ci ON ci.id=ciql.content_item_id
         WHERE q.id=?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        raise FidelitySampleAuditError("question_missing_after_selection")
    return dict(row)


def question_evidence(conn: sqlite3.Connection, question_id: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"student": [], "internal": [], "assets": []}
    for row in conn.execute(
        """
        SELECT cie.field_name, cie.visibility, cie.source_block_id, cie.character_range_json,
               cie.evidence_text, cie.evidence_sha256, scb.ordinal, scb.locator_json,
               scb.raw_text, scb.raw_sha256
          FROM content_item_question_links ciql
          JOIN content_item_evidence cie ON cie.content_item_id=ciql.content_item_id
          JOIN source_content_blocks scb ON scb.id=cie.source_block_id
         WHERE ciql.question_id=?
         ORDER BY cie.visibility, cie.field_name, scb.ordinal, cie.id
        """,
        (question_id,),
    ):
        result["student" if row["visibility"] == "student" else "internal"].append(dict(row))
    for row in conn.execute(
        """
        SELECT qie.field_name, qie.source_block_id, qie.character_range_json,
               qie.internal_payload, scb.ordinal, scb.locator_json, scb.raw_text, scb.raw_sha256
          FROM question_internal_evidence qie
          JOIN source_content_blocks scb ON scb.id=qie.source_block_id
         WHERE qie.question_id=? AND qie.field_name IN ('answer', 'analysis')
         ORDER BY qie.field_name, scb.ordinal, qie.id
        """,
        (question_id,),
    ):
        item = dict(row)
        try:
            start, end = json.loads(str(item["character_range_json"]))
            item["evidence_text"] = str(item["raw_text"])[int(start):int(end)]
        except (TypeError, ValueError, json.JSONDecodeError):
            item["evidence_text"] = ""
        result["internal"].append(item)
    for row in conn.execute(
        """
        SELECT qsae.option_label, qsae.position, sca.asset_kind, sca.locator_json,
               sca.extraction_status, scb.id AS source_block_id, scb.locator_json AS source_block_locator
          FROM question_source_asset_evidence qsae
          JOIN source_content_assets sca ON sca.id=qsae.source_content_asset_id
          JOIN source_content_blocks scb ON scb.id=qsae.source_block_id
         WHERE qsae.question_id=? ORDER BY qsae.position, qsae.id
        """,
        (question_id,),
    ):
        result["assets"].append(dict(row))
    return result


def live_block_index(extraction: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {json.dumps(item["locator"], sort_keys=True, ensure_ascii=False): item for item in extraction["blocks"]}


def outcome_from_checks(checks: Iterable[str]) -> str:
    values = list(checks)
    if "疑似损耗" in values:
        return "疑似损耗"
    if values and all(value == "无法比较" for value in values):
        return "无法比较"
    if "可接受差异" in values:
        return "可接受差异"
    if "无法比较" in values:
        return "无法比较"
    return "一致"


def field_status(stored: str | None, evidence: list[dict[str, Any]], field_name: str) -> str:
    relevant = [item for item in evidence if item["field_name"] == field_name]
    if not relevant:
        return "无法比较"
    source = "".join(item["evidence_text"] for item in relevant)
    if not normalized(stored):
        return "疑似损耗"
    return "一致" if normalized(stored) in normalized(source) else "疑似损耗"


def option_status(options_json: str, evidence: list[dict[str, Any]]) -> str:
    relevant = [item for item in evidence if item["field_name"] == "options"]
    if not relevant:
        return "无法比较"
    try:
        options = json.loads(options_json)
    except json.JSONDecodeError:
        return "疑似损耗"
    if not isinstance(options, list):
        return "疑似损耗"
    source = normalized("".join(item["evidence_text"] for item in relevant))
    if not options:
        return "一致"
    rendered = [
        str(item.get("text") if isinstance(item, dict) else item)
        for item in options
    ]
    return "一致" if all(normalized(item) in source for item in rendered) else "疑似损耗"


def internal_payloads(conn: sqlite3.Connection, question_id: str) -> dict[str, str | None]:
    values: dict[str, str | None] = {"answer": None, "analysis": None}
    for row in conn.execute(
        """
        SELECT field_name, internal_payload FROM question_internal_evidence
         WHERE question_id=? AND field_name IN ('answer', 'analysis')
         ORDER BY created_at DESC, id DESC
        """,
        (question_id,),
    ):
        if values[row["field_name"]] is None:
            values[row["field_name"]] = row["internal_payload"]
    return values


def assess_question(conn: sqlite3.Connection, selected: dict[str, Any], source_root: Path, *, word_retries: int) -> dict[str, Any]:
    question = database_question(conn, str(selected["id"]))
    evidence = question_evidence(conn, question["id"])
    internal = internal_payloads(conn, question["id"])
    source_path = resolve_under_root(source_root, str(question["relative_path"]))
    checks: dict[str, dict[str, Any]] = {}
    try:
        payload = json.loads(question["student_payload_json"] or "{}")
        payload_stem = str(payload.get("stem") or "")
        payload_options = payload.get("options")
        intermediate = "一致" if normalized(payload_stem) == normalized(question["stem"]) and payload_options is not None else "疑似损耗"
        intermediate_detail = "questions -> content_items.student_payload_json"
    except json.JSONDecodeError:
        intermediate = "疑似损耗"
        intermediate_detail = "content item payload is invalid JSON"
    question_asset_count = conn.execute("SELECT COUNT(*) FROM question_assets WHERE question_id=?", (question["id"],)).fetchone()[0]
    extraction: dict[str, Any] | None = None
    source_error: str | None = None
    if not source_path.is_file():
        source_error = "source_file_missing"
    elif sha256_file(source_path) != str(question["file_hash"]).upper():
        source_error = "source_file_hash_mismatch"
    else:
        errors: list[str] = []
        for attempt in range(word_retries):
            try:
                extraction = extract_document(source_path)
                break
            except (OSError, WordComContentExtractionError) as exc:
                errors.append(type(exc).__name__)
                if attempt + 1 < word_retries:
                    time.sleep(1)
        if extraction is None:
            source_error = f"word_com_read_failed:{','.join(errors)}"

    if extraction is None:
        for name in ("数量编号顺序", "题干完整性", "公式特殊符号", "图片表格绑定", "选项文本顺序", "答案解析", "可追溯性", "中间输出回读"):
            checks[name] = {"outcome": "无法比较", "detail": source_error or "source_not_available"}
        checks["中间输出回读"] = {"outcome": intermediate, "detail": intermediate_detail}
    else:
        live = live_block_index(extraction)
        all_evidence = evidence["student"] + evidence["internal"]
        locator_matches = [
            item for item in all_evidence
            if json.dumps(json.loads(item["locator_json"]), sort_keys=True, ensure_ascii=False) in live
            and live[json.dumps(json.loads(item["locator_json"]), sort_keys=True, ensure_ascii=False)]["raw_sha256"].upper()
            == str(item["raw_sha256"]).upper()
        ]
        trace = "一致" if all_evidence and len(locator_matches) == len(all_evidence) else "疑似损耗"
        checks["可追溯性"] = {"outcome": trace, "detail": f"live_locator_hash_matches={len(locator_matches)}/{len(all_evidence)}"}

        try:
            candidates = segment_questions(
                [SourceBlock(item["locator"].get("block_id", str(item["ordinal"])), item["ordinal"], item["raw_text"]) for item in extraction["blocks"]]
            )
            source_numbers = [candidate.source_question_no for candidate in candidates]
            db_numbers = [
                row[0] for row in conn.execute(
                    "SELECT q.source_question_no FROM questions q WHERE q.source_document_id=? ORDER BY CAST(q.source_question_no AS INTEGER), q.id",
                    (question["source_document_id"],),
                )
            ]
            number_status = "一致" if source_numbers == db_numbers else "疑似损耗"
            checks["数量编号顺序"] = {"outcome": number_status, "detail": f"source={len(source_numbers)} database={len(db_numbers)}"}
        except QuestionSegmentationError as exc:
            checks["数量编号顺序"] = {"outcome": "无法比较", "detail": f"source_segmentation_deferred:{exc}"}

        stem_status = field_status(question["stem"], evidence["student"], "stem")
        checks["题干完整性"] = {"outcome": stem_status, "detail": "student stem evidence normalized containment"}
        formula_sources = [item for item in evidence["assets"] if item["asset_kind"] == "formula"]
        if not formula_sources:
            checks["公式特殊符号"] = {"outcome": "无法比较", "detail": "no source formula asset evidence for this question"}
        else:
            checks["公式特殊符号"] = {"outcome": "无法比较", "detail": "Word COM confirms formula locator only; serialized formula expression comparison is not implemented"}
        if evidence["assets"]:
            checks["图片表格绑定"] = {"outcome": "一致" if trace == "一致" else "疑似损耗", "detail": f"source_asset_evidence={len(evidence['assets'])}; database question_assets are audited separately"}
        else:
            checks["图片表格绑定"] = {"outcome": "无法比较", "detail": "no source asset evidence for this question"}
        options_status = option_status(question["options_json"], evidence["student"])
        checks["选项文本顺序"] = {"outcome": options_status, "detail": "structured option payload normalized containment"}
        answer_status = field_status(internal["answer"], evidence["internal"], "answer")
        analysis_status = field_status(internal["analysis"], evidence["internal"], "analysis")
        if answer_status == "疑似损耗" or analysis_status == "疑似损耗":
            answer_outcome = "疑似损耗"
        elif answer_status == "无法比较" and analysis_status == "无法比较":
            answer_outcome = "无法比较"
        else:
            answer_outcome = "一致"
        checks["答案解析"] = {"outcome": answer_outcome, "detail": f"answer={answer_status}; analysis={analysis_status}"}
        checks["中间输出回读"] = {"outcome": intermediate, "detail": intermediate_detail}

    if evidence["assets"] and question_asset_count == 0:
        checks["图片表格绑定"] = {"outcome": "疑似损耗", "detail": f"source_asset_evidence={len(evidence['assets'])}; question_assets=0"}
    return {
        "question_id": question["id"],
        "source_version_id": question["source_version_id"],
        "source_relative_path": question["relative_path"],
        "source_question_no": question["source_question_no"],
        "question_type": question["question_type"],
        "sample_stratum": selected["sample_stratum"],
        "source_asset_evidence_count": len(evidence["assets"]),
        "question_asset_count": question_asset_count,
        "source_file_sha256": str(question["file_hash"]).upper(),
        "checks": checks,
        "overall_outcome": outcome_from_checks(item["outcome"] for item in checks.values()),
        "local_evidence": {
            "question": question,
            "internal_answer_analysis": internal,
            "evidence": evidence,
            "source_file_read_status": source_error or "read_and_hash_matched",
            "checks": checks,
        },
    }


def public_record(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "local_evidence"}


def render_markdown(results: list[dict[str, Any]], *, db_hash: str, evidence_path: Path, evidence_hash: str) -> str:
    counts = Counter(result["overall_outcome"] for result in results)
    lines = [
        "# FIDELITY-001 20 题抽样对照摘要",
        "",
        "本文件不含题目原文、答案、解析或图片。完整逐题原始证据仅保存在本机已忽略路径。",
        "",
        "- 固定种子：`20260804`",
        f"- 样本数：`{len(results)}`",
        f"- 数据库 SHA-256：`{db_hash}`",
        f"- 本地完整证据：`{evidence_path}`",
        f"- 本地完整证据 SHA-256：`{evidence_hash}`",
        f"- 分类：一致 `{counts['一致']}`；可接受差异 `{counts['可接受差异']}`；疑似损耗 `{counts['疑似损耗']}`；无法比较 `{counts['无法比较']}`。",
        "",
        "## 逐题结果",
        "",
    ]
    for index, result in enumerate(results, start=1):
        lines.extend((
            f"### {index}. `{result['question_id']}`",
            "",
            f"- 来源：`{result['source_relative_path']}`；源题号：`{result['source_question_no']}`；`source_version_id`：`{result['source_version_id']}`。",
            f"- 分层：`{result['sample_stratum']}`；题型：`{result['question_type']}`；源资产证据：`{result['source_asset_evidence_count']}`；`question_assets`：`{result['question_asset_count']}`。",
            f"- 原件观察：源文件 SHA-256 与数据库记录一致；当前 Word COM 读取未建立可用会话，因此未将原件文本、公式或版式比较写为通过。",
            f"- 数据库观察：`questions -> content_items.student_payload_json` 回读为 `{result['checks']['中间输出回读']['outcome']}`。",
            f"- 总体：**{result['overall_outcome']}**。",
            "",
            "| 维度 | 结论 | 证据摘要 |",
            "| --- | --- | --- |",
        ))
        for name, check in result["checks"].items():
            lines.append(f"| {name} | {check['outcome']} | {check['detail']} |")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_outputs(results: list[dict[str, Any]], evidence_dir: Path, summary_path: Path, markdown_path: Path, *, db_hash: str) -> tuple[Path, str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / "FIDELITY-001_sample20_evidence.json"
    local_payload = {"schema": "fidelity-001-local-evidence-v1", "database_sha256": db_hash, "samples": results}
    evidence_path.write_text(json.dumps(local_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    public_payload = {
        "schema": "fidelity-001-public-summary-v1",
        "seed": SEED,
        "sample_size": len(results),
        "database_sha256": db_hash,
        "outcome_counts": dict(Counter(result["overall_outcome"] for result in results)),
        "samples": [public_record(result) for result in results],
        "source_text_included": False,
    }
    summary_path.write_text(json.dumps(public_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(
        render_markdown(results, db_hash=db_hash, evidence_path=evidence_path, evidence_hash=sha256_file(evidence_path)),
        encoding="utf-8",
    )
    return evidence_path, sha256_file(evidence_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--word-retries", type=int, default=2, choices=range(1, 4))
    args = parser.parse_args()
    database_hash_before = sha256_file(args.db)
    source_root = args.source_root.resolve(strict=True)
    if source_root != DEFAULT_SOURCE_ROOT.resolve():
        try:
            source_root.relative_to(ROOT)
        except ValueError as exc:
            raise FidelitySampleAuditError("source_root_outside_workspace") from exc
    conn = readonly_connection(args.db)
    try:
        required = ("questions", "content_item_evidence", "question_internal_evidence", "question_source_asset_evidence")
        missing = [name for name in required if not has_table(conn, name)]
        if missing:
            raise FidelitySampleAuditError("required_tables_missing:" + ",".join(missing))
        selected = choose_sample(source_candidates(conn), seed=args.seed)
        results = [assess_question(conn, row, source_root, word_retries=args.word_retries) for row in selected]
    finally:
        conn.close()
    database_hash_after = sha256_file(args.db)
    if database_hash_after != database_hash_before:
        raise FidelitySampleAuditError("database_changed_during_readonly_audit")
    evidence_path, evidence_hash = write_outputs(results, args.evidence_dir, args.summary, args.markdown, db_hash=database_hash_after)
    print(json.dumps({
        "samples": len(results),
        "outcomes": dict(Counter(result["overall_outcome"] for result in results)),
        "database_sha256": database_hash_after,
        "local_evidence_path": str(evidence_path),
        "local_evidence_sha256": evidence_hash,
        "summary_path": str(args.summary),
        "markdown_path": str(args.markdown),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
