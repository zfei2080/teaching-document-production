"""
AI 提取模块

调用通义千问 Qwen-VL-Max（DashScope，OpenAI 兼容接口）从解析后的内容中提取数学题目。

功能：
- 支持文本输入和图片（多模态）输入
- 自动重试（最多 3 次，指数退避）
- 大 PDF 按页分片处理，避免单次请求图片过多
"""

import base64
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List

from openai import OpenAI

from config import (
    INITIAL_BACKOFF,
    MAX_RETRIES,
    MAX_TOKENS,
    MODEL_NAME,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    TEMPERATURE,
)
from prompts import SYSTEM_PROMPT, build_extraction_prompt

logger = logging.getLogger(__name__)

# 每次 API 调用最多处理的图片数量
MAX_IMAGES_PER_REQUEST = 2
# 文本模式下，单次请求的最大文本长度与图片数，避免长文档一次性打爆 vision 路由
MAX_TEXT_CHARS_PER_REQUEST = 8000
MAX_TEXT_IMAGES_PER_REQUEST = 4


def _encode_image(image_path: str) -> str:
    """将图片编码为 base64 data URL"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _build_messages(
    content: str, images: List[str], grade: str = ""
) -> List[Dict[str, Any]]:
    """构建 OpenAI API 消息列表，支持文本和图片混合输入"""
    content_type = "图片内容" if images else "文本内容"

    user_prompt = build_extraction_prompt(
        content=content,
        content_type=content_type,
        grade=grade,
    )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    user_content: List[Dict[str, Any]] = []
    user_content.append({"type": "text", "text": user_prompt})

    for img_path in images:
        data_url = f"data:image/png;base64,{_encode_image(img_path)}"
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": data_url},
            }
        )

    messages.append({"role": "user", "content": user_content})
    return messages


def _call_api_with_retry(
    client: OpenAI, messages: List[Dict], max_retries: int = MAX_RETRIES
) -> str:
    """调用 OpenAI API，带重试逻辑"""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"API 调用 (第 {attempt}/{max_retries} 次)...")
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            )
            result = response.choices[0].message.content
            logger.info(f"API 调用成功，返回 {len(result)} 字符")
            return result

        except Exception as e:
            last_error = e
            logger.warning(f"API 调用失败 (第 {attempt} 次): {e}")
            if attempt < max_retries:
                wait = INITIAL_BACKOFF ** attempt
                logger.info(f"等待 {wait} 秒后重试...")
                time.sleep(wait)

    raise RuntimeError(f"API 调用失败（已重试 {max_retries} 次）: {last_error}")


def _extract_json_text(response_text: str) -> str:
    """从模型响应中提取 JSON 片段。"""
    text = response_text.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        text = text.strip()

    text = text.replace("\\\\{", "{").replace("\\\\}", "}")
    text = text.replace("\\{", "{").replace("\\}", "}")

    if text.startswith("[") and text.endswith("]"):
        return text

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    return text


def _normalize_json_escapes(text: str) -> str:
    r"""修正模型常见的 JSON 非法转义。

    状态机方案：逐个字符遍历。
    对每个反斜杠，检查后续字符是否为合法 JSON escape 序列
    (\" \\ \/ \b \f \n \r \t \uXXXX)，是则保留，否则双写。
    无 pattern 互踩风险，已在 10 个历史调试文件上验证通过。
    """
    VALID_JSON_ESCAPE_CHARS = frozenset('"\\/bfnrt')
    result = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in VALID_JSON_ESCAPE_CHARS:
                result.append('\\')
                result.append(nxt)
                i += 2
                continue
            if nxt == 'u' and i + 5 < len(text):
                hex4 = text[i + 2 : i + 6]
                if all(c in '0123456789ABCDEFabcdef' for c in hex4):
                    result.append('\\u')
                    result.append(hex4)
                    i += 6
                    continue
            # 不是合法 JSON escape → 双写反斜杠
            result.append('\\\\')
            i += 1
        else:
            result.append(ch)
            i += 1
    return ''.join(result)


def _attach_question_images(
    questions: List[Dict[str, Any]], basename_to_path: Dict[str, str]
) -> List[Dict[str, Any]]:
    """把模型返回的图片文件名映射回持久路径。"""
    for q in questions:
        ai_images = q.pop("images", [])
        image_objs = []
        for fname in ai_images:
            if fname in basename_to_path:
                image_objs.append({
                    "filename": fname,
                    "filepath": basename_to_path[fname],
                })
            else:
                logger.warning(
                    f"AI 返回的图片文件名不在提取列表中: {fname}，已跳过"
                )
        q["images"] = image_objs
        q["has_image"] = len(image_objs) > 0
        q["image_refs"] = [obj["filename"] for obj in image_objs]
    return questions


def _extract_image_refs_from_text(text: str) -> List[str]:
    """从题块文本中提取图片标记顺序。"""
    refs = []
    for match in re.finditer(r"\[图片(?:\d+)?:\s*([^\]]+)\]", text or ""):
        ref = match.group(1).strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _split_text_question_blocks(content: str) -> List[str]:
    """把长文本按题号切成题块。"""
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    matches = list(re.finditer(r"(?m)^\s*\d+\.", text))
    if len(matches) < 2:
        stripped = text.strip()
        return [stripped] if stripped else []

    blocks = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def _build_text_batches(content: str, basename_to_path: Dict[str, str]) -> List[Dict[str, Any]]:
    """把长文本题目切成多个可调用批次。"""
    blocks = _split_text_question_blocks(content)
    if not blocks:
        return []

    batches = []
    current_blocks: List[str] = []
    current_images: List[str] = []
    current_chars = 0

    for block in blocks:
        refs = _extract_image_refs_from_text(block)
        block_images = [basename_to_path[r] for r in refs if r in basename_to_path]
        block_chars = len(block)
        unique_next_images = current_images + [img for img in block_images if img not in current_images]
        prospective_chars = current_chars + block_chars + (2 if current_blocks else 0)
        prospective_images = len(unique_next_images)

        if current_blocks and (
            prospective_chars > MAX_TEXT_CHARS_PER_REQUEST
            or prospective_images > MAX_TEXT_IMAGES_PER_REQUEST
        ):
            batches.append({
                "content": "\n\n".join(current_blocks),
                "images": current_images[:],
            })
            current_blocks = []
            current_images = []
            current_chars = 0

        current_blocks.append(block)
        current_chars += block_chars
        for img in block_images:
            if img not in current_images:
                current_images.append(img)

    if current_blocks:
        batches.append({
            "content": "\n\n".join(current_blocks),
            "images": current_images[:],
        })

    return batches


def _parse_api_response(
    response_text: str, source_file: str, image_refs: List[str]
) -> List[Dict[str, Any]]:
    """解析 API 返回的 JSON 数组，补充元数据字段"""
    text = _extract_json_text(response_text)

    attempts = [text, _normalize_json_escapes(text)]
    last_error = None

    for candidate in attempts:
        try:
            questions = json.loads(candidate)
            break
        except json.JSONDecodeError as e:
            last_error = e
            try:
                questions = json.loads(candidate, strict=False)
                break
            except json.JSONDecodeError as e2:
                last_error = e2
    else:
        import tempfile
        debug_path = os.path.join(tempfile.gettempdir(), f"api_response_debug_{uuid.uuid4().hex[:8]}.txt")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(response_text)
        logger.error(f"API 返回内容无法解析为 JSON，已保存到: {debug_path}")
        logger.error(f"最后的 JSON 错误: {last_error}")
        logger.error(f"响应长度: {len(response_text)} 字符")
        raise ValueError(f"API 返回格式错误，无法解析为 JSON 数组。调试文件: {debug_path}")

    if not isinstance(questions, list):
        raise ValueError("API 返回的不是 JSON 数组")

    for q in questions:
        q["id"] = str(uuid.uuid4())
        q["source_file"] = source_file

        if "knowledge_points" in q and not q.get("knowledge_point"):
            q["knowledge_point"] = q.pop("knowledge_points")
        if "common_mistakes" in q and not q.get("error_prone"):
            q["error_prone"] = q.pop("common_mistakes")
        if "common_mistake" in q and not q.get("error_prone"):
            q["error_prone"] = q.pop("common_mistake")
        if "easy_mistake" in q and not q.get("error_prone"):
            q["error_prone"] = q.pop("easy_mistake")
        if "error_prones" in q and not q.get("error_prone"):
            q["error_prone"] = q.pop("error_prones")

        q.setdefault("subject", "")
        q.setdefault("answer", "（未提供）")
        q.setdefault("knowledge_point", "未分类")
        q.setdefault("difficulty", "中等")
        q.setdefault("error_prone", "无明显易错点")
        q.setdefault("has_image", False)
        q.setdefault("images", [])
        q.setdefault("image_refs", [])
        q.setdefault("source_page", 0)

    return questions


def extract_questions(
    parsed_data: Dict[str, Any], max_questions: int = None, grade: str = ""
) -> List[Dict[str, Any]]:
    """
    从解析后的数据中提取题目。

    Args:
        parsed_data: parser.parse_file() 返回的统一格式
        max_questions: 最多提取的题目数，None 表示不限制
        grade: 学段，如 "七上"、"八下"、"高一上"、"小升初"、"初中" 等，完整列表见 textbook_filter.ALL_GRADE_LEVELS

    Returns:
        题目列表，每道题一个字典
    """
    source_file = parsed_data["source_file"]
    data_type = parsed_data["type"]

    client = OpenAI(
        api_key=OPENAI_API_KEY or "not-needed",
        base_url=OPENAI_BASE_URL,
    )

    all_questions = []
    image_refs = [os.path.basename(p) for p in parsed_data.get("images", [])]

    if data_type == "text":
        content = parsed_data["content"]
        images = parsed_data.get("images", [])
        if max_questions:
            content += f"\n\n（最多提取 {max_questions} 道题）"

        # 构建 文件名 → 持久路径 映射（用于将 AI 返回的图片文件名映射回实际路径）
        basename_to_path = {os.path.basename(p): p for p in images}

        if images:
            logger.info(f"文字模式+{len(images)}张嵌入图片（含位置标记）")

        batches = _build_text_batches(content, basename_to_path)
        if not batches:
            logger.warning("文本模式未切出有效题块，回退到整篇文本调用")
            batches = [{"content": content, "images": images}]

        for idx, batch in enumerate(batches, 1):
            logger.info(
                f"文本分片 {idx}/{len(batches)}: {len(batch['content'])} 字符, {len(batch['images'])} 张图片"
            )
            messages = _build_messages(batch["content"], batch["images"], grade)
            response = _call_api_with_retry(client, messages)
            batch_questions = _parse_api_response(response, source_file, image_refs)
            batch_questions = _attach_question_images(batch_questions, basename_to_path)
            all_questions.extend(batch_questions)

            if max_questions and len(all_questions) >= max_questions:
                break

    elif data_type == "images":
        images = parsed_data["images"]
        total_pages = len(images)
        logger.info(f"图片模式：共 {total_pages} 页，每批 {MAX_IMAGES_PER_REQUEST} 页")

        for batch_start in range(0, total_pages, MAX_IMAGES_PER_REQUEST):
            batch_end = min(batch_start + MAX_IMAGES_PER_REQUEST, total_pages)
            batch_images = images[batch_start:batch_end]

            page_range = f"第 {batch_start + 1} 页 到 第 {batch_end} 页"
            content = f"以下是从 {source_file} 的 {page_range} 提取的题目图片。"
            if max_questions:
                remaining = max_questions - len(all_questions)
                if remaining <= 0:
                    break
                content += f"（最多再提取 {remaining} 道题）"

            logger.info(f"处理 {page_range} ({len(batch_images)} 张图片)...")
            messages = _build_messages(content, batch_images, grade)
            response = _call_api_with_retry(client, messages)
            batch_questions = _parse_api_response(
                response, source_file,
                [os.path.basename(p) for p in batch_images]
            )

            for q in batch_questions:
                if q.get("source_page", 0) > 0:
                    q["source_page"] += batch_start

            all_questions.extend(batch_questions)
            logger.info(f"本批提取 {len(batch_questions)} 道题")

            if max_questions and len(all_questions) >= max_questions:
                break

    if max_questions and len(all_questions) > max_questions:
        all_questions = all_questions[:max_questions]

    logger.info(f"提取完成，共 {len(all_questions)} 道题")
    return all_questions
