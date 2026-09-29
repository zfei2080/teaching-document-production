"""
讲义自动化生成系统 - 主入口

用法:
    python main.py <文件路径> [--dry-run] [--max-questions N]

功能:
    1. 自动判断文件类型（.docx / .pdf）
    2. 解析文件内容（文本提取或图片渲染）
    3. 调用 AI 提取结构化题目
    4. 输出 JSON 到 output/ 目录
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from config import OUTPUT_DIR
from parser import parse_file
from extractor import extract_questions

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="讲义自动化生成系统 - 从 DOC/PDF 中提取结构化数学题目"
    )
    parser.add_argument(
        "file",
        type=str,
        help="输入文件路径（支持 .docx 和 .pdf）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式：只解析不调用 AI，检查解析结果",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=None,
        help="最多提取的题目数（控制 API 成本）",
    )
    parser.add_argument(
        "--grade",
        type=str,
        default="",
        help="目标学段（如 七上、八下、高一上、小升初、初中 等，完整列表见 textbook_filter.ALL_GRADE_LEVELS）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="自定义输出 JSON 文件路径（默认: output/<源文件名>_questions.json）",
    )

    args = parser.parse_args()

    # 1. 检查文件
    filepath = args.file
    if not os.path.exists(filepath):
        logger.error(f"文件不存在: {filepath}")
        sys.exit(1)

    logger.info(f"输入文件: {filepath}")
    logger.info(f"模式: {'试运行 (dry-run)' if args.dry_run else '正式运行'}")

    # 2. 解析文件
    try:
        parsed_data = parse_file(filepath)
    except Exception as e:
        logger.error(f"文件解析失败: {e}")
        sys.exit(1)

    logger.info(f"解析完成: type={parsed_data['type']}, pages={parsed_data['total_pages']}")

    if parsed_data["type"] == "text":
        logger.info(f"文本预览:\n{parsed_data['content'][:500]}...")
    else:
        logger.info(f"图片数量: {len(parsed_data['images'])}")
        for img in parsed_data["images"][:3]:
            logger.info(f"  - {img}")
        if len(parsed_data["images"]) > 3:
            logger.info(f"  ... 共 {len(parsed_data['images'])} 张")

    # 3. Dry-run 模式：只解析，不调 AI
    if args.dry_run:
        logger.info("试运行完成，解析结果正常。退出。")
        # 保存解析结果供检查
        stem = Path(filepath).stem
        parse_output = os.path.join(OUTPUT_DIR, f"{stem}_parsed.json")
        serializable = {
            k: v for k, v in parsed_data.items()
            if k != "images"
        }
        serializable["image_count"] = len(parsed_data["images"])
        with open(parse_output, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
        logger.info(f"解析结果已保存到: {parse_output}")
        return

    # 4. AI 提取题目
    logger.info("开始 AI 题目提取...")
    try:
        questions = extract_questions(parsed_data, max_questions=args.max_questions, grade=args.grade)
    except Exception as e:
        logger.error(f"题目提取失败: {e}")
        sys.exit(1)

    # 5. 输出结果
    stem = Path(filepath).stem
    output_path = args.output or os.path.join(OUTPUT_DIR, f"{stem}_questions.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)

    logger.info(f"提取完成！共 {len(questions)} 道题")
    logger.info(f"结果已保存到: {output_path}")

    # 打印摘要
    print("\n" + "=" * 60)
    print(f"提取结果摘要 - {stem}")
    print("=" * 60)
    for i, q in enumerate(questions, 1):
        print(f"\n--- 题目 {i} ---")
        print(f"ID:       {q['id']}")
        print(f"题目:     {q['subject'][:80]}{'...' if len(q.get('subject', '')) > 80 else ''}")
        print(f"答案:     {q.get('answer', 'N/A')}")
        print(f"知识点:   {q.get('knowledge_point', 'N/A')}")
        print(f"难度:     {q.get('difficulty', 'N/A')}")
        print(f"易错点:   {q.get('error_prone', 'N/A')}")
        print(f"页码:     {q.get('source_page', 'N/A')}")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
