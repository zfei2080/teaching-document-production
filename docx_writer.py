"""
docx_writer.py — 讲义 Word 生成模块

流程：构建 Markdown → pandoc 转 docx（公式→OMML）→ python-docx 后处理样式
"""

import glob
import json
import os, re, subprocess, tempfile, uuid
from datetime import datetime
from PIL import Image
from docx import Document
from docx.shared import Pt, Cm, RGBColor, Emu
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml

# ============================================================================
# 常量
# ============================================================================
COLOR_TITLE = "0066CC"        # 封面蓝色标题
COLOR_SECTION = "376092"      # 板块标题深蓝
COLOR_SUB = "4472C4"          # 子标题蓝
COLOR_RED = "C00000"          # A层暗红
COLOR_ORANGE = "E46C0A"       # B层橙
COLOR_BLUE_C = "0070C0"       # C层亮蓝
COLOR_GRAY = "808080"         # 元数据灰
COLOR_HEADER = "666666"       # 页眉灰
COLOR_BORDER = "CCCCCC"       # 边框灰
COLOR_WHITE = "FFFFFF"
COLOR_TABLE_BG = "4472C4"     # 表头蓝
COLOR_REVIEW_BG = "E8F0FE"    # 复盘框浅蓝

FONT_W = "Calibri"
FONT_E = "Microsoft YaHei"
FONT_SONG = "SimSun"
FONT_TNR = "Times New Roman"

PAGE_W = Cm(21.0); PAGE_H = Cm(29.7); MARGIN = Cm(1.27)

# ============================================================================
# Markdown 构建
# ============================================================================

def _fmt_question(text: str) -> str:
    """格式化题目文本：把换行符替换为特殊标记 ⏎，后处理中转为真正换行。"""
    if not text:
        return ""
    text = text.replace("\\n", "\n")
    if "\n" not in text:
        return text
    return text.replace("\n", " ⏎ ")


def _strip_option_prefix(opt: str) -> str:
    """去掉选项文本里已有的 A./B./C. 前缀，避免渲染时重复。"""
    text = (opt or "").strip()
    return re.sub(r"^[A-F][\.、]\s*", "", text)


def _fmt_choice_question(subject: str, options: list) -> str:
    """把选择题题干和选项合并为可直接排版的文本。"""
    if not options:
        return _fmt_question(subject)
    letters = ["A", "B", "C", "D", "E", "F"]
    lines = [_fmt_question(subject)]
    for i, opt in enumerate(options):
        prefix = letters[i] if i < len(letters) else chr(ord("A") + i)
        lines.append(f"{prefix}. {_strip_option_prefix(str(opt))}")
    return " ⏎ ".join(lines)


def _get_image_display_width(img_path: str, max_width_cm: float = 14.0,
                             default_dpi: int = 150) -> float:
    """根据图片实际像素尺寸计算适合的显示宽度。

    读取图片像素宽度，用 DPI 换算为厘米。小图保持原始比例，
    大图限制在最大宽度内。

    Args:
        img_path: 图片绝对路径
        max_width_cm: 最大显示宽度（默认14cm，A4页面留边距后）
        default_dpi: 无 DPI 元数据时的默认 DPI

    Returns:
        显示宽度（cm），精确到1位小数
    """
    try:
        with Image.open(img_path) as img:
            width_px = img.width
            dpi = img.info.get("dpi", None)
            if dpi and dpi[0] and dpi[0] > 0:
                width_cm = width_px / dpi[0] * 2.54
            else:
                width_cm = width_px / default_dpi * 2.54
        width_cm = max(1.0, min(width_cm, max_width_cm))
        return round(width_cm, 1)
    except Exception:
        return 8.0


