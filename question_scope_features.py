from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

TERMS = ("三角形", "等腰三角形", "全等", "勾股", "函数", "方程", "不等式", "平行线", "相似", "圆", "角", "面积", "周长", "坐标")
CONDITION_TERMS = ("已知", "若", "当", "因为", "则", "求", "证明", "设")
SYMBOL_RE = re.compile(r"[=＋+－\-×*/÷<>≤≥≈√²³π∠△⊥∥]")

@dataclass(frozen=True)
class ScopeFeatures:
    status: str
    normalized_text: str
    input_hash: str
    question_type: str
    term_anchors: tuple[dict[str, Any], ...]
    symbol_anchors: tuple[dict[str, Any], ...]
    condition_anchors: tuple[dict[str, Any], ...]
    candidate_features: tuple[str, ...]
    reason: str


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def extract_question_scope_features(*, stem: object, options: object = "", answer: object = "", analysis: object = "", question_type: object = "") -> ScopeFeatures:
    stem_text = _text(stem)
    parts = {"stem": stem_text, "options": _text(options), "answer": _text(answer), "analysis": _text(analysis), "question_type": _text(question_type)}
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if not stem_text:
        return ScopeFeatures("unsupported", "", digest, parts["question_type"] or "unknown", (), (), (), (), "missing_stem")
    normalized = re.sub(r"\s+", "", "\n".join(v for v in parts.values() if v))
    term_anchors = tuple({"term": term, "start": match.start(), "end": match.end()} for term in TERMS for match in re.finditer(re.escape(term), normalized))
    symbol_anchors = tuple({"symbol": match.group(), "start": match.start(), "end": match.end()} for match in SYMBOL_RE.finditer(normalized))
    condition_anchors = tuple({"term": term, "start": match.start(), "end": match.end()} for term in CONDITION_TERMS for match in re.finditer(re.escape(term), normalized))
    features = tuple(sorted({x["term"] for x in term_anchors} | {"symbolic_expression" if symbol_anchors else ""} - {""}))
    status = "candidate" if features else "unknown"
    return ScopeFeatures(status, normalized, digest, parts["question_type"] or "unknown", term_anchors, symbol_anchors, condition_anchors, features, "auditable_features" if features else "no_recognized_math_signal")


def as_dict(features: ScopeFeatures) -> dict[str, Any]:
    return asdict(features)
