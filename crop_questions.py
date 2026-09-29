"""
PaddleOCR 精确配图裁剪模块

将 PDF 页面渲染为图片 → PaddleOCR 识别文字位置 → 
匹配 Qwen 提取的题目 → 确定每道题的区域 → 
扩展包含几何图 → 裁剪保存

用法:
    from crop_questions import crop_questions_from_page
    crops = crop_questions_from_page(pdf_path, page_num, questions)
"""

import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# 常量
CROP_MARGIN = 40  # 裁剪上下额外留白（像素）
PAGE_W = 612  # A4 72DPI
PAGE_H = 792


def _get_paddle_ocr():
    """获取或初始化 PaddleOCR 实例（懒加载）。"""
    global _ocr_instance
    import warnings
    warnings.filterwarnings('ignore')
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

    if '_ocr_instance' not in globals():
        from paddleocr import PaddleOCR
        _ocr_instance = PaddleOCR(lang='ch', ocr_version='PP-OCRv5')
    return _ocr_instance


def _normalize_text(text: str) -> str:
    """归一化文本用于匹配。"""
    t = text.lower()
    t = re.sub(r'\\[a-z]+', ' ', t)
    for ch in '$^{}_[]()':
        t = t.replace(ch, ' ')
    t = re.sub(r'[，。、！？：；""\'!?:;\n\r\t]', ' ', t)
    t = re.sub(r'\s+', '', t)
    return t.strip()


def _get_page_image(pdf_path: str, page_num: int, dpi: int = 150) -> tuple:
    """渲染 PDF 页面为图片，返回 (image_path, width, height)。"""
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    pix = page.get_pixmap(dpi=dpi)
    tmp = tempfile.mkdtemp(prefix="crop_")
    img_path = os.path.join(tmp, f"page_{page_num}.png")
    pix.save(img_path)
    doc.close()
    return img_path, pix.width, pix.height


def _get_image_positions(pdf_path: str, page_num: int) -> List[Dict]:
    """获取页面上所有图片（几何图）的位置。"""
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    positions = []
    for img in page.get_images(full=True):
        xref = img[0]
        rects = page.get_image_rects(xref)
        for rect in rects:
            positions.append({
                "x0": rect.x0, "y0": rect.y0,
                "x1": rect.x1, "y1": rect.y1,
            })
    doc.close()
    return positions


def _match_questions_to_textlines(
    questions: List[Dict],
    textlines: List[Dict],
    page_h: int,
    image_positions: List[Dict] = None,
) -> List[Optional[List[int]]]:
    """将 Qwen 题目匹配到 PaddleOCR 文本行，返回每题的 bbox。

    策略：
    1. 用 Qwen 题干的前 10-15 个字去匹配 PaddleOCR 识别的文本行
    2. 匹配到的文本行 y 坐标作为该题的起始位置
    3. 下一题的起始位置作为当前题的结束位置
    4. 扩展包含该区域内的图片

    Args:
        questions: Qwen 提取的题目列表（每个含 subject）
        textlines: PaddleOCR 文本行列表 [{y0, y1, text}]
        page_h: 页面高度
        image_positions: 图片位置列表

    Returns:
        [bbox] 列表，bbox=[x1,y1,x2,y2] 或 None（未匹配到）
    """
    result = [None] * len(questions)
    if not textlines:
        return result

    # 为每道题找起始 y
    q_positions = []  # [(qi, y_start)]
    for qi, q in enumerate(questions):
        subject = q.get("subject", "").strip()
        sig = _normalize_text(subject)[:15] if subject else ""
        if not sig or len(sig) < 4:
            continue

        found_y = None
        for tl in textlines:
            tlnorm = _normalize_text(tl["text"])
            if sig in tlnorm or (len(sig) >= 8 and sig[:8] in tlnorm):
                found_y = tl["y0"]
                break

        if found_y is not None:
            q_positions.append((qi, found_y))
            logger.info(f"  匹配: 题{qi+1} y={found_y:.0f}")

    if not q_positions:
        # 文本匹配失败，按顺序分配
        step = page_h / max(len(questions), 1)
        for qi in range(len(questions)):
            y_start = qi * step
            y_end = min((qi + 1) * step + CROP_MARGIN, page_h)
            result[qi] = [0, int(y_start), PAGE_W, int(y_end)]
        logger.info(f"  文本匹配失败，按均分分配")
        return result

    q_positions.sort(key=lambda x: x[1])

    # 计算 bbox
    for idx, (qi, y_start) in enumerate(q_positions):
        y_end = (
            q_positions[idx + 1][1] + CROP_MARGIN
            if idx + 1 < len(q_positions)
            else page_h
        )

        # 扩展包含该区域内的图片
        if image_positions:
            for img in image_positions:
                img_mid = (img["y0"] + img["y1"]) / 2
                if y_start <= img_mid <= y_end:
                    y_end = max(y_end, img["y1"] + CROP_MARGIN)
                    logger.info(f"  扩展: 包含图片 y={img['y0']:.0f}-{img['y1']:.0f}")

        bbox = [0, max(0, int(y_start - CROP_MARGIN)), PAGE_W, int(min(page_h, y_end))]
        result[qi] = bbox
        logger.info(f"  裁剪: 题{qi+1} y=[{bbox[1]}-{bbox[3]}]")

    # 未匹配的题在间隙中插值
    matched_qis = {qi for qi, _ in q_positions}
    unmatched = [qi for qi in range(len(questions)) if qi not in matched_qis]
    if unmatched:
        logger.info(f"  插值: {len(unmatched)} 道题未直接匹配")
        for qi in unmatched:
            prev_y = 0
            next_y = page_h
            for mqi, my in q_positions:
                if mqi < qi and my > prev_y:
                    prev_y = my
                if mqi > qi and my < next_y:
                    next_y = my
            y_start = (prev_y + next_y) / 2 - CROP_MARGIN
            y_end = (prev_y + next_y) / 2 + CROP_MARGIN
            result[qi] = [0, int(max(0, y_start)), PAGE_W, int(min(page_h, y_end))]

    return result


