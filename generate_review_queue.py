"""Generate a review-only queue for source-faithful candidate questions.

This script does not approve questions. It exposes the remaining human review
work needed before any candidate can become eligible for teaching documents.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"
OUTPUT = ROOT / "docs" / "B2_人工审核队列_v1.md"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT q.id, q.source_question_no, q.question_type, q.difficulty,
                   q.quality_status, q.review_status, q.stem, q.answer,
                   q.options_json, q.analysis
            FROM questions q
            WHERE q.quality_status = 'needs_review' AND q.review_status = 'pending'
            ORDER BY CAST(q.source_question_no AS INTEGER)
            """
        ).fetchall()
    finally:
        conn.close()

    lines = [
        "# B2 人工审核队列 v1",
        "",
        "本队列中的题目已通过字段级来源保真审计，但尚未通过数学正确性、教学适配和教材范围审核。",
        "审核完成前，不得把任何题目状态改为 `approved`，不得用于讲义、练习或试卷交付。",
        "",
        "## 审核标准",
        "",
        "每道题必须逐项确认：",
        "",
        "- 题干、选项、图形与原始题目一致，且题意完整。",
        "- 标准答案正确，且与选项或填空结果一致。",
        "- 解析推理正确、无跳步造成的结论错误。",
        "- 知识点归类、题型、难度、学段和教材映射正确。",
        "- 图形题的图形与文字条件匹配。",
        "- 不存在版权、来源或题目重复风险。", 
        "",
        "## 当前队列",
        "",
        "| 题号 | 候选 ID | 题型 | 历史难度 | 答案 | 保真状态 | 数学审核 | 教学/教材审核 |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        options = json.loads(row["options_json"])
        source_fidelity = "通过"
        lines.append(
            f"| {row['source_question_no']} | {row['id']} | {row['question_type']} | "
            f"{row['difficulty'] or '待定'} | {row['answer']} | {source_fidelity} | 待审核 | 待审核 |"
        )
    lines.extend(
        [
            "",
            "## 审核结论写入规则",
            "",
            "- 全部审核项通过：可将题目改为 `quality_status=approved`、`review_status=approved`。",
            "- 任一项有问题：设为 `quality_status=blocked` 或 `review_status=rejected`，并记录问题和来源片段。",
            "- 未审核、结论不确定或需要补图补解析：维持 `needs_review/pending`。",
            "- 审核状态变化必须保留审核人、审核时间、依据和版本；该字段将在下一轮模型迁移中落库。",
        ]
    )
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"PENDING_REVIEW={len(rows)}")
    print(f"REVIEW_QUEUE={OUTPUT.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
