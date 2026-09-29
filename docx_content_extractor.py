"""Read-only pure-Python DOCX extraction for source-content database import.

Produces the same payload contract as ``word_com_content_extractor`` for modern
``.docx`` (ZIP/OOXML) files without launching Word COM, so source-fidelity
imports do not depend on a Word installation or an interactive Windows session.
Only ``.docx`` is supported; legacy binary ``.doc`` stays on the Word COM path.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any

import docx

SCHEMA = "word-com-content-extraction-v1"
ENGINE_ID = "python-docx + OOXML package scan"

_WP_INLINE = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline"
_WP_ANCHOR = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}anchor"
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_M_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_V_NS = "{urn:schemas-microsoft-com:vml}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

_A_BLIP = _A_NS + "blip"
_V_IMAGEDATA = _V_NS + "imagedata"
_R_EMBED = _R_NS + "embed"
_R_LINK = _R_NS + "link"
_R_ID = _R_NS + "id"


def _resolve_media(document, element) -> dict[str, str] | None:
    """Return (package_reference, asset_sha256) for a drawing/pict's media part."""
    rids: list[str] = []
    blip = element.find(".//" + _A_BLIP)
    if blip is not None:
        for attr in (_R_EMBED, _R_LINK):
            rid = blip.get(attr)
            if rid:
                rids.append(rid)
    imagedata = element.find(".//" + _V_IMAGEDATA)
    if imagedata is not None:
        rid = imagedata.get(_R_ID)
        if rid:
            rids.append(rid)
    for rid in rids:
        part = document.part.related_parts.get(rid)
        if part is None:
            continue
        return {
            "package_reference": str(part.partname),
            "asset_sha256": hashlib.sha256(part.blob).hexdigest(),
        }
    return None


class DocxContentExtractionError(RuntimeError):
    """Raised when a .docx cannot be extracted without modification."""


_WS_RE = re.compile(r"[\s ]+")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    """Mirror the Word COM extractor's Normalize-WordText (strip whitespace/nbsp/bell)."""
    return _WS_RE.sub("", value)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag else ""


def _element_visible_text(element) -> str:
    """Word text and OMML math text in document order, like docx_forensics."""
    parts: list[str] = []

    def walk(node) -> None:
        local = _local(node.tag)
        if local in {"t", "delText"} and node.text:
            parts.append(node.text)
            return
        if local == "tab":
            parts.append("\t")
            return
        if local in {"br", "cr"}:
            parts.append("\n")
            return
        for child in node:
            walk(child)

    walk(element)
    return "".join(parts).strip()


def _extract_docx(path: Path) -> dict[str, Any]:
    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001 - surface the underlying OOXML failure
        raise DocxContentExtractionError(f"docx_open_failed:{type(exc).__name__}") from exc

    source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    blocks: list[dict[str, Any]] = []
    ordinal = 0

    # Paragraphs in document order, skipping empty visible text like the COM path.
    for index, paragraph in enumerate(document.paragraphs):
        raw_text = _element_visible_text(paragraph._element)
        if not raw_text:
            continue
        style_name = None
        try:
            style_name = paragraph.style.name
        except Exception:  # noqa: BLE001 - style name is informational only
            style_name = None
        blocks.append({
            "ordinal": ordinal,
            "kind": "paragraph",
            "locator": {
                "story_type": 1,
                "paragraph_index": index + 1,
                "style": style_name,
                "in_table": False,
            },
            "raw_text": raw_text,
            "normalized_text": _normalize(raw_text),
            "raw_sha256": _sha256_text(raw_text),
            "normalized_sha256": _sha256_text(_normalize(raw_text)),
        })
        ordinal += 1

    # Table cells after all paragraphs, mirroring the COM enumeration order.
    for table_index, table in enumerate(document.tables):
        cell_ordinal = 0
        for row in table.rows:
            for cell in row.cells:
                raw_text = _element_visible_text(cell._tc)
                if not raw_text:
                    cell_ordinal += 1
                    continue
                blocks.append({
                    "ordinal": ordinal,
                    "kind": "table_cell",
                    "locator": {
                        "story_type": 1,
                        "table_index": table_index + 1,
                        "cell_index": cell_ordinal + 1,
                    },
                    "raw_text": raw_text,
                    "normalized_text": _normalize(raw_text),
                    "raw_sha256": _sha256_text(raw_text),
                    "normalized_sha256": _sha256_text(_normalize(raw_text)),
                })
                ordinal += 1
                cell_ordinal += 1

    # Assets: OMML formulas, drawings (inline or anchored), legacy picts. Body-level
    # paragraphs are enumerated first (matching the block locator paragraph_index),
    # then table cells. Each drawing/pict asset records its media target so the
    # import can persist package_reference and asset_sha256.
    assets: list[dict[str, Any]] = []
    asset_ordinal = 0

    def emit_drawing(element, paragraph_index: int | None) -> None:
        nonlocal asset_ordinal
        if element.find(".//" + _WP_INLINE) is not None:
            kind = "inline_shape"
        elif element.find(".//" + _WP_ANCHOR) is not None:
            kind = "shape"
        else:
            kind = "unknown"
        locator: dict[str, Any] = {"drawing_index": asset_ordinal, "kind": kind}
        if paragraph_index is not None:
            locator["paragraph_index"] = paragraph_index
        asset: dict[str, Any] = {"ordinal": asset_ordinal, "kind": kind, "locator": locator}
        media = _resolve_media(document, element)
        if media is not None:
            asset["package_reference"] = media["package_reference"]
            asset["asset_sha256"] = media["asset_sha256"]
        assets.append(asset)
        asset_ordinal += 1

    def emit_assets(element, paragraph_index: int | None) -> None:
        nonlocal asset_ordinal
        for el in element.iter():
            tag = el.tag
            if tag == _M_NS + "oMath":
                assets.append({
                    "ordinal": asset_ordinal,
                    "kind": "formula",
                    "locator": {"equation_index": asset_ordinal},
                })
                asset_ordinal += 1
            elif tag == _W_NS + "drawing":
                emit_drawing(el, paragraph_index)
            elif tag == _W_NS + "pict":
                emit_drawing(el, paragraph_index)

    for index, paragraph in enumerate(document.paragraphs):
        emit_assets(paragraph._element, index + 1)
    for table in document.tables:
        for cell in table._cells:
            emit_assets(cell._tc, None)

    normalized_content = _normalize("\n".join(block["raw_text"] for block in blocks))
    return {
        "schema": SCHEMA,
        "file_type": "docx",
        "source_sha256": source_sha256,
        "normalized_content_sha256": _sha256_text(normalized_content),
        "engine_id": ENGINE_ID,
        "engine_version": getattr(docx, "__version__", "unknown"),
        "blocks": blocks,
        "assets": assets,
    }


def extract_document(path: str | Path) -> dict[str, Any]:
    """Extract ordered .docx content blocks without modifying the original file."""
    source = Path(path).resolve(strict=True)
    if source.suffix.lower() != ".docx":
        raise DocxContentExtractionError("docx_only_pure_python_extraction")
    return _extract_docx(source)
