"""Low-risk deterministic consistency checks for review candidates.

These checks never prove a mathematical conclusion. They catch mechanical
contradictions before a candidate is presented for math and curriculum review.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ConsistencyFinding:
    code: str
    passed: bool
    detail: str


def check_candidate_consistency(
    *, question_type: str, options_json: str, answer: str, asset_paths: list[str]
) -> list[ConsistencyFinding]:
    findings: list[ConsistencyFinding] = []
    try:
        options = json.loads(options_json)
    except json.JSONDecodeError:
        options = None

    if not answer or not answer.strip():
        findings.append(ConsistencyFinding("answer_present", False, "答案为空"))
    else:
        findings.append(ConsistencyFinding("answer_present", True, "答案非空"))

    if question_type == "选择题":
        if not isinstance(options, list) or len(options) < 2:
            findings.append(ConsistencyFinding("choice_options", False, "选择题选项不足"))
        else:
            letters = {
                match.group(1)
                for option in options
                if (match := re.match(r"^\s*([A-F])[.．、]", str(option)))
            }
            answer_letter = answer.strip()[:1]
            passed = answer_letter in letters
            findings.append(
                ConsistencyFinding(
                    "choice_answer_matches_option",
                    passed,
                    "答案选项存在" if passed else f"答案 {answer_letter!r} 不在选项 {sorted(letters)} 中",
                )
            )
    elif question_type in {"填空题", "计算题", "解答题", "证明题", "综合题", "应用题"}:
        if options == []:
            findings.append(ConsistencyFinding("non_choice_options", True, "非选择题无选项"))
        else:
            findings.append(ConsistencyFinding("non_choice_options", False, "非选择题含未处理选项"))
    else:
        findings.append(ConsistencyFinding("question_type", False, f"题型未标准化：{question_type}"))

    for path in asset_paths:
        findings.append(
            ConsistencyFinding(
                "asset_exists",
                os.path.exists(path),
                f"图形资产存在：{path}" if os.path.exists(path) else f"图形资产缺失：{path}",
            )
        )
    return findings


def all_pass(findings: list[ConsistencyFinding]) -> bool:
    return all(finding.passed for finding in findings)