def _fmt_images(images: list) -> list:
    """将图片路径列表转为 pandoc Markdown 图片行。

    pandoc 支持 ![alt](path){width=Xcm} 语法，会自动嵌入图片到 docx。
    路径必须是绝对路径且使用正斜杠（pandoc 在 Windows 上对反斜杠处理不稳定）。
    图片宽度根据实际像素尺寸按比例计算，防止小图被撑大。
    """
    lines = []
    for img_path in (images or []):
        if not img_path or not os.path.exists(img_path):
            continue
        # 转为绝对路径 + 正斜杠
        abs_path = os.path.abspath(img_path).replace("\\", "/")
        width = _get_image_display_width(img_path)
        lines.append(f"![图]({abs_path}){{width={width}cm}}")
        lines.append("")
    return lines


def build_markdown(config: dict, is_teacher: bool = False) -> str:
    """构建完整 Markdown 讲义。"""
    md = []
    topic = config.get("topic", "未命名")

    def _normalize_options(q: dict) -> list:
        options = q.get("options", [])
        if isinstance(options, str) and options.strip():
            try:
                parsed = json.loads(options)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except Exception:
                return [x.strip() for x in re.split(r"(?:^|\n)\s*[A-D][\.、]", options) if x.strip()]
        if isinstance(options, list):
            return [str(item).strip() for item in options if str(item).strip()]
        return []

    # ── 封面 ──
    md.append("# " + topic)
    md.append("")
    md.append("## 小班专用")
    md.append("")
    md.append("---")
    md.append("")
    # 封面纵向留白，把姓名推到页面中下部（用 raw openxml 保证不被合并）
    md.append('```{=openxml}')
    for _ in range(10):
        md.append('<w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p>')
    md.append('```')
    md.append("")
    md.append("姓名：_____________")
    md.append("")

    # pandoc 原生分页：封面后分页 + 空白页
    md.append('```{=openxml}')
    md.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    md.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    md.append('```')
    md.append("")

    # ── 一、知识精讲 ──
    md.append("## 一、知识精讲（25-30分钟）")
    md.append("")
    for i, sec in enumerate(config.get("knowledgeSections", [])):
        d = sec.get("direction", f"方向{i+1}")
        md.append(f"### 方向{i+1}：{d}")
        md.append("")
        md.append(sec.get("concept", ""))
        md.append("")
        ex = sec.get("example", {})
        ex_options = _normalize_options(ex)
        md.append(f"**【例题】** {_fmt_choice_question(ex.get('subject',''), ex_options)}")
        md.append("")
        md.extend(_fmt_images(ex.get("images", [])))
        if ex.get("analysis"):
            md.append(f"**【思路分析】** {ex['analysis']}")
            md.append("")
        if ex.get("solution"):
            md.append(f"**【规范解答】** {ex['solution']}")
            md.append("")
        vr = sec.get("variant", {})
        vr_options = _normalize_options(vr)
        md.append(f"**【变式】** {_fmt_choice_question(vr.get('subject',''), vr_options)}")
        md.append("")
        md.extend(_fmt_images(vr.get("images", [])))
        # 学生版：留空白答题区（用 raw openxml 保证空行不被合并）
        if not is_teacher:
            md.append('```{=openxml}')
            md.append('<w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p>')
            md.append('<w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p>')
            md.append('<w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p>')
            md.append('<w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p>')
            md.append('```')
            md.append("")
        # 教师版：显示答案
        if is_teacher and vr.get("answer"):
            md.append(f"**【答案】** {vr['answer']}")
            md.append("")

    # ── 二、诊断测试 ──
    md.append("## 二、诊断测试（6题 · 12-15分钟）")
    md.append("")
    md.append("以下6道题覆盖本讲全部考察方向。做完后对答案，标记每个方向 ✅ 或 ❌。")
    md.append("")
    diag = config.get("diagnosis", {})
    for i, q in enumerate(diag.get("questions", []), 1):
        md.append(f"**{i}.** {_fmt_choice_question(q.get('subject',''), _normalize_options(q))}")
        md.extend(_fmt_images(q.get("images", [])))
        _review(md, q.get("direction",""), q.get("reviewOptions",[]))
        md.append("")

    md.append("**【分流规则】**")
    md.append("")
    md.append("| 诊断结果 | 分层练习内容 |")
    md.append("|----------|-------------|")
    md.append("| 方向标记 ❌ | 每个❌方向做2道巩固题 |")
    md.append("| 方向标记 ✅ | 每个✅方向做1道综合挑战题 |")
    md.append("")

    # ── 三、分层练习 ──
    md.append("## 三、分层练习（35-40分钟）")
    md.append("")
    md.append("根据诊断结果：❌的方向做巩固题，✅的方向做挑战题。")
    md.append("")

    prac = config.get("practice", {})
    cons = prac.get("consolidate", {})
    chal = prac.get("challenge", {})

    if cons:
        md.append("### 📝 巩固题（错的方向 — 每题2道）")
        md.append("")
        for direction, qs in cons.items():
            md.append(f"**▸ {direction}**")
            md.append("")
            for j, q in enumerate(qs or [], 1):
                md.append(f"**{j}.** {_fmt_choice_question(q.get('subject',''), _normalize_options(q))}")
                md.extend(_fmt_images(q.get("images", [])))
                _review(md, direction, q.get("reviewOptions",[]))
                md.append("")

    if chal:
        md.append("### 🚀 挑战题（对的方向 — 每题1道综合题）")
        md.append("")
        for direction, qs in chal.items():
            md.append(f"**▸ {direction}**")
            md.append("")
            for j, q in enumerate(qs or [], 1):
                md.append(f"**{j}.** {_fmt_choice_question(q.get('subject',''), _normalize_options(q))}")
                md.extend(_fmt_images(q.get("images", [])))
                _review(md, direction, q.get("reviewOptions",[]))
                md.append("")

    # ── 四、综合检测 ──
    md.append("## 四、综合检测（6题 · 15-18分钟）")
    md.append("")
    md.append("全班统一，覆盖全部考察方向。当堂批改。")
    md.append("")
    comp = config.get("comprehensive", {})
    for i, q in enumerate(comp.get("questions", []), 1):
        md.append(f"**{i}.** {_fmt_choice_question(q.get('subject',''), _normalize_options(q))}")
        md.extend(_fmt_images(q.get("images", [])))
        # 学生版：留空白答题区
        if not is_teacher:
            md.append('```{=openxml}')
            md.append('<w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p>')
            md.append('<w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p>')
            md.append('<w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p>')
            md.append('<w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p>')
            md.append('```')
            md.append("")
        _review(md, q.get("direction",""), q.get("reviewOptions",[]))
        md.append("")

    # ── 五、今日小结 ──
    md.append("## 五、今日小结（5分钟）")
    md.append("")
    md.append("请填写以下内容，回顾今天的学习：")
    md.append("")
    summary = config.get("summary", "")
    if summary:
        # 先处理换行符
        summary = _fmt_question(summary)
        # 再按 ⏎ 拆分
        for line in summary.split("⏎"):
            line = line.strip()
            if line:
                md.append(f"- {line}")
        md.append("")
    md.append("**📊 今日逻辑链断裂统计：**")
    md.append("")
    md.append("- 我错的最多的考察方向是：_____________")
    md.append("- 我最容易断的逻辑链步骤是：_____________")
    md.append("")

    # ── 教师版专属 ──
    if is_teacher:
        md.append("\\newpage")
        md.append("")
        md.append("## 教师专属")
        md.append("")
        md.append("### 课后反思")
        md.append("")
        md.append("1. 本节课学生掌握情况如何？哪些知识点理解困难？")
        md.append("")
        md.append("2. 例题讲解是否清晰？分层练习时间是否合理？")
        md.append("")
        md.append("3. 下节课如何衔接？")
        md.append("")

    return "\n".join(md)


