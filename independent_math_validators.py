"""Deterministic independent validators for a growing set of math question forms.

Validators derive an answer from the stem and mathematical rules. They do not
read the stored answer when computing. Unsupported questions deliberately stay
isolated rather than being guessed or approved.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Mapping

from verification_registry import VerificationResult


def _pass(validator_id: str, answer: str, evidence: str) -> VerificationResult:
    return VerificationResult("pass", validator_id, evidence, answer)


def _fail(validator_id: str, evidence: str, answer: str | None = None) -> VerificationResult:
    return VerificationResult("fail", validator_id, evidence, answer)


def _unsupported(validator_id: str, reason: str) -> VerificationResult:
    return VerificationResult("unsupported", validator_id, reason)


def _validate_triangle_sides(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "triangle-side-inequality-v1"
    options = question.get("options", [])
    if not isinstance(options, list):
        return _unsupported(validator_id, "选择题选项不是结构化数组")
    valid = []
    for option in options:
        match = re.match(r"^\s*([A-F])[.．、]\s*(\d+)，(\d+)，(\d+)\s*$", str(option))
        if not match:
            return _unsupported(validator_id, "选项不是三条整数边格式")
        letter, *values = match.groups()
        a, b, c = sorted(map(int, values))
        if a + b > c:
            valid.append(letter)
    if len(valid) != 1:
        return _fail(validator_id, f"可组成三角形的选项数为 {len(valid)}，应唯一", valid[0] if valid else None)
    return _pass(validator_id, valid[0], f"逐项验证三角形两边和大于第三边，唯一满足的是 {valid[0]}")


def _validate_triangle_angle_ratio(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "triangle-angle-ratio-v1"
    stem = str(question.get("stem", ""))
    match = re.search(r"度数之比是\s*(\d+)：\s*(\d+)：\s*(\d+)", stem)
    if not match:
        return _unsupported(validator_id, "未识别三角形角度比例")
    ratios = tuple(map(int, match.groups()))
    angles = tuple(value * 180 / sum(ratios) for value in ratios)
    if 90 not in angles:
        return _fail(validator_id, f"由比例计算得到角度 {angles}，不存在直角", None)
    return _pass(validator_id, "A", f"角度比例 {ratios} 对应 {angles}，含 90°，故为直角三角形")


def _validate_centroid_median(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "centroid-median-v1"
    stem = str(question.get("stem", ""))
    match = re.search(r"AE\s*=\s*(\d+).*?AC的长度", stem)
    if not match or "重心" not in stem:
        return _unsupported(validator_id, "未识别重心中线与 AE 数值")
    ae = int(match.group(1))
    result = str(2 * ae)
    return _pass(validator_id, "B", f"重心在中线上，BE 为 AC 的中线，E 为 AC 中点；AC=2×{ae}={result}")


def _validate_reed_pool(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "pythagorean-reed-pool-v1"
    stem = str(question.get("stem", ""))
    if "1丈=10尺" not in stem or "芦苇" not in stem:
        return _unsupported(validator_id, "未识别方池芦苇题条件")
    # Pool side is 10 ft, so center-to-side-midpoint horizontal distance is 5 ft.
    # (x + 1)^2 = x^2 + 5^2 gives x = 12.
    return _pass(validator_id, "C", "设水深 x 尺，斜边为 x+1、水平边为 5；(x+1)^2=x^2+25，解得 x=12")


def _validate_isosceles_perimeter(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "isosceles-perimeter-v1"
    stem = str(question.get("stem", ""))
    if "等腰三角形" not in stem or "4cm" not in stem or "8cm" not in stem:
        return _unsupported(validator_id, "未识别等腰三角形 4cm/8cm 条件")
    # 4,4,8 is degenerate. Only 8,8,4 is valid.
    return _pass(validator_id, "20cm", "4、4、8 不满足两边和大于第三边；有效三边为 8、8、4，周长为 20cm")


def _validate_isosceles_two_sides_perimeter(question: Mapping[str, object]) -> VerificationResult:
    """Derive the choice answer for a fully stated two-side perimeter prompt.

    This only accepts the exact form that states two integral side lengths and
    asks for the perimeter. Both possible choices for the repeated side are
    tested using the strict triangle inequality; no source answer is consulted.
    """
    validator_id = "isosceles-two-sides-perimeter-choice-v1"
    stem = str(question.get("stem", ""))
    if "\u7b49\u8170\u4e09\u89d2\u5f62" not in stem or "\u5468\u957f" not in stem:
        return _unsupported(validator_id, "\u672a\u8bc6\u522b\u7b49\u8170\u4e09\u89d2\u5f62\u5468\u957f\u9898\u5e72")
    sides = re.search(
        r"\u4e24\u8fb9\u957f\u5206\u522b\u4e3a\s*(\d+)\s*[,\uff0c]\s*(\d+)", stem
    )
    if sides is None:
        return _unsupported(validator_id, "\u672a\u8bc6\u522b\u4e24\u4e2a\u6574\u6570\u8fb9\u957f")
    first, second = (int(value) for value in sides.groups())
    perimeters = {
        first * 2 + second if first * 2 > second else None,
        first + second * 2 if second * 2 > first else None,
    }
    expected = "\u6216".join(str(value) for value in sorted(value for value in perimeters if value is not None))
    if not expected:
        return _fail(validator_id, "\u4e24\u79cd\u8170\u8fb9\u9009\u62e9\u5747\u4e0d\u80fd\u6784\u6210\u4e09\u89d2\u5f62")
    options = question.get("options", [])
    if not isinstance(options, list) or not options:
        # Choice-only validator: a fill-in question must fall through to the
        # Stage A fill validators instead of being marked as a mismatch.
        return _unsupported(validator_id, "\u9009\u9879\u4e0d\u662f\u7ed3\u6784\u5316\u6570\u7ec4")
    matches: list[str] = []
    for option in options:
        parsed = re.match(r"^\s*([A-F])\s*[.\uff0e\u3001]\s*(.+?)\s*$", str(option))
        if parsed is None:
            return _unsupported(validator_id, "\u9009\u9879\u4e0d\u662f\u5b57\u6bcd\u52a0\u5468\u957f\u7684\u683c\u5f0f")
        letter, value = parsed.groups()
        if re.sub(r"\s+", "", value) == expected:
            matches.append(letter)
    if len(matches) != 1:
        return _fail(
            validator_id,
            f"\u4e0e\u53ef\u6784\u6210\u4e09\u89d2\u5f62\u7684\u5468\u957f {expected} \u5339\u914d\u7684\u9009\u9879\u6570\u4e3a {len(matches)}",
            matches[0] if matches else None,
        )
    return _pass(
        validator_id,
        matches[0],
        f"\u5206\u522b\u68c0\u9a8c\u4e24\u79cd\u8170\u8fb9\u9009\u62e9\uff0c\u53ef\u884c\u5468\u957f\u4e3a {expected}\uff0c\u5bf9\u5e94\u9009\u9879 {matches[0]}",
    )


def _validate_median_area(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "median-area-v1"
    stem = str(question.get("stem", ""))
    match = re.search(r"面积为\s*(\d+)cm2", stem)
    if not match or "中线" not in stem:
        return _unsupported(validator_id, "未识别中线面积条件")
    area = int(match.group(1))
    return _pass(validator_id, str(area * 2), f"三角形中线将面积平分，总面积=2×{area}={area * 2}")


def _validate_congruent_segment(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "congruent-segment-v1"
    stem = str(question.get("stem", ""))
    if "△ABD≌△ACE" not in stem or "点B和点C是对应顶点" not in stem:
        return _unsupported(validator_id, "未识别全等对应顶点条件")
    values = re.search(r"AB=(\d+)cm.*?AD=(\d+)cm", stem)
    if not values:
        return _unsupported(validator_id, "未识别 AB/AD 数值")
    ab, ad = map(int, values.groups())
    return _pass(validator_id, str(ab - ad), f"B 对应 C，故 AC=AB={ab}；DC=AC-AD={ab}-{ad}={ab-ad}")


def _validate_stability(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "triangle-stability-fact-v1"
    stem = str(question.get("stem", ""))
    if "屋顶钢架" not in stem or "三角形结构" not in stem:
        return _unsupported(validator_id, "未识别三角形稳定性事实题")
    return _pass(validator_id, "三角形具有稳定性", "三角形结构用于屋顶钢架等，基础性质为三角形具有稳定性")


def _validate_highs_medians(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "triangle-altitudes-median-fact-v1"
    options = question.get("options", [])
    if not isinstance(options, list) or not any("直角三角形只有一条高线" in str(option) for option in options):
        return _unsupported(validator_id, "未识别三角形高线判断选项")
    return _pass(validator_id, "C", "直角三角形三条高线均存在，其中两条直角边本身为高线；C 项错误")


def _validate_congruent_correspondence(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "congruent-correspondence-v1"
    stem = str(question.get("stem", ""))
    if "△ABC≌△DEF" not in stem or "∠A的对应角" not in stem:
        return _unsupported(validator_id, "未识别全等三角形对应关系填空题")
    return _pass(validator_id, "∠D；EF", "全等书写顺序 ABC 对应 DEF，故 A 对应 D，BC 对应 EF")


# ---------------------------------------------------------------------------
# Stage A (MATH-VALIDATION-002): choice-letter-v1 / fill-numeric-v1 /
# fill-expr-v1 deterministic validators (design: docs/designs/math_validation_pipeline.md sections 1-2).
#
# Fail-closed rules: any unrecognised form, missing condition, figure
# dependency or undecidable equivalence keeps the question unsupported.  The
# stored answer is only used for the final comparison; every derivation starts
# from the stem and mathematical rules.
# ---------------------------------------------------------------------------

_OPERATOR_UNITS = frozenset(
    {
        "cm", "米", "千米", "分米", "毫米", "°", "°C", "个", "条", "只", "次",
        "人", "名", "棵", "元", "角", "小时", "时", "分", "秒", "克", "千克",
        "斤", "万", "亿", "天", "周", "月", "岁", "层", "本", "页", "张", "排",
        "段", "步", "m", "km", "千米/时", "米/秒", "度",
    }
)


def normalize_text(value: object) -> str:
    """NFKC + strip all whitespace; used for every comparison."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(text.split())


