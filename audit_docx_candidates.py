"""Create a review-only inventory for deterministic DOCX question candidates."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from candidate_validation import validate_candidate
from docx_candidate_parser import parse_candidates

ROOT = Path(__file__).parent
SOURCE_DOC = ROOT / "data" / "dev" / "golden-samples" / "golden_source_001.docx"
OUTPUT_JSON = ROOT / "data" / "dev" / "golden-samples" / "candidate_inventory.json"
OUTPUT_MD = ROOT / "docs" / "B2_候选题解析审计_v1.md"


def main() -> None:
    candidates = parse_candidates(SOURCE_DOC)
    items = []
    for candidate in candidates:
        validation = validate_candidate(candidate)
        items.append(
            {
                "number": candidate.number,
                "stem_fragment_ids": candidate.stem_fragment_ids,
                "answer_fragment_id": candidate.answer_fragment_id,
                "knowledge_fragment_id": candidate.knowledge_fragment_id,
                "analysis_fragment_ids": candidate.analysis_fragment_ids,
                "has_answer": bool(candidate.answer),
                "has_knowledge": bool(candidate.knowledge_text),
                "has_analysis": bool(candidate.analysis_text),
                "eligible_for_review": validation.eligible_for_review,
                "blockers": list(validation.reasons),
                "stem_preview": candidate.stem_text[:160],
            }
        )

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    eligible = [item for item in items if item["eligible_for_review"]]
    blocked = [item for item in items if not item["eligible_for_review"]]
    lines = [
        "# B2 候选题解析审计 v1",
        "",
        "来源：`golden_source_001.docx`。本报告只生成候选清单，不写入可交付题库。",
        "",
        "## 结果",
        "",
        f"- 识别题号：{len(items)} 道（1 至 {len(items)}）。",
        f"- 可进入人工审核候选：{len(eligible)} 道。",
        f"- 阻断迁移：{len(blocked)} 道。",
        "- 自动资格仅代表题干、答案、知识点、解析及来源片段齐全；不代表数学正确、教材适配或可交付。",
        "",
        "## 可进入人工审核候选",
        "",
        "| 题号 | 来源题干段 | 答案段 | 知识点段 | 解析段数 |",
        "|---:|---|---:|---:|---:|",
    ]
    for item in eligible:
        lines.append(
            f"| {item['number']} | {','.join(map(str, item['stem_fragment_ids']))} | "
            f"{item['answer_fragment_id']} | {item['knowledge_fragment_id']} | "
            f"{len(item['analysis_fragment_ids'])} |"
        )
    lines.extend([
        "",
        "## 阻断迁移候选",
        "",
        "| 题号 | 阻断原因 |",
        "|---:|---|",
    ])
    for item in blocked:
        lines.append(f"| {item['number']} | {'；'.join(item['blockers'])} |")
    lines.extend([
        "",
        "## 处理规则",
        "",
        "1. 本批仅允许前 20 道客观题进入人工审核候选队列。",
        "2. 21 题及以后包含主观题、多小问、跨段或不规则答案版式，当前不自动迁移。",
        "3. 阻断迁移并不是丢弃；待建立主观题答案、评分细则和多段图形关联解析器后重新处理。",
        "4. 所有候选在人工审核通过前均不得选题、组卷或交付。",
    ])
    OUTPUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"CANDIDATES={len(items)}")
    print(f"REVIEW_ELIGIBLE={len(eligible)}")
    print(f"BLOCKED={len(blocked)}")
    print(f"INVENTORY={OUTPUT_JSON.relative_to(ROOT).as_posix()}")
    print(f"REPORT={OUTPUT_MD.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