def _review(md: list, direction: str, options: list):
    """添加紧凑复盘框。"""
    compact_options = [_strip_option_prefix(str(o))[:24] for o in (options or []) if str(o).strip()][:3]
    md.append(f"| 🔍 错因复盘 — {direction} |")
    md.append("|---------------------------|")
    md.append("| □ 做对了 □ 做错了 |")
    if compact_options:
        md.append("| " + "  ".join(f"□ {o}" for o in compact_options) + " |")
    md.append("| □ 其他：_____________ |")


# ============================================================================
# pandoc 转换
# ============================================================================

def _pandoc(md: str, out: str):
    """Markdown → docx（公式 → OMML）"""
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", encoding="utf-8", delete=False) as f:
        f.write(md)
        p = f.name
    try:
        r = subprocess.run(
            ["pandoc", p, "-o", out, "--from", "markdown", "--to", "docx"],
            capture_output=True,
            timeout=30,
        )
        stderr = r.stderr.decode("utf-8", errors="replace") if isinstance(r.stderr, (bytes, bytearray)) else (r.stderr or "")
        stdout = r.stdout.decode("utf-8", errors="replace") if isinstance(r.stdout, (bytes, bytearray)) else (r.stdout or "")
        if r.returncode != 0:
            raise RuntimeError(f"pandoc failed (code={r.returncode}): {stderr or stdout or 'no output'}")
    finally:
        os.unlink(p)