def crop_questions(
    pdf_path: str,
    page_num: int,
    questions: List[Dict],
    output_dir: str = None,
) -> List[Dict]:
    """对一页 PDF，用 PaddleOCR 精准裁剪每道题的区域。

    Args:
        pdf_path: PDF 文件路径
        page_num: 页码（从 0 开始）
        questions: Qwen 从该页提取的题目列表
        output_dir: 输出目录，默认 project/images/cropped/

    Returns:
        题目列表，每道题追加 image_path 和 has_image 字段
    """
    if output_dir is None:
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "images", "cropped"
        )
    os.makedirs(output_dir, exist_ok=True)

    from PIL import Image

    # 1. 渲染页面
    page_img_path, page_w, page_h = _get_page_image(pdf_path, page_num)

    # 2. 获取图片位置
    image_positions = _get_image_positions(pdf_path, page_num)

    # 3. PaddleOCR 识别文字位置
    ocr = _get_paddle_ocr()
    result = ocr.predict(page_img_path)
    ocr_result = result[0]
    dt_polys = ocr_result.get('dt_polys', [])
    rec_texts = ocr_result.get('rec_texts', [])
    textlines = []
    for poly, text in zip(dt_polys, rec_texts):
        ys = [p[1] for p in poly]
        text = text.strip()
        if text:
            textlines.append({
                "y0": min(ys),
                "y1": max(ys),
                "text": text,
            })

    logger.info(f"PaddleOCR: {len(textlines)} 文本行, {len(image_positions)} 张图")

    # 4. 匹配题目到文本行
    bboxes = _match_questions_to_textlines(
        questions, textlines, page_h, image_positions
    )

    # 5. 裁剪
    output_questions = []
    for qi, q in enumerate(questions):
        q = dict(q)  # 复制
        bbox = bboxes[qi] if qi < len(bboxes) else None
        if bbox and bbox[3] > bbox[1] + 20:  # 至少 20px 高
            from PIL import Image as PILImage
            img = PILImage.open(page_img_path)
            cropped = img.crop(bbox)
            crop_path = os.path.join(output_dir, f"{q.get('id', f'q{qi}')}.png")
            cropped.save(crop_path, "PNG")
            q["has_image"] = True
            q["image_path"] = crop_path
            logger.info(f"  裁剪完成: {crop_path} ({bbox[2]-bbox[0]}x{bbox[3]-bbox[1]}px)")

        output_questions.append(q)

    return output_questions
