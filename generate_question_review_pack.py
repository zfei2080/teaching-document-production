"""Generate a human-readable review pack for pending source-faithful questions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"
OUTPUT = ROOT / "docs" / "B3_第一批题目逐题审核包_v1.md"


def source_text(conn: sqlite3.Connection, question_id: str, field_name: str) -> str:
    rows = conn.execute(
        """
        SELECT fragment.raw_text
        FROM question_source_fragments provenance
        JOIN source_fragments fragment ON fragment.id = provenance.source_fragment_id
        WHERE provenance.question_id=? AND provenance.field_name=?
        ORDER BY fragment.paragraph_index
        """,
        (question_id, field_name),
    ).fetchall()
    return "\n".join(row[0] for row in rows)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, source_question_no, stem, options_json, answer, analysis,
                   question_type, quality_status, review_status
            FROM questions
            WHERE quality_status='needs_review' AND review_status='pending'
            ORDER BY CAST(source_question_no AS INTEGER)
            """
        ).fetchall()
        lines = [
            "# B3 第一批题目逐题审核包 v1",
            "",
            "用途：对来源保真和机械一致性均通过的候选题进行逐题人工审核。",
            "本文件不是批准记录，不能代替 `question_reviews` 中的五项带依据审核。",
            "",
            "## 审核顺序",
            "",
            "1. 对照原始字段与结构化字段，确认没有遗漏或错位。",
            "2. 验证答案和解析的数学正确性。",
            "3. 判断题型、难度、教学用途、学段和教材范围。",
            "4. 图形题确认图形与题干条件一致。",
            "5. 核验来源和使用权限。",
            "",
        ]
        for row in rows:
            options = json.loads(row["options_json"])
            number = row["source_question_no"]
            lines.extend(
                [
                    f"## 第 {number} 题：{row['id']}",
                    "",
                    f"- 当前状态：`{row['quality_status']} / {row['review_status']}`",
                    f"- 当前题型：{row['question_type']}",
                    "",
                    "### 结构化题目",
                    "",
                    row["stem"],
                    "",
                ]
            )
            if options:
                lines.extend(options)
                lines.append("")
            lines.extend(
                [
                    "### 原始字段对照",
                    "",
                    "**题干/选项原文：**",
                    "",
                    source_text(conn, row["id"], "stem") + "\n" + source_text(conn, row["id"], "options"),
                    "",
                    "**标准答案：**",
                    "",
                    row["answer"],
                    "",
                    "**答案原文：**",
                    "",
                    source_text(conn, row["id"], "answer"),
                    "",
                    "**解析：**",
                    "",
                    row["analysis"],
                    "",
                    "### 审核记录要求",
                    "",
                    "- 数学正确性：待审核",
                    "- 教学适配：待审核",
                    "- 教材范围：待审核",
                    "- 图形匹配：待审核",
                    "- 来源与版权：待审核",
                    "",
                ]
            )
    finally:
        conn.close()

    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"PENDING_QUESTIONS={len(rows)}")
    print(f"REVIEW_PACK={OUTPUT.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