# ============================================================================
# 样式后处理
# ============================================================================

def _shade(cell, color: str):
    cell._tc.get_or_add_tcPr().append(
        parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color}"/>'))

def _font(run, w=FONT_W, e=FONT_E, size=None, bold=None, color=None):
    rPr = run._r.get_or_add_rPr()
    rf = rPr.find(qn('w:rFonts'))
    if rf is None:
        rf = parse_xml(f'<w:rFonts {nsdecls("w")}/>')
        rPr.insert(0, rf)
    rf.set(qn('w:ascii'), w); rf.set(qn('w:hAnsi'), w); rf.set(qn('w:eastAsia'), e)
    if size: run.font.size = size
    if bold is not None: run.bold = bold
    if color: run.font.color.rgb = RGBColor.from_string(color)

def _pfont(para, w=FONT_W, e=FONT_E, size=Pt(10.5), bold=False, color=None):
    for run in para.runs:
        _font(run, w, e, size, bold, color)

def _set_spacing(para, before=0, after=0, line=None):
    """设置段前段后间距（单位：twips，1pt=20twips）"""
    pPr = para._p.get_or_add_pPr()
    spacing = pPr.find(qn('w:spacing'))
    if spacing is None:
        spacing = parse_xml(f'<w:spacing {nsdecls("w")}/>')
        pPr.append(spacing)
    if before: spacing.set(qn('w:before'), str(before))
    if after: spacing.set(qn('w:after'), str(after))
    if line: spacing.set(qn('w:line'), str(line))

def _set_indent(para, left=0, firstLine=0):
    pPr = para._p.get_or_add_pPr()
    ind = pPr.find(qn('w:ind'))
    if ind is None:
        ind = parse_xml(f'<w:ind {nsdecls("w")}/>')
        pPr.append(ind)
    if left: ind.set(qn('w:left'), str(left))
    if firstLine: ind.set(qn('w:firstLine'), str(firstLine))

