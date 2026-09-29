"""
文件解析模块

支持三种源文件格式：
1. .docx — 使用 python-docx 提取文本
2. 文字型 .pdf — 使用 PyMuPDF 提取文本
3. 扫描件 .pdf — 使用 PyMuPDF 将每页渲染为 PNG 图片

统一输出格式:
{
    "type": "text" | "images",
    "content": "提取的文本内容",
    "images": ["/path/to/page_1.png", ...],
    "source_file": "源文件名",
    "total_pages": 页数
}
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# VML 命名空间（python-docx 的 qn() 不含 v 前缀，需手动定义）
_VML_NS = "urn:schemas-microsoft-com:vml"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _has_text(pdf_doc: Any, sample_pages: int = 3) -> bool:
    """检测 PDF 是否为文字型（采样前几页检查文本长度）"""
    total_chars = 0
    pages_to_check = min(sample_pages, len(pdf_doc))
    for i in range(pages_to_check):
        page = pdf_doc[i]
        text = page.get_text().strip()
        total_chars += len(text)
    # 平均每页超过 50 个字符认为是文字型
    return (total_chars / pages_to_check) >= 50 if pages_to_check > 0 else False


def parse_old_doc(filepath: str) -> Dict[str, Any]:
    """解析旧版 .doc 文件：用 LibreOffice 导出 PDF，再渲染为图片。
    
    原因：LibreOffice 转换 .doc → .docx 时 MathType 公式对象会丢失，
    但渲染为 PDF 时公式能正常显示。走图片模式可 100% 保留公式。
    """
    import subprocess
    import shutil

    logger.info(f"解析旧版 DOC 文件（PDF 渲染模式）: {filepath}")

    # 检查 LibreOffice
    if not shutil.which("soffice") and not shutil.which("libreoffice"):
        raise RuntimeError(
            "解析 .doc 文件需要 LibreOffice。请安装: sudo apt install libreoffice"
        )

    tmpdir = tempfile.mkdtemp(prefix="doc2pdf_")
    try:
        # LibreOffice 导出为 PDF
        cmd = [
            "soffice" if shutil.which("soffice") else "libreoffice",
            "--headless",
            "--convert-to", "pdf",
            "--outdir", tmpdir,
            filepath,
        ]
        logger.info(f"DOC → PDF: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"DOC 转 PDF 失败: {result.stderr}")

        # 找到输出的 PDF
        pdf_files = list(Path(tmpdir).glob("*.pdf"))
        if not pdf_files:
            raise RuntimeError("转换后未找到 PDF 文件")

        pdf_path = str(pdf_files[0])
        logger.info(f"PDF 生成成功: {pdf_path}")

        # 走扫描件 PDF 渲染流程（公式不会丢）
        return parse_scanned_pdf(pdf_path)
    finally:
        import shutil as _shutil
        _shutil.rmtree(tmpdir, ignore_errors=True)


def _build_rid_to_media_map(doc) -> dict:
    """用 python-docx 的 part.rels 建立 rId → media 文件名映射。

    遍历 doc.part.rels，筛选 reltype 含 "image" 的关系，
    返回 {"rId5": "image5.png", ...}
    """
    rid_map = {}
    for rel_id, rel in doc.part.rels.items():
        if "image" in rel.reltype.lower():
            rid_map[rel_id] = os.path.basename(rel.target_ref)
    return rid_map


def _local_name(tag: str) -> str:
    """返回 XML 标签的本地名（去掉命名空间）。"""
    if not tag:
        return ""
    return tag.rsplit("}", 1)[-1]


def _extract_math_text(math_elem) -> str:
    """提取 Word OMML 公式中的可见文本。"""
    parts = []
    for elem in math_elem.iter():
        local = _local_name(elem.tag)
        if local in ("t", "delText") and elem.text:
            parts.append(elem.text)
        elif local == "chr":
            ch = elem.get("val") or elem.get(f"{{{_R_NS}}}val")
            if ch:
                parts.append(ch)
        elif local == "sym":
            ch = elem.get("char") or elem.get(f"{{{_R_NS}}}char")
            if ch:
                parts.append(ch)
    return "".join(parts).strip()


def _extract_para_text_with_images(p_elem, rid_map: dict) -> str:
    """从 <w:p> 元素中按文档顺序提取文本、公式和图片标记。

    深度优先遍历 p_elem，遇到 <w:t> 收集文本，
    遇到 Word 公式对象（m:oMath / m:oMathPara）提取其可见文本，
    遇到图片引用（DrawingML / VML）插入 [图片: imageN.png]。
    同一 rId 的 DML+VML 双引用只计一次。
    """
    from docx.oxml.ns import qn

    parts = []
    rid_output = set()

    def walk(elem):
        local = _local_name(elem.tag)

        if local in ("oMath", "oMathPara"):
            math_text = _extract_math_text(elem)
            if math_text:
                parts.append(math_text)
            return

        if local == "t":
            if elem.text:
                parts.append(elem.text)
            return

        if local == "blip":
            rid = elem.get(qn('r:embed'))
            if rid and rid in rid_map and rid not in rid_output:
                rid_output.add(rid)
                name = rid_map[rid]
                parts.append(f"\n[图片: {name}]\n")
            return

        if local == "imagedata":
            rid = elem.get(f"{{{_R_NS}}}id")
            if rid and rid in rid_map and rid not in rid_output:
                rid_output.add(rid)
                name = rid_map[rid]
                parts.append(f"\n[图片: {name}]\n")
            return

        for child in list(elem):
            walk(child)

    walk(p_elem)
    return "".join(parts)


def parse_docx(filepath: str) -> Dict[str, Any]:
    """解析 .docx 文件，提取带图片位置标记的文本和嵌入图片。

    文本中的 [图片: imageN.png] 标记表示该位置有一张嵌入图片，
    N 为图片在文档中的实际文件名。图片提取到持久目录供后续使用。
    """
    import zipfile
    from docx import Document

    logger.info(f"解析 DOCX 文件: {filepath}")

    doc = Document(filepath)

    # 建立 rId → media 文件名映射
    rid_map = _build_rid_to_media_map(doc)
    logger.info(f"找到 {len(rid_map)} 个图片关系")

    # 提取带图片标记的文本
    paragraphs = []
    for para in doc.paragraphs:
        text = _extract_para_text_with_images(para._element, rid_map).strip()
        if text:
            paragraphs.append(text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    text = _extract_para_text_with_images(para._element, rid_map).strip()
                    if text:
                        paragraphs.append(text)
    content = "\n".join(paragraphs)

    # 提取图片到持久目录（不用 tempfile，因为图片要长期存在供讲义使用）
    source_stem = Path(filepath).stem
    output_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "images", "source", source_stem
    )
    os.makedirs(output_dir, exist_ok=True)

    image_paths = []
    try:
        from PIL import Image as PILImage
        from io import BytesIO
    except ImportError:
        PILImage = None
        BytesIO = None

    _MIN_IMG_DIM = 10  # 过滤百炼不接受的过小图片

    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            for name in z.namelist():
                if name.startswith("word/media/") and not name.endswith('/'):
                    ext = os.path.splitext(name)[1].lower()
                    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff'):
                        data = z.read(name)
                        img_name = os.path.basename(name)

                        # 尺寸过滤：跳过宽/高 < MIN_IMG_DIM 的装饰图
                        if PILImage is not None:
                            try:
                                bio = BytesIO(data)
                                with PILImage.open(bio) as pil_img:
                                    w, h = pil_img.size
                                if w < _MIN_IMG_DIM or h < _MIN_IMG_DIM:
                                    logger.info(f"跳过小尺寸图片 {img_name}（{w}x{h} < {_MIN_IMG_DIM}px）")
                                    continue
                            except Exception:
                                pass  # 无法读取尺寸时保留图片

                        img_path = os.path.join(output_dir, img_name)
                        with open(img_path, 'wb') as f:
                            f.write(data)
                        image_paths.append(img_path)
                        logger.debug(f"提取图片: {img_name}")
    except Exception as e:
        logger.warning(f"提取 DOCX 图片失败（不影响文字提取）: {e}")

    logger.info(
        f"DOCX 解析完成，{len(paragraphs)} 段文本，{len(image_paths)} 张图片，"
        f"图片目录: {output_dir}"
    )

    return {
        "type": "text",
        "content": content,
        "images": image_paths,
        "source_file": os.path.basename(filepath),
        "total_pages": max(len(image_paths), 1),
    }


def parse_text_pdf(filepath: str) -> Dict[str, Any]:
    """解析文字型 PDF，提取文本内容（分页）"""
    import fitz  # PyMuPDF

    logger.info(f"解析文字型 PDF: {filepath}")
    pdf_doc = fitz.open(filepath)

    pages_text = []
    for i in range(len(pdf_doc)):
        page = pdf_doc[i]
        text = page.get_text().strip()
        if text:
            pages_text.append(f"--- 第 {i + 1} 页 ---\n{text}")

    total_pages = len(pdf_doc)
    pdf_doc.close()

    content = "\n\n".join(pages_text)
    logger.info(f"文字型 PDF 解析完成，{total_pages} 页，共 {len(content)} 字符")

    return {
        "type": "text",
        "content": content,
        "images": [],
        "source_file": os.path.basename(filepath),
        "total_pages": total_pages,
    }


def parse_scanned_pdf(filepath: str, output_dir: str = None) -> Dict[str, Any]:
    """解析扫描件 PDF，将每页渲染为 PNG 图片"""
    import fitz  # PyMuPDF

    logger.info(f"解析扫描件 PDF: {filepath}")
    pdf_doc = fitz.open(filepath)

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="lecture_pdf_")

    image_paths = []
    for i in range(len(pdf_doc)):
        page = pdf_doc[i]
        # 渲染页面为图片，DPI=72 控制大小在 Qwen API 限制内
        pix = page.get_pixmap(dpi=72)
        img_path = os.path.join(output_dir, f"page_{i + 1:04d}.png")
        pix.save(img_path)
        image_paths.append(img_path)
        logger.debug(f"渲染第 {i + 1}/{len(pdf_doc)} 页 -> {img_path}")

    pdf_doc.close()

    logger.info(f"扫描件 PDF 渲染完成，{len(image_paths)} 张图片 -> {output_dir}")

    return {
        "type": "images",
        "content": "",
        "images": image_paths,
        "source_file": os.path.basename(filepath),
        "total_pages": len(image_paths),
    }


def parse_file(filepath: str) -> Dict[str, Any]:
    """
    自动检测文件类型并选择合适的解析器。

    返回统一格式:
    {
        "type": "text" | "images",
        "content": "...",
        "images": [...],
        "source_file": "...",
        "total_pages": N
    }
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")

    suffix = path.suffix.lower()

    if suffix == ".docx":
        return parse_docx(filepath)

    elif suffix == ".doc":
        return parse_old_doc(filepath)

    elif suffix == ".pdf":
        import fitz

        # 先检测 PDF 类型
        pdf_doc = fitz.open(filepath)
        try:
            if _has_text(pdf_doc):
                pdf_doc.close()
                return parse_text_pdf(filepath)
            else:
                pdf_doc.close()
                return parse_scanned_pdf(filepath)
        finally:
            if not pdf_doc.is_closed:
                pdf_doc.close()

    else:
        raise ValueError(f"不支持的文件格式: {suffix}，仅支持 .doc、.docx 和 .pdf")