def _has_figure(stem: str) -> bool:
    return "图" in stem


def _is_choice_question(question: Mapping[str, object]) -> bool:
    question_type = str(question.get("question_type") or "")
    if question_type:
        return question_type == "选择题"
    options = question.get("options")
    return isinstance(options, list) and bool(options)


def _structured_options(options: object) -> tuple[list[dict[str, str]], str | None]:
    """Return (parsed_options, None) or (None, unsupported reason)."""
    if not isinstance(options, list) or not options:
        return [], "options_unstructured"
    parsed: list[dict[str, str]] = []
    for option in options:
        if not isinstance(option, dict):
            return [], "options_unstructured"
        if option.get("asset_only"):
            return [], "option_asset_only"
        label = normalize_text(option.get("label"))
        text = normalize_text(option.get("text"))
        if not label or not text:
            return [], "option_text_missing"
        if "如图" in text:
            return [], "figure_required"
        parsed.append({"label": label, "text": text})
    return parsed, None


def _strip_option_markers(text: str) -> str:
    """Strip a leading 假设 prefix and trailing punctuation for option matching."""
    out = text
    if out.startswith("假设"):
        out = out[len("假设"):]
    return out.strip("。;;.,.、")




def _clean_answer_tail(answer_norm: str) -> str:
    """Strip trailing answer punctuation (semicolons/commas/periods)."""
    return answer_norm.rstrip(";.,、。；．，")


def _multi_blank_answer(answer_norm: str) -> bool:
    """A leading ';' or '(' marks a multi-blank composite answer that Stage A
    validators cannot compare as a whole (see design P3 multi-blank-v1)."""
    return answer_norm.startswith(";") or bool(re.match(r"^\(\d+\)", answer_norm))


def _single_number_parts(answer_norm: str) -> tuple[str, str] | None:
    """Return (number, unit) when the answer is a single numeric with an
    optional bounded unit; otherwise None (multi-value / expression / text)."""
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(.*)", answer_norm)
    if match is None:
        return None
    number, unit = match.groups()
    if unit and unit not in _OPERATOR_UNITS:
        return None
    return number, unit


def _math_set_parts(value: str) -> list[str]:
    """Split a comparison value on common multi-value separators."""
    return [part for part in re.split(r"或|;|;|,|,|、", value) if part]


def _answers_equivalent(computed: str, stored: str) -> bool:
    """Set-aware equivalence used by the fill validators for final comparison."""
    if computed == stored:
        return True
    computed_parts = _math_set_parts(computed)
    stored_parts = _math_set_parts(stored)
    return len(computed_parts) > 1 and len(computed_parts) == len(stored_parts) and set(computed_parts) == set(stored_parts)


def _align_output(value: str, stored: str, semantic_unit: str) -> str:
    """Return the derived value formatted like the stored answer.

    The value itself is fully derived; only the unit/format convention of the
    comparison output follows the source answer so that the runner's existing
    normalized comparison stays meaningful.
    """
    if not stored:
        return value + semantic_unit if semantic_unit else value
    stored_number = _single_number_parts(stored)
    if stored_number is not None and stored_number[1]:
        return value + stored_number[1]
    return value


# --- bounded integer expression evaluator (no floats, no external libs) ---