def apply_styles(doc: Document, is_teacher: bool = False):
    """精确匹配模板样式。"""

    # ── 处理 ⏎ 换行标记 ──
    from docx.oxml import OxmlElement
    from copy import deepcopy
    for para in doc.paragraphs:
        runs = list(para.runs)
        for ri, run in enumerate(runs):
            if '⏎' in (run.text or ''):
                parts = run.text.split('⏎')
                run.text = parts[0].rstrip()
                insert_after = run._r
                for part in parts[1:]:
                    # 创建包含 br 的 run（br 必须在 w:r 内部）
                    br_run = OxmlElement('w:r')
                    rPr = run._r.find(qn('w:rPr'))
                    if rPr is not None:
                        br_run.append(deepcopy(rPr))
                    br_elem = OxmlElement('w:br')
                    br_run.append(br_elem)
                    insert_after.addnext(br_run)
                    insert_after = br_run
                    # 创建文本 run
                    text_run = OxmlElement('w:r')
                    if rPr is not None:
                        text_run.append(deepcopy(rPr))
                    t_elem = OxmlElement('w:t')
                    t_elem.set(qn('xml:space'), 'preserve')
                    t_elem.text = part.lstrip()
                    text_run.append(t_elem)
                    insert_after.addnext(text_run)
                    insert_after = text_run
                break

    # ── 页面 ──
    for sec in doc.sections:
        sec.page_width = PAGE_W; sec.page_height = PAGE_H
        sec.top_margin = MARGIN; sec.bottom_margin = MARGIN
        sec.left_margin = MARGIN; sec.right_margin = MARGIN

        # 页眉
        hdr = sec.header
        hp = hdr.paragraphs[0] if hdr.paragraphs else hdr.add_paragraph()
        hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pPr = hp._p.get_or_add_pPr()
        pBdr = parse_xml(
            f'<w:pBdr {nsdecls("w")}>'
            f'<w:bottom w:val="single" w:sz="6" w:space="1" w:color="{COLOR_BORDER}"/>'
            f'</w:pBdr>')
        pPr.append(pBdr)
        # 清除并设置
        for r in hp.runs: r._r.getparent().remove(r._r)
        rn = hp.add_run("\t张老师说数学（西安版）")
        _font(rn, FONT_SONG, FONT_SONG, Pt(9), color=COLOR_HEADER)

    # ── 段落 ──
    for para in doc.paragraphs:
        sn = para.style.name if para.style else ""
        txt = para.text.strip()

        if sn == "Heading 1":
            # 封面标题：Times New Roman 28pt 蓝色居中
            _pfont(para, FONT_TNR, FONT_TNR, Pt(28), True, COLOR_TITLE)
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _set_spacing(para, before=0, after=0)

        elif sn == "Heading 2":
            # 板块标题：14pt 加粗 Calibri+微软雅黑 深蓝 #376092
            _pfont(para, FONT_W, FONT_E, Pt(14), True, COLOR_SECTION)
            _set_spacing(para, before=480, after=200)

        elif sn == "Heading 3":
            # 子标题：11pt 加粗 蓝色 #4472C4
            _pfont(para, FONT_W, FONT_E, Pt(11), True, COLOR_SUB)
            _set_spacing(para, before=120, after=80)

        elif sn in ("Body Text", "First Paragraph", "Compact", "Normal"):
            # 正文：10.5pt
            _pfont(para, FONT_W, FONT_E, Pt(10.5))

            # 特殊段落识别
            if txt.startswith("【例题】") or txt.startswith("【变式】"):
                _pfont(para, FONT_W, FONT_E, Pt(10.5), True)  # 加粗标签
                _set_spacing(para, after=40)
            elif txt.startswith("【思路分析】") or txt.startswith("【规范解答】") or txt.startswith("【答案】"):
                _set_indent(para, left=567)
                _set_spacing(para, after=40)
            elif txt.startswith("▸"):
                # 方向标签
                _pfont(para, FONT_W, FONT_E, Pt(10.5), True)
                _set_indent(para, left=283)
                _set_spacing(para, after=40)
            elif txt.startswith("方向") and "：" in txt:
                # 概念框标签
                _set_spacing(para, after=80)
            elif any(txt.startswith(f"{x}.") for x in range(1, 20)):
                # 题目编号行
                _pfont(para, FONT_W, FONT_E, Pt(10.5), True)
                _set_spacing(para, after=80)
            elif txt.startswith("📝") or txt.startswith("🚀"):
                # 分层标签 → 在 Markdown 里用 ### 已经是 Heading 3 了
                pass
            elif txt in ("小班专用",):
                _pfont(para, FONT_TNR, FONT_TNR, Pt(28), True, COLOR_TITLE)
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif txt.startswith("姓名"):
                _set_spacing(para, after=200)
                _set_indent(para, left=4337)
            elif txt.startswith("📊"):
                _pfont(para, FONT_W, FONT_E, Pt(10.5), True)
                _set_spacing(para, before=120, after=80)

    # ── 表格 ──
    for table in doc.tables:
        first_text = table.rows[0].cells[0].text if table.rows else ""
        is_review = "错因复盘" in first_text

        if is_review:
            # 复盘框：左对齐、满可用宽度，紧贴题目
            table.alignment = WD_TABLE_ALIGNMENT.LEFT
            tbl = table._tbl
            tblPr = tbl.find(qn('w:tblPr'))
            if tblPr is None:
                tblPr = parse_xml(f'<w:tblPr {nsdecls("w")}/>')
                tbl.insert(0, tblPr)
            tblW = tblPr.find(qn('w:tblW'))
            if tblW is None:
                tblW = parse_xml(f'<w:tblW {nsdecls("w")} w:w="9000" w:type="dxa"/>')
                tblPr.append(tblW)
            else:
                tblW.set(qn('w:w'), '9000')
                tblW.set(qn('w:type'), 'dxa')
        else:
            table.alignment = WD_TABLE_ALIGNMENT.CENTER

        for ri, row in enumerate(table.rows):
            for cell in row.cells:
                for para in cell.paragraphs:
                    if is_review:
                        # 复盘框
                        if ri == 0:
                            _shade(cell, COLOR_REVIEW_BG)
                            _pfont(para, FONT_W, FONT_E, Pt(10), True, COLOR_SUB)
                        else:
                            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
                            _pfont(para, FONT_W, FONT_E, Pt(10))
                    else:
                        # 普通表格
                        if ri == 0:
                            _shade(cell, COLOR_TABLE_BG)
                            _pfont(para, FONT_W, FONT_E, Pt(10), True, COLOR_WHITE)
                        else:
                            _pfont(para, FONT_W, FONT_E, Pt(10))
                        para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # ── 封面段落特殊处理 ──
    # Heading 2 "小班专用" → 需要和模板一样：Times New Roman 28pt 蓝色居中
    for para in doc.paragraphs:
        if para.style.name == "Heading 2" and para.text.strip() == "小班专用":
            _pfont(para, FONT_TNR, FONT_TNR, Pt(28), True, COLOR_TITLE)
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _set_spacing(para, before=0, after=0)


