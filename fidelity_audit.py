"""Source-fidelity checks used before a candidate question can be reviewed.

The functions are deterministic and offline. They do not infer mathematics,
call a model, or approve a question. They only prove that stored values occur
in their recorded DOCX source fragments.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from docx_forensics import ParagraphFragment


@dataclass(frozen=True)
class FieldCheck:
    field_name: str
    passed: bool
    detail: str


def normalize_text(value: str) -> str:
    """Normalize whitespace that is not semantically meaningful in Word text."""
    return re.sub(r"\s+", "", value or "").replace("\u00a0", "")


def _join_fragments(fragments: dict[str, ParagraphFragment], source_ids: list[str]) -> str:
    return "\n".join(fragments[source_id].text for source_id in source_ids if source_id in fragments)


def _contains(source: str, expected: str) -> bool:
    return normalize_text(expected) in normalize_text(source)


def check_question_fields(
    *,
    stem: str,
    options_json: str,
    answer: str,
    analysis: str,
    question_type: str,
    fragments: dict[str, ParagraphFragment],
    provenance: dict[str, list[str]],
) -> list[FieldCheck]:
    """Check each stored field against all fragments recorded for that field."""
    checks: list[FieldCheck] = []

    for field_name, expected in (("stem", stem), ("answer", answer), ("analysis", analysis)):
        source_ids = provenance.get(field_name, [])
        source_text = _join_fragments(fragments, source_ids)
        if not source_ids:
            checks.append(FieldCheck(field_name, False, "source provenance is missing"))
        elif _contains(source_text, expected):
            checks.append(FieldCheck(field_name, True, "exact normalized text found"))
        else:
            checks.append(FieldCheck(field_name, False, "stored text differs from source fragment set"))

    try:
        options = json.loads(options_json)
    except json.JSONDecodeError:
        options = None
    option_source_ids = provenance.get("options", [])
    option_text = _join_fragments(fragments, option_source_ids)
    is_choice = question_type == "选择题"
    if not isinstance(options, list):
        checks.append(FieldCheck("options", False, "options are not a JSON array"))
    elif not is_choice and not options:
        checks.append(FieldCheck("options", True, "non-choice question has no options"))
    elif is_choice and not options:
        checks.append(FieldCheck("options", False, "choice question has no options"))
    elif not option_source_ids:
        checks.append(FieldCheck("options", False, "source provenance is missing"))
    elif all(_contains(option_text, option) for option in options):
        checks.append(FieldCheck("options", True, f"{len(options)} options found"))
    else:
        checks.append(FieldCheck("options", False, "one or more options differ from source fragment set"))

    return checks


def all_pass(checks: Iterable[FieldCheck]) -> bool:
    return all(check.passed for check in checks)