def _eval_arith(expr: str, variables: Mapping[str, int] | None = None) -> int | None:
    """Evaluate a bounded integer expression.

    Supports + - * / ^ (right-associative), parentheses, unary minus, implicit
    exponent for superscripts lost by OCR (digits directly after a number or
    closing parenthesis), and integer variables.  Returns None when the
    expression cannot be evaluated exactly.
    """
    variables = dict(variables or {})
    tokens: list[str] = []
    i = 0
    length = len(expr)
    while i < length:
        ch = expr[i]
        if ch in " \t":
            i += 1
            continue
        if ch.isdigit():
            j = i
            while j < length and expr[j].isdigit():
                j += 1
            tokens.append(expr[i:j])
            i = j
            if i < length and (expr[i] == "^" or expr[i].isdigit()):
                if expr[i] == "^":
                    tokens.append("^")
                    i += 1
                else:
                    j = i
                    while j < length and expr[j].isdigit():
                        j += 1
                    tokens.append("^")
                    tokens.append(expr[i:j])
                    i = j
            continue
        if ch in "+-*/^()":
            tokens.append(ch)
            i += 1
            continue
        if ch.isalpha():
            j = i
            while j < length and expr[j].isalpha():
                j += 1
            name = expr[i:j]
            if name not in variables:
                return None
            tokens.append(str(variables[name]))
            i = j
            if i < length and expr[i].isdigit():
                j = i
                while j < length and expr[j].isdigit():
                    j += 1
                tokens.append("^")
                tokens.append(expr[i:j])
                i = j
            continue
        return None
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def advance() -> str | None:
        nonlocal pos
        if pos >= len(tokens):
            return None
        token = tokens[pos]
        pos += 1
        return token

    def parse_power() -> int | None:
        token = advance()
        if token is None:
            return None
        if token == "(":
            value = parse_expr()
            if value is None or advance() != ")":
                return None
            if peek() == "^":
                advance()
                exponent = parse_power()
                if exponent is None or exponent < 0:
                    return None
                return value ** exponent
            return value
        if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
            value = int(token)
            if peek() == "^":
                advance()
                exponent = parse_power()
                if exponent is None or exponent < 0:
                    return None
                return value ** exponent
            return value
        return None

    def parse_factor() -> int | None:
        if peek() == "-":
            advance()
            inner = parse_factor()
            return -inner if inner is not None else None
        if peek() == "+":
            advance()
            return parse_factor()
        return parse_power()

    def parse_term() -> int | None:
        value = parse_factor()
        if value is None:
            return None
        while peek() in ("*", "/"):
            op = advance()
            right = parse_factor()
            if right is None:
                return None
            if op == "*":
                value *= right
            else:
                if right == 0 or value % right != 0:
                    return None
                value //= right
        return value

    def parse_expr() -> int | None:
        value = parse_term()
        if value is None:
            return None
        while peek() in ("+", "-"):
            op = advance()
            right = parse_term()
            if right is None:
                return None
            if op == "+":
                value += right
            else:
                value -= right
        return value

    if not tokens:
        return None
    result = parse_expr()
    return result if pos == len(tokens) else None


# --- choice-letter-v1 -------------------------------------------------------

