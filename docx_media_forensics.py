"""Inventory DOCX embedded-media instances without rendering the document.

The inventory preserves relationship IDs, package parts, host/container locations,
media paths and content hashes. This is source evidence only: it does not claim
that an image semantically matches a question.
"""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from docx import Document
from docx.oxml.ns import qn

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@dataclass(frozen=True)
class ParagraphMedia:
    paragraph_index: int
    filenames: tuple[str, ...]


@dataclass(frozen=True)
class MediaInstance:
    package_part: str
    relationship_id: str
    media_path: str | None
    filename: str | None
    media_sha256: str | None
    host_kind: str
    host_location: str
    paragraph_index: int | None
    relationship_status: str = "resolved_internal"
    relationship_target: str | None = None
    drawing_layout: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _ImageRelationship:
    target: str | None
    target_mode: str | None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag else ""


def _relationship_media_map(document: Document) -> dict[str, str]:
    mapping = {}
    for relationship_id, relationship in document.part.rels.items():
        if "image" in relationship.reltype.lower():
            mapping[relationship_id] = Path(relationship.target_ref).name
    return mapping


def paragraph_media(path: str | Path) -> list[ParagraphMedia]:
    """Compatibility view for images in python-docx top-level paragraphs."""
    document = Document(str(path))
    relationship_map = _relationship_media_map(document)
    result: list[ParagraphMedia] = []
    for index, paragraph in enumerate(document.paragraphs):
        names: list[str] = []
        seen: set[str] = set()
        for element in paragraph._element.iter():
            if _local_name(element.tag) not in {"blip", "imagedata"}:
                continue
            relationship_id = element.get(qn("r:embed")) or element.get(qn("r:id"))
            filename = relationship_map.get(relationship_id or "")
            if filename and filename not in seen:
                seen.add(filename)
                names.append(filename)
        if names:
            result.append(ParagraphMedia(paragraph_index=index, filenames=tuple(names)))
    return result


def _rels_path(part: str) -> str:
    directory, filename = posixpath.split(part)
    return posixpath.join(directory, "_rels", filename + ".rels")


def _resolve_target(part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(part), target))


def _image_relationships(archive: ZipFile, part: str) -> dict[str, _ImageRelationship]:
    rels_name = _rels_path(part)
    if rels_name not in archive.namelist():
        return {}
    root = ET.fromstring(archive.read(rels_name))
    result: dict[str, _ImageRelationship] = {}
    for rel in root.findall(f"{{{REL_NS}}}Relationship"):
        if "image" not in (rel.get("Type") or "").lower():
            continue
        rel_id = rel.get("Id")
        if rel_id:
            result[rel_id] = _ImageRelationship(rel.get("Target"), rel.get("TargetMode"))
    return result


def _host_kind(part: str, ancestors: tuple[str, ...]) -> str:
    if part.startswith("word/header"):
        return "header"
    if part.startswith("word/footer"):
        return "footer"
    if part == "word/footnotes.xml":
        return "footnote"
    if part == "word/endnotes.xml":
        return "endnote"
    if part == "word/comments.xml":
        return "comment"
    if "txbxContent" in ancestors:
        return "textbox"
    if "tc" in ancestors:
        return "table_cell"
    return "body_paragraph"


def _drawing_layout(ancestors: tuple[ET.Element, ...]) -> tuple[tuple[str, str], ...]:
    """Record declared inline/anchor geometry; never infer rendered placement."""
    container = next((item for item in reversed(ancestors) if _local_name(item.tag) in {"anchor", "inline"}), None)
    if container is None:
        return ()
    kind = _local_name(container.tag)
    evidence: list[tuple[str, str]] = [("drawing_kind", kind)]
    for name, value in sorted(container.attrib.items()):
        evidence.append((f"{kind}.@{_local_name(name)}", value))
    if kind == "anchor":
        for direction, child_name in (("horizontal", "positionH"), ("vertical", "positionV")):
            position = next((child for child in container if _local_name(child.tag) == child_name), None)
            if position is None:
                continue
            relative_from = position.get("relativeFrom")
            if relative_from is not None:
                evidence.append((f"anchor.{direction}.relative_from", relative_from))
            for child in position:
                local = _local_name(child.tag)
                if local in {"align", "posOffset"} and child.text is not None:
                    evidence.append((f"anchor.{direction}.{local}", child.text.strip()))
    return tuple(evidence)


