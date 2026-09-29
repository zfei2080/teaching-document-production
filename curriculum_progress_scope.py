"""Read-only, fail-closed curriculum progress boundary resolution.

This module resolves an explicitly named textbook and a chapter-style progress
label (for example ``第二章``) against the trusted ``textbooks`` and
``curriculum_nodes`` catalog.  It does not read questions, mappings, class
progress controls, or make any write to the database.

The boundary is intentionally catalog-wide: all nodes in catalog order through
the selected chapter subtree are allowed; every later node is outside scope.
Malformed trees, duplicate sibling sequences, version mismatches, and
ambiguous labels produce a blocked result rather than a guessed boundary.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


_TEXTBOOK_TABLE = "textbooks"
_NODE_TABLE = "curriculum_nodes"
_RELEASE_TABLE = "catalog_releases"
_REQUIRED_TEXTBOOK_COLUMNS = ("id", "name", "status", "catalog_version")
_REQUIRED_NODE_COLUMNS = (
    "id",
    "textbook_id",
    "parent_id",
    "node_type",
    "name",
    "sequence",
    "catalog_version",
    "status",
)
_REQUIRED_RELEASE_COLUMNS = ("textbook_id", "catalog_version", "status")
_PROGRESS_LABEL = re.compile(r"^第([0-9零一二三四五六七八九十百千万两]+)(章|单元)$")
_NODE_CHAPTER_NAME = re.compile(r"^第([0-9零一二三四五六七八九十百千万两]+)章")
_CHINESE_DIGITS: Mapping[str, int] = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "两": 2,
}
_UNITS: Mapping[str, int] = {"十": 10, "百": 100, "千": 1000, "万": 10000}


@dataclass(frozen=True)
class TrustedCurriculumNode:
    """The catalog fields required to calculate a deterministic boundary."""

    node_id: str
    textbook_id: str
    parent_id: str | None
    node_type: str
    name: str
    sequence: int
    catalog_version: str
    status: str


@dataclass(frozen=True)
class ProgressSelector:
    """A strict, parsed progress label; no fuzzy textbook or node matching."""

    node_type: str
    ordinal: int
    canonical_label: str


@dataclass(frozen=True)
class TextbookProgressRequest:
    """Strictly parsed user-facing form: ``教材名，进度第二章``."""

    textbook_reference: str
    selector: ProgressSelector


@dataclass(frozen=True)
class CurriculumProgressScope:
    """The resolved boundary, or a blocked result with stable reasons."""

    status: str  # resolved | blocked
    textbook_id: str | None
    catalog_version: str | None
    current_node_id: str | None
    allowed_node_ids: tuple[str, ...]
    outside_node_ids: tuple[str, ...]
    reasons: tuple[str, ...]


class CurriculumProgressScopeStorageError(RuntimeError):
    """Raised only when the read-only catalog storage contract is unavailable."""


def _blocked(*reasons: str, textbook_id: str | None = None, catalog_version: str | None = None) -> CurriculumProgressScope:
    return CurriculumProgressScope(
        status="blocked",
        textbook_id=textbook_id,
        catalog_version=catalog_version,
        current_node_id=None,
        allowed_node_ids=(),
        outside_node_ids=(),
        reasons=tuple(sorted(set(reasons))),
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    table_row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if table_row is None:
        raise CurriculumProgressScopeStorageError(f"missing required table: {table}")
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _require_catalog_contract(conn: sqlite3.Connection) -> None:
    for table, required in (
        (_TEXTBOOK_TABLE, _REQUIRED_TEXTBOOK_COLUMNS),
        (_NODE_TABLE, _REQUIRED_NODE_COLUMNS),
        (_RELEASE_TABLE, _REQUIRED_RELEASE_COLUMNS),
    ):
        missing = sorted(set(required) - _table_columns(conn, table))
        if missing:
            raise CurriculumProgressScopeStorageError(
                f"table {table} missing required columns: {', '.join(missing)}"
            )


def _parse_chinese_number(value: str) -> int | None:
    if value.isdecimal():
        return int(value)
    total = 0
    section = 0
    number: int | None = None
    for char in value:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
            continue
        unit = _UNITS.get(char)
        if unit is None:
            return None
        if unit == 10000:
            section = (section + (number or 0)) * unit
            total += section
            section = 0
        else:
            section += (number if number is not None else 1) * unit
        number = None
    result = total + section + (number or 0)
    return result if result > 0 else None


def parse_progress_selector(progress_label: str) -> ProgressSelector | None:
    """Parse only canonical chapter/unit labels such as ``第二章`` or ``第2章``."""

    compact = "".join((progress_label or "").split())
    match = _PROGRESS_LABEL.fullmatch(compact)
    if match is None:
        return None
    ordinal = _parse_chinese_number(match.group(1))
    if ordinal is None:
        return None
    node_type = "chapter" if match.group(2) == "章" else "unit"
    return ProgressSelector(node_type, ordinal, f"第{ordinal}{match.group(2)}")


def parse_textbook_progress_request(value: str) -> TextbookProgressRequest | None:
    """Parse a strict textbook/progress request without heuristic extraction.

    The only accepted format is ``<exact textbook reference>，进度<第N章>``
    (an ASCII comma is accepted as the separator too).  This keeps prose such
    as a request containing several candidate textbooks or progress ranges
    isolated for an upstream caller to clarify.
    """

    raw = (value or "").strip()
    separators = raw.count("，") + raw.count(",")
    if separators != 1:
        return None
    textbook_reference, progress_part = re.split(r"[，,]", raw, maxsplit=1)
    textbook_reference = textbook_reference.strip()
    progress_part = "".join(progress_part.split())
    if not textbook_reference or not progress_part.startswith("进度"):
        return None
    selector = parse_progress_selector(progress_part.removeprefix("进度"))
    return TextbookProgressRequest(textbook_reference, selector) if selector is not None else None


def _chapter_ordinal(node: TrustedCurriculumNode) -> int | None:
    if node.node_type != "chapter":
        return None
    match = _NODE_CHAPTER_NAME.match(" ".join(node.name.split()))
    return _parse_chinese_number(match.group(1)) if match is not None else None


def _validate_and_order(nodes: Sequence[TrustedCurriculumNode]) -> tuple[tuple[TrustedCurriculumNode, ...], tuple[str, ...]]:
    """Validate the catalog forest and return a preorder traversal if sound."""

    if not nodes:
        return (), ("no_active_catalog_nodes",)
    by_id: dict[str, TrustedCurriculumNode] = {}
    reasons: list[str] = []
    for node in nodes:
        if not node.node_id or not node.name or not node.node_type or not node.catalog_version:
            reasons.append("incomplete_catalog_node")
        if not isinstance(node.sequence, int) or isinstance(node.sequence, bool) or node.sequence < 1:
            reasons.append("unordered_catalog_sequence")
        if node.node_id in by_id:
            reasons.append("duplicate_catalog_node_id")
        by_id[node.node_id] = node
    if reasons:
        return (), tuple(sorted(set(reasons)))

    children: dict[str | None, list[TrustedCurriculumNode]] = {}
    for node in nodes:
        if node.parent_id == node.node_id:
            reasons.append("self_referencing_catalog_node")
            continue
        if node.parent_id is not None and node.parent_id not in by_id:
            reasons.append("missing_catalog_parent")
            continue
        children.setdefault(node.parent_id, []).append(node)

    for siblings in children.values():
        sequences = [node.sequence for node in siblings]
        if len(sequences) != len(set(sequences)):
            reasons.append("ambiguous_sibling_sequence")
    if reasons:
        return (), tuple(sorted(set(reasons)))

    ordered: list[TrustedCurriculumNode] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: TrustedCurriculumNode) -> None:
        if node.node_id in visiting:
            reasons.append("cyclic_catalog_parentage")
            return
        if node.node_id in visited:
            return
        visiting.add(node.node_id)
        ordered.append(node)
        for child in sorted(children.get(node.node_id, ()), key=lambda item: (item.sequence, item.node_id)):
            visit(child)
        visiting.remove(node.node_id)
        visited.add(node.node_id)

    for root in sorted(children.get(None, ()), key=lambda item: (item.sequence, item.node_id)):
        visit(root)
    if len(visited) != len(nodes):
        reasons.append("cyclic_catalog_parentage")
    if reasons:
        return (), tuple(sorted(set(reasons)))
    return tuple(ordered), ()


def resolve_progress_scope_from_catalog(
    *,
    textbook_id: str,
    catalog_version: str,
    progress_label: str,
    nodes: Iterable[TrustedCurriculumNode],
) -> CurriculumProgressScope:
    """Resolve the allowed and future nodes from one already trusted catalog.

    Callers that load data themselves must supply a single active textbook's
    active catalog-version nodes.  Any cross-textbook/version node is rejected
    instead of being ignored, because silently dropping catalog data could move
    a teaching boundary earlier or later.
    """

    selector = parse_progress_selector(progress_label)
    if selector is None:
        return _blocked("invalid_progress_label", textbook_id=textbook_id, catalog_version=catalog_version)
    node_list = tuple(nodes)
    if any(node.textbook_id != textbook_id for node in node_list):
        return _blocked("cross_textbook_catalog_node", textbook_id=textbook_id, catalog_version=catalog_version)
    if any(node.catalog_version != catalog_version for node in node_list):
        return _blocked("catalog_version_mismatch", textbook_id=textbook_id, catalog_version=catalog_version)
    if any(node.status != "active" for node in node_list):
        return _blocked("inactive_catalog_node", textbook_id=textbook_id, catalog_version=catalog_version)
    ordered, reasons = _validate_and_order(node_list)
    if reasons:
        return _blocked(*reasons, textbook_id=textbook_id, catalog_version=catalog_version)

    if selector.node_type == "chapter":
        matches = tuple(node for node in ordered if _chapter_ordinal(node) == selector.ordinal)
    else:
        # Unit labels are only unambiguous when the trusted catalog stores the
        # label verbatim; no inferred relationship between units and chapters.
        matches = tuple(node for node in ordered if node.node_type == "unit" and " ".join(node.name.split()) == selector.canonical_label)
    if not matches:
        return _blocked("progress_node_not_found", textbook_id=textbook_id, catalog_version=catalog_version)
    if len(matches) != 1:
        return _blocked("ambiguous_progress_node", textbook_id=textbook_id, catalog_version=catalog_version)
    current = matches[0]
    current_index = ordered.index(current)

    # Include the entire selected subtree.  Preorder guarantees descendants
    # occur contiguously until the next node outside the current ancestor path.
    descendant_ids: set[str] = set()
    for node in ordered:
        parent_id = node.parent_id
        while parent_id is not None:
            if parent_id == current.node_id:
                descendant_ids.add(node.node_id)
                break
            parent = next((item for item in ordered if item.node_id == parent_id), None)
            parent_id = parent.parent_id if parent is not None else None
    end_index = current_index
    for index, node in enumerate(ordered[current_index + 1 :], start=current_index + 1):
        if node.node_id in descendant_ids:
            end_index = index
            continue
        break
    allowed = tuple(node.node_id for node in ordered[: end_index + 1])
    outside = tuple(node.node_id for node in ordered[end_index + 1 :])
    return CurriculumProgressScope(
        status="resolved",
        textbook_id=textbook_id,
        catalog_version=catalog_version,
        current_node_id=current.node_id,
        allowed_node_ids=allowed,
        outside_node_ids=outside,
        reasons=(),
    )


def resolve_trusted_progress_scope(
    conn: sqlite3.Connection,
    *,
    textbook_reference: str,
    progress_label: str,
) -> CurriculumProgressScope:
    """Read a trusted catalog and resolve its progress boundary without writes.

    ``textbook_reference`` must exactly equal an active textbook id or name.
    Exact matching is deliberate: publisher/grade fuzzy matching is not trusted
    enough to authorize a curriculum range.
    """

    _require_catalog_contract(conn)
    reference = (textbook_reference or "").strip()
    if not reference:
        return _blocked("missing_textbook_reference")
    textbook_rows = conn.execute(
        """SELECT id, catalog_version FROM textbooks
        WHERE status='active' AND (id=? OR name=?) ORDER BY id""",
        (reference, reference),
    ).fetchall()
    if not textbook_rows:
        return _blocked("textbook_not_found")
    if len(textbook_rows) != 1:
        return _blocked("ambiguous_textbook_reference")
    textbook_id, catalog_version = (str(value) for value in textbook_rows[0])
    if not catalog_version.strip():
        return _blocked("missing_textbook_catalog_version", textbook_id=textbook_id)
    rows = conn.execute(
        """SELECT id, textbook_id, parent_id, node_type, name, sequence,
                  catalog_version, status
        FROM curriculum_nodes WHERE textbook_id=? ORDER BY id""",
        (textbook_id,),
    ).fetchall()
    approved_releases = conn.execute(
        """SELECT id FROM catalog_releases
        WHERE textbook_id=? AND catalog_version=? AND status='approved' ORDER BY id""",
        (textbook_id, catalog_version),
    ).fetchall()
    if not approved_releases:
        return _blocked(
            "no_approved_catalog_release",
            textbook_id=textbook_id,
            catalog_version=catalog_version,
        )
    if len(approved_releases) != 1:
        return _blocked(
            "ambiguous_approved_catalog_release",
            textbook_id=textbook_id,
            catalog_version=catalog_version,
        )
    nodes = tuple(
        TrustedCurriculumNode(
            node_id=str(row[0]),
            textbook_id=str(row[1]),
            parent_id=str(row[2]) if row[2] is not None else None,
            node_type=str(row[3]),
            name=str(row[4]),
            sequence=int(row[5]),
            catalog_version=str(row[6]),
            status=str(row[7]),
        )
        for row in rows
    )
    return resolve_progress_scope_from_catalog(
        textbook_id=textbook_id,
        catalog_version=catalog_version,
        progress_label=progress_label,
        nodes=nodes,
    )


def resolve_trusted_progress_request(
    conn: sqlite3.Connection,
    *,
    request: str,
) -> CurriculumProgressScope:
    """Resolve a strict ``教材，进度第N章`` request against the trusted catalog."""

    parsed = parse_textbook_progress_request(request)
    if parsed is None:
        return _blocked("invalid_textbook_progress_request")
    return resolve_trusted_progress_scope(
        conn,
        textbook_reference=parsed.textbook_reference,
        progress_label=parsed.selector.canonical_label,
    )