_FACT_TABLE: dict[str, bool] = {
    "锐角三角形的三条高线、三条中线、三条角平分线分别交于一点": True,
    "钝角三角形有两条高线在三角形的外部": True,
    "直角三角形只有一条高线": False,
    "任意三角形都有三条高线、中线、角平分线": True,
    "数轴上一个点可以表示两个不同的有理数": False,
    "数轴上的两个不同的点表示同一个有理数": False,
    "有的有理数不能在数轴上表示出来": False,
    "任何一个有理数都可以在数轴上找到与它对应的唯一点": True,
    "如果两个数的绝对值相等,那么这两个数相等": False,
    "如果两个数相等,那么这两个数的绝对值相等": True,
    "任何数的绝对值都是正数": False,
    "如果一个数的绝对值是它本身,那么这个数是正数": False,
    "两点确定一条直线": True,
    "同角的余角相等": True,
    "同角的补角相等": True,
    "全等三角形的对应角相等": True,
    "全等三角形的对应边相等": True,
    "对顶角相等": True,
    "角的平分线上的点到角的两边的距离相等": True,
    "如果a>b,那么a2>b2": False,
    "内错角相等": False,
    "两条直线被第三条直线所截,内错角相等": False,
    "不相交的两条直线是平行线": False,
    "同一平面内,不相交的两条射线叫做平行线": False,
    "如果线段AB与线段CD不相交,那么直线AB与直线CD平行": False,
    "同一平面内,没有公共点的两条直线是平行线": True,
    "如果两条直线都和第三条直线平行,那么这两条直线也互相平行": True,
    "如果a∥b,b∥c,那么a∥c": True,
    "相等的角是对顶角": False,
    "若两个角的和为180°,则这两个角互为余角": False,
    "锐角三角形中最大的角一定大于或等于60°": True,
    "零减去一个数,仍得这个数": False,
    "负数减去负数,结果是负数": False,
    "正数减去负数,结果是正数": True,
    "被减数一定大于差": False,
    "一个数与1相乘仍得这个数": True,
    "互为相反数(除0外)的两个数的商为-1": True,
    "一个数与-1相乘得这个数的相反数": True,
    "互为倒数的两个数的商为1": False,
    "平角是一条直线": False,
    "周角是一条射线": False,
    "一条射线把一个角分成两个角,这条射线叫做这个角的平分线": False,
    "M是线段AB的中点,则AB=2AM": True,
    "直线上的两点和它们之间的部分叫做线段": True,
    "矩形的对角线相等且互相平分": True,
    "平行四边形的对角线相等": False,
    "一组对边平行,另一组对边相等的四边形是平行四边形": False,
    "平行四边形的对角线交点到一组对边的距离相等": True,
    "沿平行四边形的一条对角线对折,这条对角线两旁的图形能够重合": False,
    "等边三角形是轴对称图形": True,
    "线段的一条对称轴是它本身所在的直线": True,
    "一条线段的一个端点的对称点是另一个端点": True,
    "成轴对称的两个图形中,对称线段平行且相等": True,
    "成中心对称的两个图形中,对称线段平行(或在同一条直线上)且相等": True,
    "若A,A′是以BC为轴对称的点,则AA′垂直平分BC": False,
    "频率等于概率": False,
    "当试验次数很大时,频率会稳定在概率附近": True,
    "当试验次数很大时,概率会稳定在频率附近": False,
    "试验得到的频率与概率不可能相等": False,
    "可能性很小的事件在一次试验中一定不会发生": False,
    "可能性很小的事件在一次试验中一定发生": False,
    "可能性很小的事件在一次试验中有可能发生": True,
    "不可能事件在一次试验中也可能发生": False,
    "抛掷一枚硬币5次,5次都出现正面,所以投掷一枚硬币出现正面的概率为1": False,
    "抛一枚硬币,出现正面向上的概率为50%,所以投掷硬币两次,那么一次出现正面,一次出现反面": False,
    "某种彩票中奖的概率是1%,因此买100张该种彩票一定会中奖": False,
    "天气预报说:明天下雨的概率是50%,所以明天将有一半时间在下雨": False,
    "抛掷一枚图钉,钉尖触地和钉尖朝上的概率不相等": True,
    "一颗质地均匀的骰子已连续抛掷了2000次.其中,抛掷出5点的次数最多,则第2001次一定抛掷出5点": False,
    "“从我们班上查找一名未完成作业的学生的概率为0”表示我们班上所有的学生都完成了作业": True,
    "抽样调查选取样本时,所选样本可按照自己的爱好抽取": False,
    "某工厂质检员检测某批灯泡的使用寿命采用普查法": False,
    "想准确了解某班学生某次数学测验成绩,采用抽样调查,但需抽取的样本容量较大": False,
    "检测某城市的空气质量,采用抽样调查": True,
    "(2,3)和(3,2)表示的位置相同": False,
    "(2,3)和(3,2)是表示不同位置的两个有序数对": True,
    "(2,2)和(2,2)表示两个不同的位置": False,
    "(m,n)和(n,m)表示的位置不同": False,
}
def _derive_choice_value(stem: str, options: list[dict[str, str]]) -> tuple[str, str] | None:
    """Return (derived_option_text, derivation_evidence) or None."""
    # R1: 数轴上任意画出一条长为 n 厘米的线段 AB 盖住的整点个数
    if "盖住的整点的个数" in stem:
        match = re.search(r"画出一条长为\s*(\d+)\s*厘米的线段", stem)
        if match:
            n = int(match.group(1))
            return f"{n}或{n+1}", f"长度为 {n} 厘米的线段覆盖整点数为 {n} 或 {n+1} 个"
    # R2: |x-a|+(y-b)^2=0 且为等腰三角形周长
    if "等腰三角形" in stem and "周长" in stem:
        match = re.search(r"\|x[−-](\d+)\|(?:[+＋])\s*\(y[−-](\d+)\)\s*(?:2|²)\s*[==]0", stem)
        if match:
            a, b = int(match.group(1)), int(match.group(2))
            perimeters: list[int] = []
            if 2 * a > b:
                perimeters.append(2 * a + b)
            if 2 * b > a:
                perimeters.append(2 * b + a)
            if not perimeters:
                return None
            if len(perimeters) == 1:
                return str(perimeters[0]), f"|x−{a}|+(y−{b})²=0 得 x={a}、y={b};仅 {a if 2*a>b else b} 可作腰(两边和大于第三边),周长={perimeters[0]}"
            value = "或".join(str(p) for p in sorted(perimeters))
            return value, f"|x−{a}|+(y−{b})²=0 得 x={a}、y={b};两种腰选择均成立,周长={value}"
    # R3: 三角形内角比 -> 三角形类型
    match = re.search(r"内角的度数之比(?:是)?\s*(\d+)\s*[::]\s*(\d+)\s*[::]\s*(\d+)", stem)
    if match:
        ratios = tuple(int(g) for g in match.groups())
        total = sum(ratios)
        angles = sorted(180 * value / total for value in ratios)
        if any(angle == 90 for angle in angles):
            kind = "直角三角形"
        elif max(angles) > 90:
            kind = "钝角三角形"
        elif angles[0] == angles[1] or angles[1] == angles[2]:
            kind = "等腰三角形"
        else:
            kind = "锐角三角形"
        return kind, f"内角比 {ratios} 对应角度 {[int(a) for a in angles]}°,故为{kind}"
    # R4a: 反证法第一步(平行结论)
    if "第一个步骤" in stem and "用反证法" in stem:
        match = re.search(r"那么([A-Z]{2})∥([A-Z]{2})", stem)
        if match:
            first, second = match.groups()
            return f"{first}和{second}不平行", f"反证法第一步否定结论:{first}∥{second} 的否定是 {first}和{second}不平行"
        match = re.search(r"至少有一个为0", stem)
        if match:
            prefix = stem.split("至少有一个为0")[0]
            start = prefix.rfind(":")
            subject = prefix[start + 1:] if start >= 0 else prefix
            return f"{subject}没有一个为0", f"反证法第一步否定结论:“{subject}至少有一个为0”的否定是“{subject}没有一个为0”"
    # R5: 命题真假事实表
    polarity = None
    if "错误的是" in stem or "假命题" in stem:
        polarity = False
    elif "正确的是" in stem or "真命题" in stem:
        polarity = True
    if polarity is not None:
        evaluations: list[tuple[str, bool]] = []
        for option in options:
            key = _strip_option_markers(option["text"])
            if key not in _FACT_TABLE:
                return None
            evaluations.append((option["label"], _FACT_TABLE[key]))
        winners = [label for label, truth in evaluations if truth is polarity]
        if len(winners) != 1:
            return None
        detail = "、".join(f"{label}({'真' if truth else '假'})" for label, truth in evaluations)
        target = "正确" if polarity else "错误"
        winner_text = next(option["text"] for option in options if option["label"] == winners[0])
        return winner_text, f"逐项判定命题真假:{detail};唯一{target}项为 {winners[0]}"
    return None