def media_instances(path: str | Path) -> list[MediaInstance]:
    """Return every image reference, including unresolved or external evidence.

    Only internal, present media parts have a content hash. Missing relationship,
    external target, missing relationship target and missing package part are
    explicit anomalous statuses rather than silently omitted evidence.
    """
    path = Path(path)
    instances: list[MediaInstance] = []
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        parts = sorted(
            name for name in names
            if name == "word/document.xml"
            or name.startswith("word/header") and name.endswith(".xml")
            or name.startswith("word/footer") and name.endswith(".xml")
            or name in {"word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"}
        )
        media_hashes: dict[str, str] = {}
        for part in parts:
            relationships = _image_relationships(archive, part)
            root = ET.fromstring(archive.read(part))
            body_paragraph_indexes: dict[int, int] = {}
            if part == "word/document.xml":
                body = root.find(f"{{{W_NS}}}body")
                if body is not None:
                    direct_paragraphs = [child for child in body if _local_name(child.tag) == "p"]
                    body_paragraph_indexes = {id(element): index for index, element in enumerate(direct_paragraphs)}

            def walk(element: ET.Element, ancestor_names: tuple[str, ...], ancestor_elements: tuple[ET.Element, ...], location: str, paragraph: ET.Element | None) -> None:
                local = _local_name(element.tag)
                current_paragraph = element if local == "p" else paragraph
                if local in {"blip", "imagedata"}:
                    rel_id = element.get(f"{{{R_NS}}}embed") or element.get(f"{{{R_NS}}}id")
                    relationship = relationships.get(rel_id or "")
                    target = relationship.target if relationship else None
                    media_path: str | None = None
                    if not rel_id or relationship is None:
                        status = "missing_relationship"
                    elif relationship.target_mode == "External":
                        status = "external_target"
                    elif not target:
                        status = "missing_relationship_target"
                    else:
                        media_path = _resolve_target(part, target)
                        status = "resolved_internal" if media_path in names else "missing_media_part"
                    digest = None
                    if status == "resolved_internal" and media_path is not None:
                        digest = media_hashes.setdefault(media_path, hashlib.sha256(archive.read(media_path)).hexdigest())
                    if rel_id:
                        paragraph_index = body_paragraph_indexes.get(id(current_paragraph)) if current_paragraph is not None else None
                        instances.append(MediaInstance(
                            package_part=part, relationship_id=rel_id, media_path=media_path,
                            filename=posixpath.basename(media_path) if media_path else None,
                            media_sha256=digest, host_kind=_host_kind(part, ancestor_names + (local,)),
                            host_location=location, paragraph_index=paragraph_index,
                            relationship_status=status, relationship_target=target,
                            drawing_layout=_drawing_layout(ancestor_elements + (element,)),
                        ))
                counts: dict[str, int] = {}
                for child in list(element):
                    child_local = _local_name(child.tag)
                    counts[child_local] = counts.get(child_local, 0) + 1
                    walk(child, ancestor_names + (local,), ancestor_elements + (element,), f"{location}/{child_local}[{counts[child_local]}]", current_paragraph)

            walk(root, (), (), f"/{_local_name(root.tag)}[1]", None)
    return instances


def embedded_media_names(path: str | Path) -> set[str]:
    """Return all files stored in word/media for source inventory checks."""
    with ZipFile(path) as archive:
        return {Path(name).name for name in archive.namelist() if name.startswith("word/media/") and not name.endswith("/")}
