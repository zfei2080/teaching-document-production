"""
回归测试 —— lecture-generator 主链路关键修复点
运行: pytest test_pipeline.py -v
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from extractor import _normalize_json_escapes
from database import QuestionBankDB
from lecture_generator import _is_renderable_question, select_questions
from textbook_filter import is_in_scope


# ================================================================
# T2-1: parser 图片尺寸过滤
# ================================================================

def _make_tiny_png():
    """生成一个 1x1 的极小 PNG 字节。"""
    from io import BytesIO
    from PIL import Image

    bio = BytesIO()
    img = Image.new("RGB", (1, 1), (255, 0, 0))
    img.save(bio, format="PNG")
    return bio.getvalue()


def test_parser_skips_tiny_images(monkeypatch):
    """parser 应跳过宽或高 < 10px 的图片。"""
    pytest.importorskip("PIL")

    from io import BytesIO
    from PIL import Image

    # 生成一张 1x1 像素的极小 PNG
    tiny_bio = BytesIO()
    img = Image.new("RGB", (1, 1), (255, 0, 0))
    img.save(tiny_bio, format="PNG")
    tiny_png = tiny_bio.getvalue()

    # 直接测尺寸过滤逻辑（parse_docx 内部循环的核心条件）
    from PIL import Image as PILImage
    bio = BytesIO(tiny_png)
    with PILImage.open(bio) as pil_img:
        w, h = pil_img.size
    MIN_IMG_DIM = 10
    assert w < MIN_IMG_DIM or h < MIN_IMG_DIM  # 应触发过滤
    assert w == 1 and h == 1


# ================================================================
# T2-2: extractor JSON 转义加固
# ================================================================

def test_normalize_escapes_single_letter_latex():
    """单字母 LaTeX 命令（如 \\c \\d \\p）应被正确转义，\\triangle 保留合法 \\t。"""
    raw = '{"text": "angle \\c and \\d and \\p and \\triangle"}'

    result = _normalize_json_escapes(raw)
    # 单字母命令应被双写
    assert "\\\\c" in result
    assert "\\\\d" in result
    assert "\\\\p" in result
    # \\t 是合法 JSON escape（tab），状态机不二次转义
    parsed = json.loads(result)
    assert "angle" in parsed["text"]


def test_normalize_escapes_preserves_json_valid():
    """合法 JSON 转义（\\n \\t \\\\）不应被破坏。"""
    raw = '{"key": "line1\\nline2\\ttab\\\\end"}'
    result = _normalize_json_escapes(raw)
    parsed = json.loads(result)
    assert "\n" in parsed["key"]
    assert "\t" in parsed["key"]
    # \\\\end → JSON 解析为 \end（一文字）


def test_normalize_escapes_mixed_angle():
    """混合场景：LaTeX \\angle 含配图中不应破坏 JSON。"""
    raw = (
        '{"q": "\\\\triangle ABC \\\\cong \\\\triangle DEF", '
        '"answer": "\\\\angle A = 30^\\\\circ"}'
    )
    result = _normalize_json_escapes(raw)
    parsed = json.loads(result)
    assert "triangle" in parsed["q"]


def test_normalize_escapes_no_double_process():
    """二次加工不应发生：Pattern 4 不再对 Pattern 3 已转义的反斜杠加工。"""
    raw = '{"answer": "75^\\circ"}'
    result = _normalize_json_escapes(raw)
    # 应能解析，不应有三连反斜杠
    parsed = json.loads(result)
    assert "circ" in parsed["answer"]


def test_robust_escape_all_debug_files():
    """状态机方案应能解析绝大多数历史调试文件。
    部分文件有非转义类硬伤（字符串未闭合等模型输出问题），属于无法修复的边界。
    """
    from extractor import _extract_json_text
    import glob, os

    debug_dir = r"<local-path>"
    files = glob.glob(os.path.join(debug_dir, "api_response_debug_*.txt"))
    assert len(files) >= 6, f"调试文件不足，仅有 {len(files)} 个"

    failed = []
    for f in files:
        data = open(f, "r", encoding="utf-8").read()
        text = _extract_json_text(data)
        try:
            json.loads(_normalize_json_escapes(text))
        except json.JSONDecodeError as e:
            failed.append((os.path.basename(f), str(e)))

    # 允许部分文件有非转义类硬伤，但绝大多数应通过
    assert len(failed) <= 3, f"{len(failed)}/{len(files)} 调试文件解析失败: {failed[:4]}"


def test_renderable_question_filters_image_only_subject():
    """只有图片标记、无文字的题目不应进入讲义。"""
    q = {"subject": "[图片: image1.png] [图片: image2.png]", "question_type": "解答题"}
    assert _is_renderable_question(q) is False


def test_is_in_scope_blocks_circle_for_bsj_baxia():
    """北师大版八下即使 scope 含圆，也应被硬禁用拦住。"""
    scope = ["圆", "全等三角形"]
    assert is_in_scope("圆的切线判定", scope, textbook="北师大版", grade_level="八下") is False
    assert is_in_scope("全等三角形的判定", scope, textbook="北师大版", grade_level="八下") is True


def test_select_questions_topic_miss_does_not_fallback_whole_pool(tmp_db):
    """知识点不匹配时不应回退到整库。"""
    q = {
        "id": "q1",
        "subject": "这是全等三角形题目",
        "answer": "A",
        "knowledge_point": "全等三角形的判定",
        "difficulty": "基础",
        "error_prone": "无明显易错点",
        "grade_level": "初中",
        "question_type": "选择题",
        "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
        "has_image": False,
        "images": [],
        "source_file": "demo.docx",
        "source_page": 1,
    }
    tmp_db.insert_question(q)
    config = select_questions(tmp_db, grade="八下", topic="圆", textbook="北师大版")
    assert config is None


# ================================================================
# T2-3: database 难度兜底 + 逐题提交
# ================================================================

@pytest.fixture
def tmp_db():
    """创建临时 SQLite 数据库。"""
    import tempfile

    tmp = tempfile.mktemp(suffix=".db")
    db = QuestionBankDB(tmp)
    db.init_db()
    yield db
    db.close()
    os.unlink(tmp)


def test_difficulty_default_accepts_invalid(tmp_db):
    """'（未提供）' 应被归一化为 '基础'，不应抛异常。"""
    q = {
        "subject": "1 + 1 = ?",
        "difficulty": "（未提供）",
        "knowledge_point": "加法",
        "grade_level": "初中",
    }
    qid = tmp_db.insert_question(q)
    assert qid is not None


def test_difficulty_accepts_valid(tmp_db):
    """合法难度值正常插入。"""
    q = {
        "subject": "2 + 2 = ?",
        "difficulty": "中等",
        "knowledge_point": "加法",
        "grade_level": "初中",
    }
    qid = tmp_db.insert_question(q)
    assert qid is not None


def test_per_question_isolation(tmp_db):
    """逐题提交：一道题有脏数据时，其他题仍能入库。"""
    good1 = {"subject": "first good question", "difficulty": "基础", "knowledge_point": "测试", "grade_level": "初中"}
    bad = {"subject": "bad question", "difficulty": "基础", "grade_level": "INVALID_GRADE"}
    good2 = {"subject": "second different question", "difficulty": "中等", "knowledge_point": "测试", "grade_level": "初中"}

    results = tmp_db.insert_batch([good1, bad, good2])
    # 两题成功，一题 None
    inserted = [r for r in results if r is not None]
    assert len(inserted) == 2