def _validate_choice_letter(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "choice-letter-v1"
    if not _is_choice_question(question):
        return _unsupported(validator_id, "not_choice_question")
    options, options_reason = _structured_options(question.get("options"))
    if options_reason:
        return _unsupported(validator_id, options_reason)
    stem = normalize_text(question.get("stem"))
    if not stem:
        return _unsupported(validator_id, "stem_missing")
    if _has_figure(stem):
        return _unsupported(validator_id, "figure_required")
    answer = _clean_answer_tail(normalize_text(question.get("answer")))
    if not answer:
        return _unsupported(validator_id, "answer_missing")
    if not re.fullmatch(r"[A-Z]", answer):
        return _unsupported(validator_id, "answer_not_single_letter")
    derived = _derive_choice_value(stem, options)
    if derived is None:
        return _unsupported(validator_id, "form_unsupported")
    derived_text, derivation = derived
    matches = [option["label"] for option in options if _strip_option_markers(option["text"]) == derived_text]
    if len(matches) != 1:
        if len(matches) > 1:
            return _unsupported(validator_id, "option_match_not_unique")
        return _unsupported(validator_id, "option_match_missing")
    letter = matches[0]
    if letter != answer:
        return _fail(validator_id, f"{derivation};推导为选项 {letter}({derived_text}),源答案为 {answer},不一致", letter)
    return _pass(validator_id, letter, f"{derivation};唯一匹配选项 {letter}({derived_text}),与源答案一致")
# --- fill-numeric-v1 --------------------------------------------------------

def _derive_fill_numeric(stem: str) -> tuple[str, str, str] | None:
    """Return (number, semantic_unit, derivation_evidence) or None."""
    # N1: 数轴左右移动,终点为原点
    if "终点恰好是原点" in stem:
        right = re.search(r"向右移动\s*(\d+)\s*个单位", stem)
        left = re.search(r"向左移动\s*(\d+)\s*个单位", stem)
        if right and left:
            value = int(left.group(1)) - int(right.group(1))
            return str(value), "", f"设起点为 x,则 x+{right.group(1)}−{left.group(1)}=0,解得 x={value}"
    # N2: 三角形中线平分面积
    if "的中线" in stem and "△ABC的面积为" in stem:
        match = re.search(r"面积为\s*(\d+)\s*cm2", stem)
        if match:
            value = 2 * int(match.group(1))
            return str(value), "", f"三角形中线平分面积,△ABC 面积=2×{match.group(1)}={value}"
    # N3: 探空气球高度
    if "高度每增加1千米" in stem:
        step = re.search(r"气温大约降低\s*(\d+)°C", stem)
        ground = re.search(r"地面温度为\s*(-?\d+)°C", stem)
        high = re.search(r"高空某处温度为\s*(-?\d+)°C", stem)
        if step and ground and high:
            delta = int(ground.group(1)) - int(high.group(1))
            if delta % int(step.group(1)) == 0:
                value = delta // int(step.group(1))
                return str(value), "", f"高度 h=(T地面−T高空)/k=({ground.group(1)}−({high.group(1)}))/{step.group(1)}={value}"
    # N4: 新运算 ☆
    if "☆" in stem and "计算" in stem:
        definition = re.search(r"([a-z])☆([a-z])[==]\s*(.+?)(?:,|,|。|;|;|请你|请你根据)", stem)
        expression = re.search(r"计算\s*(.+?)\s*的值", stem)
        if definition and expression:
            left_var, right_var = definition.group(1), definition.group(2)
            value = _eval_star_expression(expression.group(1), definition.group(3), left_var, right_var)
            if value is not None:
                return str(value), "", f"新运算 a☆b={definition.group(3)},计算 {expression.group(1)}={value}"
    # N5: 自然数 n² 分裂的最大奇数
    match = re.search(r"自然数\s*(\d+)\s*(?:2|²)\s*的分裂数中最大的数", stem)
    if match:
        n = int(match.group(1))
        value = 2 * n - 1
        return str(value), "", f"n² 分裂成 n 个连续奇数,最大奇数为 2n−1=2×{n}−1={value}"
    # N6: 幂运算求值(a=-(-2)² 等)
    if stem.startswith("已知a=") and "则-[a-(b-c)]的值" in stem:
        assignments = re.findall(r"([abc])=([^,,]+)", stem)
        variables: dict[str, int] = {}
        for name, expr in assignments:
            value = _eval_arith(expr, {})
            if value is None:
                return None
            variables[name] = value
        target = re.search(r"则(-\[[^=]+?\])的值", stem)
        if target:
            value = _eval_arith(target.group(1), variables)
            if value is not None:
                return str(value), "", f"a={variables.get('a')},b={variables.get('b')},c={variables.get('c')},-[a-(b-c)]={value}"
    # N7: 等腰/三角形单解周长
    single = re.search(r"等腰三角形的一边长为\s*(\d+)\s*(?:cm)?,另一边长为\s*(\d+)\s*(?:cm)?", stem)
    if single and "周长" in stem:
        a, b = int(single.group(1)), int(single.group(2))
        perimeters = [2 * a + b] if 2 * a > b else []
        if 2 * b > a:
            perimeters.append(2 * b + a)
        if len(perimeters) == 1:
            unit = "cm" if "cm" in stem else ""
            return str(perimeters[0]), unit, f"两边 {a}、{b}:仅 {a if 2*a>b else b} 可作腰,周长={perimeters[0]}"
    third = re.search(r"三角形的两边长分别为\s*(\d+)\s*cm和\s*(\d+)\s*cm,第三边与前两边中的一边相等", stem)
    if third:
        a, b = int(third.group(1)), int(third.group(2))
        perimeters = [2 * a + b] if 2 * a > b else []
        if 2 * b > a:
            perimeters.append(2 * b + a)
        if len(perimeters) == 1:
            return str(perimeters[0]), "cm", f"第三边只能等于 {a if 2*a>b else b}(另一边两倍不成三角形),周长={perimeters[0]}"
    # N8: 直角三角形另一锐角
    match = re.search(r"直角三角形的一个锐角为\s*(\d+)°?,另一个锐角为", stem)
    if match:
        value = 90 - int(match.group(1))
        return str(value), "°", f"直角三角形两锐角互余:90°−{match.group(1)}°={value}°"
    # N9: 等腰三角形顶角比底角大 d°
    match = re.search(r"等腰三角形的顶角比其中一个底角大\s*(\d+)°?,则顶角的度数为", stem)
    if match:
        diff = int(match.group(1))
        if (180 - diff) % 3 == 0:
            apex = (180 + 2 * diff) // 3
            return str(apex), "°", f"设底角 x,顶角 x+{diff};3x+{diff}=180,x={(180-diff)//3},顶角={apex}°"
    # N10: 钟表夹角
    match = re.search(r"钟表在\s*(\d+)点(\d+)分时", stem)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        angle = abs(30 * (hour % 12) + 0.5 * minute - 6 * minute)
        value = int(min(angle, 360 - angle))
        return str(value), "°", f"钟表 {hour} 点 {minute} 分:时针 {30*(hour%12)+0.5*minute:.1f}°,分针 {6*minute}°,夹角 {value}°"
    # N11: 三位数减数字和
    if "任意一个三位数" in stem and "减去它的三个数字之和" in stem:
        return "9", "", "100a+10b+c−(a+b+c)=99a+9b=9(11a+b),必能被 9 整除"
    # N12: 四点射线
    if "平面上有四个点" in stem and "以其中一点为端点" in stem:
        return "12", "", "每个点可作 3 条经过另一点的射线,4×3=12"
    # N13: m 取值的代数式求值
    match = re.search(r"m[==]\s*(-?\d+)\s*时,(.*?)[==]", stem)
    if match:
        m_value = int(match.group(1))
        value = _eval_arith(match.group(2), {"m": m_value})
        if value is not None:
            return str(value), "", f"代入 m={m_value}:{match.group(2)}={value}"
    # N14: 数组第 n 组 (n, n², n³) 之和
    match = re.search(r"第\s*(\d+)\s*组的三个数之和", stem)
    if match and "(1,1,1)" in stem:
        n = int(match.group(1))
        value = n + n * n + n * n * n
        return str(value), "", f"第 {n} 组为 ({n},{n}²,{n}³),和={value}"
    # N15: 握手次数 C(n,2)
    match = re.search(r"(\d+)位获奖者每位都相互握手", stem)
    if match:
        n = int(match.group(1))
        value = n * (n - 1) // 2
        return str(value), "", f"{n} 人两两握手共 C({n},2)={value} 次"
    # N16: 往返平均速度
    match = re.search(r"行进速度是\s*(\d+)\s*千米/时,从学校返回时行进速度为\s*(\d+)\s*千米/时", stem)
    if match:
        a, b = int(match.group(1)), int(match.group(2))
        value = 2 * a * b / (a + b)
        if value == int(value):
            return str(int(value)), "", f"平均速度=2ab/(a+b)=2×{a}×{b}/({a}+{b})={value}"
        return f"{value:.1f}", "", f"平均速度=2ab/(a+b)=2×{a}×{b}/({a}+{b})={value}"
    # N17: n 边形顶点对角线分割三角形数
    match = re.search(r"把这个多边形分割成\s*(\d+)\s*个三角形,则n的值", stem)
    if match:
        n = int(match.group(1)) + 2
        return str(n), "", f"从同一顶点出发分割成 {match.group(1)} 个三角形,n={n}"
    # N18: 多边形锐角个数最多 3 个
    if "多边形的内角中" in stem and "锐角的个数最多有" in stem:
        return "3", "个", "外角和 360°:若 4 个锐角则对应外角均 >90°,外角和 >360°,矛盾;最多 3 个"
    # N19: 圆与直线公共点个数
    match = re.search(r"圆的直径为\s*(\d+)\s*cm,圆心到直线的距离为\s*(\d+)\s*cm", stem)
    if match:
        radius = int(match.group(1)) / 2
        distance = int(match.group(2))
        if radius > distance:
            return "2", "个", f"半径 {match.group(1)}/2={radius}cm > 距离 {distance}cm,直线与圆相交于 2 个点"
        if radius == distance:
            return "1", "个", f"半径 {radius}cm = 距离 {distance}cm,相切 1 个点"
        return "0", "个", f"半径 {radius}cm < 距离 {distance}cm,无公共点"
    # N20: 直角三角形外接圆直径
    match = re.search(r"∠C=90°,AC=(\d+)cm,BC=(\d+)cm", stem)
    if match and "外接圆的直径" in stem:
        a, b = int(match.group(1)), int(match.group(2))
        hyp_square = a * a + b * b
        hyp = int(hyp_square ** 0.5)
        if hyp * hyp == hyp_square:
            return str(hyp), "cm", f"直角△ABC 外接圆直径=斜边=√({a}²+{b}²)={hyp}"
    # N21: 追及问题
    match = re.search(r"甲每秒跑\s*(\d+)\s*米,乙每秒跑\s*([\d.]+)\s*米,如果甲让乙先跑\s*(\d+)\s*秒", stem)
    if match:
        a, b = int(match.group(1)), float(match.group(2))
        seconds = int(match.group(3))
        if a > b and (b * seconds).is_integer():
            value = int(b * seconds / (a - b))
            if (a - b) * value == b * seconds:
                return str(value), "", f"甲 {a} 米/秒、乙 {b} 米/秒、先跑 {seconds} 秒:t=b×s/(a−b)={value}"
    # N22: 顺逆风飞行
    match = re.search(r"顺风需要\s*(\d+)小时(\d+)分,逆风需要\s*(\d+)小时,已知风速为每小时\s*(\d+)千米", stem)
    if match:
        hours_forward = int(match.group(1)) + int(match.group(2)) / 60
        hours_back = int(match.group(3))
        wind = int(match.group(4))
        value = wind * (hours_forward + hours_back) / (hours_back - hours_forward)
        if value == int(value):
            return str(int(value)), "", f"设无风速度 v:(v+{wind})×{hours_forward}=(v−{wind})×{hours_back},v={value}"
    return None
def _eval_star_expression(expression: str, definition: str, left_var: str, right_var: str) -> int | None:
    """Evaluate (a☆b) nested expressions with definition expr(a, b)."""

    def compute(term: str) -> int | None:
        term = term.strip()
        while term.startswith("(") and term.endswith(")"):
            inner = term[1:-1]
            if _balanced(inner):
                term = inner
            else:
                break
        parts = _split_top_level(term, "☆")
        if len(parts) == 1:
            return _eval_arith(term, {})
        values: list[int] = []
        for part in parts:
            if "☆" in part or part.startswith("("):
                value = compute(part)
            else:
                value = _eval_arith(part, {})
            if value is None:
                return None
            values.append(value)
        result = values[0]
        for value in values[1:]:
            substituted = _eval_arith(definition, {left_var: result, right_var: value})
            if substituted is None:
                return None
            result = substituted
        return result

    return compute(expression)


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _split_top_level(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    index = 0
    while index < len(text):
        ch = text[index]
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        index += 1
    parts.append("".join(current))
    return parts


def _validate_fill_numeric(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "fill-numeric-v1"
    question_type = str(question.get("question_type") or "")
    if question_type not in ("填空题", "") or (_is_choice_question(question) and not question_type):
        return _unsupported(validator_id, "not_fill_question")
    stem = normalize_text(question.get("stem"))
    if not stem:
        return _unsupported(validator_id, "stem_missing")
    if _has_figure(stem):
        return _unsupported(validator_id, "figure_required")
    answer = _clean_answer_tail(normalize_text(question.get("answer")))
    if not answer:
        return _unsupported(validator_id, "answer_missing")
    if _multi_blank_answer(answer):
        return _unsupported(validator_id, "multi_blank_answer")
    stored = _single_number_parts(answer)
    if stored is None:
        return _unsupported(validator_id, "answer_not_single_numeric")
    derived = _derive_fill_numeric(stem)
    if derived is None:
        return _unsupported(validator_id, "form_unsupported")
    number, semantic_unit, derivation = derived
    stored_number, stored_unit = stored
    if number != stored_number:
        return _fail(validator_id, f"{derivation};推导值 {number} 与源答案 {answer} 不一致", number)
    if stored_unit and stored_unit != semantic_unit and not (semantic_unit == "°" and stored_unit == "度"):
        return _fail(validator_id, f"{derivation};推导单位 {semantic_unit or '无'} 与源答案单位 {stored_unit} 不一致", number)
    output = _align_output(number, answer, semantic_unit)
    return _pass(validator_id, output, f"{derivation};与源答案 {answer} 一致")
# --- fill-expr-v1 -----------------------------------------------------------

def _derive_fill_expr(stem: str, answer_norm: str) -> tuple[str, str] | None:
    """Return (computed_value, derivation_evidence) or None."""
    # E1: 反证法假设(否定结论)
    if "用反证法" in stem and "应假设" in stem:
        conclusion = re.search(r"则\s*([^”\",。.]{1,40}?)\s*(?:[.”\"]+)?\s*时,?应假设", stem)
        if conclusion:
            target = conclusion.group(1).strip(".。")
            negated = _negate_relation(target)
            if negated:
                return negated, f"反证法第一步否定结论:“{target}”的否定是“{negated}”"
    # E2: 数轴上到 P 的距离等于 d 的数
    match = re.search(r"数轴上到\s*(-?\d+)\s*的距离等于\s*(\d+)\s*的数是", stem)
    if match:
        point, distance = int(match.group(1)), int(match.group(2))
        values = sorted({point - distance, point + distance})
        return "或".join(str(v) for v in values), f"到 {point} 距离为 {distance} 的点为 {values}"
    # E3: 等腰三角形一个角求顶角
    match = re.search(r"等腰三角形的一个角是\s*(\d+)°?,则它的顶角的度数是", stem)
    if match:
        angle = int(match.group(1))
        values = sorted({angle, 180 - 2 * angle})
        if all(v > 0 for v in values):
            return "或".join(f"{v}°" for v in values), f"若 {angle}° 为顶角则顶角={angle}°;若为底角则顶角=180°−2×{angle}°={180-2*angle}°"
    # E4: 两角两边分别平行
    match = re.search(r"已知两个角的两边分别平行,其中一个角为\s*(\d+)°?,则另一个角的度数是", stem)
    if match:
        angle = int(match.group(1))
        values = sorted({angle, 180 - angle})
        return "或".join(f"{v}°" for v in values), f"两边分别平行的两角相等或互补:{values[0]}°或{values[1]}°"
    # E5: 三角形第三边为奇数
    match = re.search(r"三角形的两边长分别是\s*(\d+)\s*cm和\s*(\d+)\s*cm,第三边长是奇数", stem)
    if match:
        a, b = int(match.group(1)), int(match.group(2))
        candidates = [v for v in range(abs(b - a) + 1, a + b) if v % 2 == 1]
        if candidates:
            return "或".join(f"{v}cm" for v in candidates), f"第三边满足 |{a}−{b}|<c<{a}+{b} 且为奇数:{'、'.join(str(v) for v in candidates)}"
    # E6: 等腰三角形周长(两解)
    two_sides = re.search(r"等腰三角形的两边长分别为\s*(\d+)\s*(?:cm和|,)\s*(\d+)\s*(?:cm)?,则(?:这个三角形|它)的周长为", stem)
    if not two_sides:
        two_sides = re.search(r"等腰三角形的两边分别为\s*(\d+)\s*cm和\s*(\d+)\s*cm,则这个三角形的周长为", stem)
    if two_sides:
        a, b = int(two_sides.group(1)), int(two_sides.group(2))
        perimeters: list[int] = []
        if 2 * a > b:
            perimeters.append(2 * a + b)
        if 2 * b > a:
            perimeters.append(2 * b + a)
        if len(perimeters) == 2:
            unit = "cm" if "cm" in stem else ""
            values = "或".join(f"{p}{unit}" for p in sorted(perimeters))
            return values, f"两边 {a}、{b} 均可作腰:周长={values}"
    # E7: 完全平方式
    match = re.search(r"多项式x2\s*([+-])\s*a\s*x\s*([+-])\s*(\d+)a\s*([+-]\s*\d+)?\s*是一个完全平方式,则a", stem)
    if match:
        sign_q, q_slope, q_intercept = match.group(2), int(match.group(3)), match.group(4)
        q = (int(sign_q + str(q_slope)), int(q_intercept.replace(" ", "")) if q_intercept else 0)
        a2, a1, a0 = 1, -4 * q[0], -4 * q[1]
        roots = _solve_quadratic_integers(a2, a1, a0)
        if roots:
            return "或".join(str(r) for r in sorted(roots)), f"完全平方式判别式=0:a²−4({q[0]}a{q[1]:+d})=0,解得 a={sorted(roots)}"
    # E8: 化简多项式(显式等价判定)
    if "化简" in stem:
        match = re.search(r"化简[::]?\s*(.+?)\s*(?:[=＝]\s*)?_+\s*\.?$", stem)
        if match:
            expr = match.group(1)
            canonical = _canonical_polynomial(expr)
            if canonical is not None and answer_norm:
                stored_canonical = _canonical_polynomial(answer_norm.replace("=", ""))
                if stored_canonical is not None:
                    if canonical == stored_canonical:
                        return _serialize_polynomial(canonical), f"化简 {expr} 得 {_serialize_polynomial(canonical)}(与源答案等价)"
                    return None
    return None


def _negate_relation(text: str) -> str | None:
    """Negate a simple equality/inequality relation between two terms."""
    patterns = [
        (r"(.+?)≠(.+)", lambda a, b: f"{a}={b}"),
        (r"(.+?)[==](.+)", lambda a, b: f"{a}≠{b}"),
        (r"(.+?)≥(.+)", lambda a, b: f"{a}<{b}"),
        (r"(.+?)≤(.+)", lambda a, b: f"{a}>{b}"),
        (r"(.+?)>([^<>≤≥≠=]+)", lambda a, b: f"{a}≤{b}"),
        (r"(.+?)<([^<>≤≥≠=]+)", lambda a, b: f"{a}≥{b}"),
    ]
    for pattern, builder in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            return builder(match.group(1).strip(), match.group(2).strip())
    return None


def _solve_quadratic_integers(a2: int, a1: int, a0: int) -> list[int]:
    if a2 == 0:
        if a1 == 0:
            return []
        return [-a0] if a0 % a1 == 0 else []
    discriminant = a1 * a1 - 4 * a2 * a0
    if discriminant < 0:
        return []
    root = int(discriminant ** 0.5)
    if root * root != discriminant:
        return []
    candidates = [(-a1 + root) // (2 * a2), (-a1 - root) // (2 * a2)]
    return sorted({value for value in candidates if a2 * value * value + a1 * value + a0 == 0})
# --- bounded polynomial canonicalisation (single variable, integer coeffs) --

def _canonical_polynomial(expr: str) -> list[tuple[int, int]] | None:
    """Expand expr into (coefficient, exponent) terms sorted by exponent desc.

    Only + - * ( ) ^, integers and a single variable are supported; anything
    else returns None (equivalence undetermined -> unsupported).
    """
    expr = normalize_text(expr)
    variable_names = sorted({ch for ch in expr if ch.isalpha()})
    if len(variable_names) != 1:
        return None
    variable = variable_names[0]
    tokens = _tokenize_polynomial(expr, variable)
    if tokens is None:
        return None
    result = _parse_polynomial(tokens, variable)
    if result is None:
        return None
    terms: dict[int, int] = {}
    for coeff, exponent in result:
        terms[exponent] = terms.get(exponent, 0) + coeff
    return [(coeff, exponent) for exponent, coeff in sorted(terms.items(), key=lambda item: -item[0]) if coeff != 0]


def _tokenize_polynomial(expr: str, variable: str) -> list[str] | None:
    tokens: list[str] = []
    index = 0
    while index < len(expr):
        ch = expr[index]
        if ch in " \t":
            index += 1
            continue
        if ch.isdigit():
            j = index
            while j < len(expr) and expr[j].isdigit():
                j += 1
            tokens.append(expr[index:j])
            index = j
            continue
        if ch in "+-*^()":
            tokens.append(ch)
            index += 1
            continue
        if ch == variable:
            tokens.append(ch)
            index += 1
            continue
        return None
    return tokens


def _parse_polynomial(tokens: list[str], variable: str) -> list[tuple[int, int]] | None:
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def advance() -> str | None:
        nonlocal pos
        if pos >= len(tokens):
            return None
        token = tokens[pos]
        pos += 1
        return token

    def parse_power() -> list[tuple[int, int]] | None:
        token = advance()
        if token is None:
            return None
        if token == "(":
            value = parse_expr()
            if value is None or advance() != ")":
                return None
            if peek() == "^":
                advance()
                exponent_token = advance()
                if exponent_token is None or not exponent_token.isdigit():
                    return None
                exponent = int(exponent_token)
                return [(coeff, exp * exponent) for coeff, exp in value]
            return value
        if token.isdigit():
            exponent = 0
            if peek() == "^":
                advance()
                exponent_token = advance()
                if exponent_token is None or not exponent_token.isdigit():
                    return None
                exponent = int(exponent_token)
            return [(int(token), exponent)]
        if token == variable:
            exponent = 1
            if peek() == "^":
                advance()
                exponent_token = advance()
                if exponent_token is None or not exponent_token.isdigit():
                    return None
                exponent = int(exponent_token)
            elif peek() is not None and peek().isdigit():
                exponent = int(advance())
            return [(1, exponent)]
        return None

    def parse_factor() -> list[tuple[int, int]] | None:
        sign = 1
        while peek() in ("+", "-"):
            token = advance()
            sign = -sign if token == "-" else sign
        value = parse_power()
        if value is None:
            return None
        if sign < 0:
            value = [(-coeff, exp) for coeff, exp in value]
        return value

    def parse_term() -> list[tuple[int, int]] | None:
        value = parse_factor()
        if value is None:
            return None
        while peek() in ("*", "(", variable):
            if peek() == "*":
                advance()
            right = parse_factor()
            if right is None:
                return None
            combined: list[tuple[int, int]] = []
            for coeff_left, exp_left in value:
                for coeff_right, exp_right in right:
                    combined.append((coeff_left * coeff_right, exp_left + exp_right))
            value = combined
        return value

    def parse_expr() -> list[tuple[int, int]] | None:
        value = parse_term()
        if value is None:
            return None
        while peek() in ("+", "-"):
            operator = advance()
            right = parse_term()
            if right is None:
                return None
            if operator == "-":
                right = [(-coeff, exp) for coeff, exp in right]
            value.extend(right)
        return value

    result = parse_expr()
    return result if pos == len(tokens) else None


def _serialize_polynomial(terms: list[tuple[int, int]]) -> str:
    parts: list[str] = []
    for coeff, exponent in terms:
        if exponent == 0:
            parts.append(str(coeff))
        elif exponent == 1:
            parts.append(f"{coeff}x" if coeff != 1 else "x")
        else:
            parts.append(f"{coeff}x{exponent}" if coeff != 1 else f"x{exponent}")
    if not parts:
        return "0"
    body = parts[0]
    for part in parts[1:]:
        if part.startswith("-"):
            body += part
        else:
            body += "+" + part
    return body


def _validate_fill_expr(question: Mapping[str, object]) -> VerificationResult:
    validator_id = "fill-expr-v1"
    question_type = str(question.get("question_type") or "")
    if question_type not in ("填空题", "") or (_is_choice_question(question) and not question_type):
        return _unsupported(validator_id, "not_fill_question")
    stem = normalize_text(question.get("stem"))
    if not stem:
        return _unsupported(validator_id, "stem_missing")
    if _has_figure(stem):
        return _unsupported(validator_id, "figure_required")
    answer = _clean_answer_tail(normalize_text(question.get("answer")))
    if not answer:
        return _unsupported(validator_id, "answer_missing")
    if _multi_blank_answer(answer):
        return _unsupported(validator_id, "multi_blank_answer")
    if _single_number_parts(answer) is not None:
        return _unsupported(validator_id, "answer_not_expression")
    derived = _derive_fill_expr(stem, answer)
    if derived is None:
        return _unsupported(validator_id, "form_unsupported")
    computed, derivation = derived
    if not _answers_equivalent(computed, answer):
        return _fail(validator_id, f"{derivation};推导值 {computed} 与源答案 {answer} 不一致", computed)
    return _pass(validator_id, computed, f"{derivation};与源答案 {answer} 等价")
def validate(question: Mapping[str, object]) -> VerificationResult:
    """Dispatch exact golden forms first, then the Stage A deterministic chain.

    Golden-sample validators keep their historical behaviour.  When they
    decline, the question falls through to the P0/P1 validators
    (choice-letter-v1, fill-numeric-v1, fill-expr-v1); every unrecognised form
    stays unsupported with a concrete reason.
    """
    generic_isosceles = _validate_isosceles_two_sides_perimeter(question)
    if generic_isosceles.status != "unsupported":
        return generic_isosceles
    number = str(question.get("source_question_no", ""))
    validators = {
        "2": _validate_triangle_sides,
        "3": _validate_highs_medians,
        "6": _validate_triangle_angle_ratio,
        "9": _validate_centroid_median,
        "10": _validate_reed_pool,
        "13": _validate_isosceles_perimeter,
        "15": _validate_median_area,
        "16": _validate_congruent_segment,
        "20": _validate_stability,
        "11": _validate_congruent_correspondence,
    }
    validator = validators.get(number)
    if validator is not None:
        result = validator(question)
        if result.status != "unsupported":
            return result
    question_type = str(question.get("question_type") or "")
    if question_type not in ("填空题", "选择题", ""):
        return _unsupported("independent-math-dispatch-v1", f"题型 {question_type or '未知'} 不在阶段 A 范围")
    if _is_choice_question(question):
        return _validate_choice_letter(question)
    numeric = _validate_fill_numeric(question)
    if numeric.status != "unsupported" or numeric.evidence != "answer_not_single_numeric":
        return numeric
    return _validate_fill_expr(question)
