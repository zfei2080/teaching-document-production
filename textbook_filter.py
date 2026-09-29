"""
教材版本范围过滤模块 + K-12 学段体系定义

提供全 K-12（小学→高中）学段体系的单一数据源，
以及基于 textbook_scope.json 的教材范围过滤功能。

学段常量（本模块为单一数据源，其他模块从这里导入）:
    ALL_GRADE_LEVELS  : 全部 38 个用户可见学段
    BASE_GRADE_MAP    : 用户学段 → 24 个基准学段（用于教材范围查询）
    STAGE_MAP         : 学段 → 大学段（小学/初中/高中）
    EQUIVALENT_GRADES : DB 查询等价学段映射
    BASE_SCOPE_LEVELS : 24 个基准学段列表

过滤用法:
    from textbook_filter import load_scope, is_in_scope, validate_textbook

    scope = load_scope("人教版", "七升八")  # BASE_GRADE_MAP 自动映射为 "七下"
    in_scope = is_in_scope("全等三角形的判定(SSS)", scope)  # True
    out_of_scope = is_in_scope("二次函数", scope)           # False
"""

import json
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

# 配置文件路径
_SCOPE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "textbook_scope.json")

# 缓存已加载的配置
_scope_cache: Optional[dict] = None

# 有效的教材版本
VALID_TEXTBOOKS = {"人教版", "北师大版"}

# 教材/学段硬禁用主题：即使 scope 命中也不放行
BLOCKED_SCOPE_KEYWORDS = {
    ("北师大版", "八下"): [
        "圆", "圆周", "圆弧", "扇形", "切线", "弧长", "圆锥", "圆柱", "内接于⊙", "直径"
    ],
}

# ============================================================
# 全 K-12 学段体系（单一数据源）
# ============================================================

# 全部用户可见学段（38 个）
ALL_GRADE_LEVELS = [
    # === 学期内学段 (24) ===
    # 小学 (12)
    "一上", "一下", "二上", "二下", "三上", "三下",
    "四上", "四下", "五上", "五下", "六上", "六下",
    # 初中 (6)
    "七上", "七下", "八上", "八下", "九上", "九下",
    # 高中 (6)
    "高一上", "高一下", "高二上", "高二下", "高三上", "高三下",

    # === 暑假衔接 (6) ===
    "小升初", "六升七", "七升八", "八升九", "初升高", "高二升高三",

    # === 寒假衔接 (5) ===
    "七上衔接七下", "八上衔接八下", "九上衔接九下",
    "高一上衔接高一下", "高二上衔接高二下",

    # === 通配 (3) ===
    "小学", "初中", "高中",
]

# 用户学段 → 教材范围基准学段（24 个基准）
# 衔接学段映射到"已完成的学期"，学期学段映射到自身，通配映射到自身
BASE_GRADE_MAP = {
    # 学期学段 → 自身
    "一上": "一上", "一下": "一下",
    "二上": "二上", "二下": "二下",
    "三上": "三上", "三下": "三下",
    "四上": "四上", "四下": "四下",
    "五上": "五上", "五下": "五下",
    "六上": "六上", "六下": "六下",
    "七上": "七上", "七下": "七下",
    "八上": "八上", "八下": "八下",
    "九上": "九上", "九下": "九下",
    "高一上": "高一上", "高一下": "高一下",
    "高二上": "高二上", "高二下": "高二下",
    "高三上": "高三上", "高三下": "高三下",

    # 暑假衔接 → 已完成的学期
    "小升初": "六下",
    "六升七": "六下",
    "七升八": "七下",
    "八升九": "八下",
    "初升高": "九下",
    "高二升高三": "高二下",

    # 寒假衔接 → 已完成的上学期
    "七上衔接七下": "七上",
    "八上衔接八下": "八上",
    "九上衔接九下": "九上",
    "高一上衔接高一下": "高一上",
    "高二上衔接高二下": "高二上",

    # 通配 → 自身
    "小学": "小学",
    "初中": "初中",
    "高中": "高中",
}

# 学段 → 大学段（小学/初中/高中）
STAGE_MAP = {
    # 小学
    "一上": "小学", "一下": "小学", "二上": "小学", "二下": "小学",
    "三上": "小学", "三下": "小学", "四上": "小学", "四下": "小学",
    "五上": "小学", "五下": "小学", "六上": "小学", "六下": "小学",
    "小升初": "小学", "六升七": "小学", "小学": "小学",

    # 初中
    "七上": "初中", "七下": "初中", "八上": "初中", "八下": "初中",
    "九上": "初中", "九下": "初中",
    "七升八": "初中", "八升九": "初中", "初升高": "初中",
    "七上衔接七下": "初中", "八上衔接八下": "初中", "九上衔接九下": "初中",
    "初中": "初中",

    # 高中
    "高一上": "高中", "高一下": "高中", "高二上": "高中", "高二下": "高中",
    "高三上": "高中", "高三下": "高中",
    "高二升高三": "高中",
    "高一上衔接高一下": "高中", "高二上衔接高二下": "高中",
    "高中": "高中",
}