# ============================================================================
# 主入口
# ============================================================================

def _pick_output_path(output_dir: str, topic: str, is_teacher: bool) -> str:
    """选择一个可写的输出路径，避免被现有锁文件占用。"""
    safe = re.sub(r'[/\\ ]', '_', topic)
    vs = "教师版" if is_teacher else "学生版"
    base = os.path.join(output_dir, f"{safe}_{vs}.docx")
    lock_name = f"~${safe}_{vs}.docx"
    lock_path = os.path.join(output_dir, lock_name)
    if os.path.exists(lock_path):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(output_dir, f"{safe}_{vs}_{stamp}_{uuid.uuid4().hex[:6]}.docx")
    if os.path.exists(base):
        try:
            with open(base, "ab"):
                pass
        except OSError:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return os.path.join(output_dir, f"{safe}_{vs}_{stamp}_{uuid.uuid4().hex[:6]}.docx")
    return base


def generate(config: dict, output_dir: str = None, is_teacher: bool = False) -> str:
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

    topic = config.get("topic", "lecture")
    out = _pick_output_path(output_dir, topic, is_teacher)
    os.makedirs(output_dir, exist_ok=True)

    md = build_markdown(config, is_teacher)
    _pandoc(md, out)
    doc = Document(out)
    apply_styles(doc, is_teacher)
    doc.save(out)
    return out


def generate_both(config: dict, output_dir: str = None) -> dict:
    return {
        "student": generate(config, output_dir, False),
        "teacher": generate(config, output_dir, True),
    }


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) < 2:
        print("用法: python docx_writer.py config.json"); sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        config = json.load(f)
    r = generate_both(config)
    print(f"✅ 学生版: {r['student']}")
    print(f"✅ 教师版: {r['teacher']}")
