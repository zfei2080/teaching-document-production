"""Generate mechanical consistency results for the pending review queue."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from candidate_consistency import all_pass, check_candidate_consistency

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"
OUTPUT_JSON = ROOT / "data" / "dev" / "golden-samples" / "consistency_audit.json"
OUTPUT_MD = ROOT / "docs" / "B3_候选题机械一致性审计_v1.md"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, source_question_no, question_type, options_json, answer
            FROM questions
            WHERE quality_status='needs_review' AND review_status='pending'
            ORDER BY CAST(source_question_no AS INTEGER)
            """
        ).fetchall()
        results = []
        for row in rows:
            assets = [
                str(ROOT / item[0])
                for item in conn.execute(
                    "SELECT relative_path FROM question_assets WHERE question_id=? AND asset_type='image'",
                    (row["id"],),
                )
                if item[0]
            ]
            findings = check_candidate_consistency(
                question_type=row["question_type"],
                options_json=row["options_json"],
                answer=row["answer"],
                asset_paths=assets,
            )
            results.append(
                {
                    "id": row["id"],
                    "number": row["source_question_no"],
                    "question_type": row["question_type"],
                    "passed": all_pass(findings),
                    "findings": [finding.__dict__ for finding in findings],
                }
            )
    finally:
        conn.close()

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = [item for item in results if item["passed"]]
    blocked = [item for item in results if not item["passed"]]
    lines = [
        "# B3 候选题机械一致性审计 v1",
        "",
        "本审计只检查答案存在性、选择题答案与选项对应、非选择题选项结构、已登记图形资产路径和题型标准化。",
        "它不证明数学正确性、不完成教材适配审核，也不改变题目审核状态。",
        "",
        f"- 待审核候选：{len(results)} 道。",
        f"- 机械一致性通过：{len(passed)} 道。",
        f"- 需修订/补标：{len(blocked)} 道。",
        "",
        "## 通过",
        "",
        "| 题号 | 候选 ID | 题型 |",
        "|---:|---|---|",
    ]
    for item in passed:
        lines.append(f"| {item['number']} | {item['id']} | {item['question_type']} |")
    lines.extend(["", "## 需修订或补标", "", "| 题号 | 候选 ID | 问题 |", "|---:|---|---|"])
    for item in blocked:
        details = "；".join(finding["detail"] for finding in item["findings"] if not finding["passed"])
        lines.append(f"| {item['number']} | {item['id']} | {details} |")
    lines.extend(
        [
            "",
            "## 后续处理",
            "",
            "1. 机械一致性失败的候选不得进入人工数学审核完成状态，先补齐题型或资产记录。",
            "2. 机械一致性通过的候选仍需完成数学正确性、教学适配、教材范围、图形匹配、来源版权五项审核。",
            "3. 本报告不产生 `approved` 题目，也不允许生成教学文档。",
        ]
    )
    OUTPUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"CANDIDATES={len(results)}")
    print(f"CONSISTENCY_PASSED={len(passed)}")
    print(f"CONSISTENCY_BLOCKED={len(blocked)}")
    print(f"REPORT={OUTPUT_MD.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