# DB 查询等价学段映射
# 衔接学段 → [衔接学段, 基准学期]（IN 查询同时匹配新旧标签）
# 通配学段 → stage 字符串（按 stage 列过滤）
# 普通学期学段 → 无需映射（精确匹配自身）
EQUIVALENT_GRADES = {
    # 暑假衔接
    "小升初": ["小升初", "六下"],
    "六升七": ["六升七", "六下"],
    "七升八": ["七升八", "七下"],
    "八升九": ["八升九", "八下"],
    "初升高": ["初升高", "九下"],
    "高二升高三": ["高二升高三", "高二下"],

    # 寒假衔接
    "七上衔接七下": ["七上衔接七下", "七上"],
    "八上衔接八下": ["八上衔接八下", "八上"],
    "九上衔接九下": ["九上衔接九下", "九上"],
    "高一上衔接高一下": ["高一上衔接高一下", "高一上"],
    "高二上衔接高二下": ["高二上衔接高二下", "高二上"],

    # 通配
    "小学": "小学",
    "初中": "初中",
    "高中": "高中",
}

# 基准学段列表（textbook_scope.json 的键，24 个）
BASE_SCOPE_LEVELS = [
    "一上", "一下", "二上", "二下", "三上", "三下",
    "四上", "四下", "五上", "五下", "六上", "六下",
    "七上", "七下", "八上", "八下", "九上", "九下",
    "高一上", "高一下", "高二上", "高二下", "高三上", "高三下",
]


def _load_scope_data() -> dict:
    """加载 textbook_scope.json（带缓存）。"""
    global _scope_cache
    if _scope_cache is not None:
        return _scope_cache
    try:
        with open(_SCOPE_FILE, "r", encoding="utf-8") as f:
            _scope_cache = json.load(f)
        return _scope_cache
    except FileNotFoundError:
        logger.error(f"教材范围配置文件不存在: {_SCOPE_FILE}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"教材范围配置文件 JSON 解析错误: {e}")
        raise


def validate_textbook(textbook: str) -> None:
    """验证教材版本是否有效，无效则抛出 ValueError。"""
    if textbook not in VALID_TEXTBOOKS:
        raise ValueError(
            f"无效的教材版本: '{textbook}'，允许的值: {VALID_TEXTBOOKS}"
        )


def load_scope(textbook: str, grade_level: str) -> Optional[List[str]]:
    """获取指定教材版本+学段的允许知识点范围列表。

    通过 BASE_GRADE_MAP 将用户学段（如"七升八"）映射到基准学段（如"七下"），
    再从 textbook_scope.json 中查找对应范围。

    Args:
        textbook: 教材版本，如 "人教版" 或 "北师大版"
        grade_level: 用户学段，如 "七上"、"七升八"、"高一上"、"初中" 等

    Returns:
        允许的知识点短语列表；如果学段不在配置中则返回 None（表示跳过过滤）；
        如果为 ["*"] 则表示通配（匹配所有知识点）。

    Raises:
        ValueError: 教材版本无效
    """
    validate_textbook(textbook)
    data = _load_scope_data()

    # 查找该教材版本下的学段
    grade_scopes = data.get(textbook)
    if not grade_scopes:
        logger.warning(f"教材版本 '{textbook}' 在配置中不存在")
        return None

    # 通过 BASE_GRADE_MAP 将用户学段映射到基准学段
    base_level = BASE_GRADE_MAP.get(grade_level, grade_level)

    scope = grade_scopes.get(base_level)
    if scope is None:
        logger.info(
            f"学段 '{grade_level}' (基准: '{base_level}') "
            f"在教材 '{textbook}' 中无明确范围，跳过过滤"
        )
        return None

    return scope


def _contains_blocked_keyword(text: str, blocked_keywords: List[str]) -> bool:
    """判断知识点是否命中硬禁用关键词。"""
    if not text or not blocked_keywords:
        return False
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in blocked_keywords)


def is_in_scope(
    knowledge_point: str,
    scope_list: List[str],
    textbook: Optional[str] = None,
    grade_level: Optional[str] = None,
) -> bool:
    """判断知识点是否在允许范围内（模糊子串匹配 + 硬禁用主题）。

    模糊匹配规则：
    1. knowledge_point 包含 scope 短语 → 匹配
    2. scope 短语包含 knowledge_point → 匹配
    3. scope_list 为 ["*"] → 通配，全部匹配
    4. knowledge_point 为空 → 返回 False（严格过滤模式下不放行）
    5. 命中教材/学段硬禁用关键词 → 直接 False
    """
    blocked_keywords = BLOCKED_SCOPE_KEYWORDS.get((textbook, grade_level), [])
    if _contains_blocked_keyword(knowledge_point or "", blocked_keywords):
        return False

    if not scope_list:
        return True

    # knowledge_point 为空时，不再默认放行
    if not knowledge_point:
        return False

    # 通配符：仅在未命中硬禁用时放行
    if scope_list == ["*"]:
        return True

    kp_lower = knowledge_point.lower()
    for phrase in scope_list:
        phrase_lower = phrase.lower()
        if phrase_lower in kp_lower or kp_lower in phrase_lower:
            return True

    return False
