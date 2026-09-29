"""Read-only DOCX forensic extraction for source-fidelity checks.

This module intentionally does not call an AI model. It preserves the visible
text order of normal Word runs and OMML math text so the migration pipeline can
compare legacy structured records with their original DOCX fragments.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from docx import Document


@dataclass(frozen=True)
class ParagraphFragment:
    index: int
    text: str
    sha256: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag else ""


def paragraph_visible_text(paragraph) -> str:
    """Extract Word text and OMML math text in document order.

    This is a fidelity baseline, not a LaTeX converter. It retains the visible
    symbols/characters embedded in OMML instead of silently dropping them as
    ``python-docx`` paragraph.text does.
    """
    parts: list[str] = []

    def walk(element) -> None:
        local = _local_name(element.tag)
        if local in {"t", "delText"} and element.text:
            parts.append(element.text)
            return
        if local == "tab":
            parts.append("\t")
            return
        if local in {"br", "cr"}:
            parts.append("\n")
            return
        for child in element:
            walk(child)

    walk(paragraph._element)
    return "".join(parts).strip()


def extract_paragraphs(path: str | Path) -> list[ParagraphFragment]:
    """Return non-empty document paragraphs with stable source indexes."""
    document = Document(str(path))
    fragments: list[ParagraphFragment] = []
    for index, paragraph in enumerate(document.paragraphs):
        text = paragraph_visible_text(paragraph)
        if text:
            fragments.append(
                ParagraphFragment(
                    index=index,
                    text=text,
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
    return fragments
