"""Tests for read-only trusted curriculum range resolution."""

from __future__ import annotations

import sqlite3
import unittest

from curriculum_progress_scope import (
    TrustedCurriculumNode,
    parse_progress_selector,
    parse_textbook_progress_request,
    resolve_progress_scope_from_catalog,
    resolve_trusted_progress_request,
    resolve_trusted_progress_scope,
)


BOOK = "nbsd-math-8-lower"
VERSION = "2026.1"


def node(
    node_id: str,
    parent_id: str | None,
    node_type: str,
    name: str,
    sequence: int,
    **changes: object,
) -> TrustedCurriculumNode:
    values: dict[str, object] = {
        "node_id": node_id,
        "textbook_id": BOOK,
        "parent_id": parent_id,
        "node_type": node_type,
        "name": name,
        "sequence": sequence,
        "catalog_version": VERSION,
        "status": "active",
    }
    values.update(changes)
    return TrustedCurriculumNode(**values)  # type: ignore[arg-type]


CATALOG = (
    node("term", None, "term", "八年级下册", 1),
    node("chapter-1", "term", "chapter", "第一章 三角形", 1),
    node("chapter-1-topic", "chapter-1", "topic", "三角形内角和", 1),
    node("chapter-2", "term", "chapter", "第二章 全等三角形", 2),
    node("chapter-2-topic-a", "chapter-2", "topic", "全等三角形判定", 1),
    node("chapter-2-topic-b", "chapter-2", "topic", "全等三角形性质", 2),
    node("chapter-3", "term", "chapter", "第三章 勾股定理", 3),
)


class CurriculumProgressScopeTests(unittest.TestCase):
    def test_chapter_two_includes_preceding_nodes_and_current_subtree(self) -> None:
        result = resolve_progress_scope_from_catalog(
            textbook_id=BOOK,
            catalog_version=VERSION,
            progress_label="第二章",
            nodes=CATALOG,
        )
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.current_node_id, "chapter-2")
        self.assertEqual(
            result.allowed_node_ids,
            ("term", "chapter-1", "chapter-1-topic", "chapter-2", "chapter-2-topic-a", "chapter-2-topic-b"),
        )
        self.assertEqual(result.outside_node_ids, ("chapter-3",))

    def test_parser_accepts_canonical_chinese_and_arabic_chapter_numbers_only(self) -> None:
        self.assertEqual(parse_progress_selector(" 第 2 章 ").canonical_label, "第2章")
        self.assertEqual(parse_progress_selector("第二章").ordinal, 2)
        self.assertIsNone(parse_progress_selector("进度第二章"))
        self.assertIsNone(parse_progress_selector("第二章至第三章"))

    def test_textbook_progress_request_requires_one_explicit_separator_and_prefix(self) -> None:
        parsed = parse_textbook_progress_request("北师大版八下，进度第二章")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.textbook_reference, "北师大版八下")
        self.assertEqual(parsed.selector.canonical_label, "第2章")
        self.assertIsNone(parse_textbook_progress_request("北师大版八下 进度第二章"))
        self.assertIsNone(parse_textbook_progress_request("北师大版八下，第二章"))
        self.assertIsNone(parse_textbook_progress_request("甲，进度第二章，乙"))

    def test_duplicate_sibling_sequences_fail_closed(self) -> None:
        result = resolve_progress_scope_from_catalog(
            textbook_id=BOOK,
            catalog_version=VERSION,
            progress_label="第二章",
            nodes=CATALOG + (node("chapter-2-copy", "term", "chapter", "第二章 复制", 2),),
        )
        self.assertEqual(result.status, "blocked")
        self.assertIn("ambiguous_sibling_sequence", result.reasons)

    def test_missing_or_nonpositive_sequence_is_not_treated_as_catalog_order(self) -> None:
        result = resolve_progress_scope_from_catalog(
            textbook_id=BOOK,
            catalog_version=VERSION,
            progress_label="第二章",
            nodes=(node("chapter-2", None, "chapter", "第二章", 0),),
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reasons, ("unordered_catalog_sequence",))

    def test_ambiguous_chapter_label_fails_closed_even_with_different_parents(self) -> None:
        result = resolve_progress_scope_from_catalog(
            textbook_id=BOOK,
            catalog_version=VERSION,
            progress_label="第二章",
            nodes=CATALOG + (
                node("other-term", None, "term", "附册", 2),
                node("other-chapter-2", "other-term", "chapter", "第二章 附加内容", 1),
            ),
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reasons, ("ambiguous_progress_node",))

    def test_cycles_and_catalog_version_mismatch_fail_closed(self) -> None:
        cyclic = (
            node("a", "b", "chapter", "第一章 A", 1),
            node("b", "a", "chapter", "第二章 B", 1),
        )
        result = resolve_progress_scope_from_catalog(
            textbook_id=BOOK, catalog_version=VERSION, progress_label="第二章", nodes=cyclic
        )
        self.assertIn("cyclic_catalog_parentage", result.reasons)
        mismatch = resolve_progress_scope_from_catalog(
            textbook_id=BOOK,
            catalog_version=VERSION,
            progress_label="第二章",
            nodes=(node("bad", None, "chapter", "第二章", 1, catalog_version="other"),),
        )
        self.assertEqual(mismatch.reasons, ("catalog_version_mismatch",))

    def test_cross_textbook_and_inactive_nodes_fail_closed(self) -> None:
        cross = resolve_progress_scope_from_catalog(
            textbook_id=BOOK,
            catalog_version=VERSION,
            progress_label="第二章",
            nodes=(node("bad", None, "chapter", "第二章", 1, textbook_id="other"),),
        )
        self.assertEqual(cross.reasons, ("cross_textbook_catalog_node",))
        inactive = resolve_progress_scope_from_catalog(
            textbook_id=BOOK,
            catalog_version=VERSION,
            progress_label="第二章",
            nodes=(node("bad", None, "chapter", "第二章", 1, status="archived"),),
        )
        self.assertEqual(inactive.reasons, ("inactive_catalog_node",))


class TrustedCatalogSqliteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE textbooks (id TEXT PRIMARY KEY, name TEXT, status TEXT, catalog_version TEXT);
            CREATE TABLE curriculum_nodes (
                id TEXT PRIMARY KEY, textbook_id TEXT, parent_id TEXT, node_type TEXT,
                name TEXT, sequence INTEGER, catalog_version TEXT, status TEXT
            );
            CREATE TABLE catalog_releases (
                id TEXT PRIMARY KEY, textbook_id TEXT, catalog_version TEXT, status TEXT
            );
            """
        )
        self.conn.execute(
            "INSERT INTO textbooks VALUES (?, ?, 'active', ?)", (BOOK, "北师大版八下", VERSION)
        )
        self.conn.execute(
            "INSERT INTO catalog_releases VALUES ('release-1', ?, ?, 'approved')", (BOOK, VERSION)
        )
        self.conn.executemany(
            "INSERT INTO curriculum_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (item.node_id, item.textbook_id, item.parent_id, item.node_type, item.name, item.sequence, item.catalog_version, item.status)
                for item in CATALOG
            ],
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_exact_active_textbook_name_is_resolved_read_only(self) -> None:
        before = self.conn.total_changes
        result = resolve_trusted_progress_scope(
            self.conn, textbook_reference="北师大版八下", progress_label="第二章"
        )
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.outside_node_ids, ("chapter-3",))
        self.assertEqual(self.conn.total_changes, before)

    def test_combined_request_resolves_the_same_boundary(self) -> None:
        result = resolve_trusted_progress_request(self.conn, request="北师大版八下，进度第二章")
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.current_node_id, "chapter-2")
        malformed = resolve_trusted_progress_request(self.conn, request="北师大版八下进度第二章")
        self.assertEqual(malformed.reasons, ("invalid_textbook_progress_request",))

    def test_ambiguous_textbook_reference_and_missing_schema_are_blocked_or_rejected(self) -> None:
        self.conn.execute("INSERT INTO textbooks VALUES ('other', '北师大版八下', 'active', '2026.2')")
        result = resolve_trusted_progress_scope(
            self.conn, textbook_reference="北师大版八下", progress_label="第二章"
        )
        self.assertEqual(result.reasons, ("ambiguous_textbook_reference",))

    def test_unapproved_or_duplicate_catalog_release_fails_closed(self) -> None:
        self.conn.execute("UPDATE catalog_releases SET status='draft'")
        unapproved = resolve_trusted_progress_scope(
            self.conn, textbook_reference=BOOK, progress_label="第二章"
        )
        self.assertEqual(unapproved.reasons, ("no_approved_catalog_release",))
        self.conn.execute("UPDATE catalog_releases SET status='approved'")
        self.conn.execute(
            "INSERT INTO catalog_releases VALUES ('release-2', ?, ?, 'approved')", (BOOK, VERSION)
        )
        duplicate = resolve_trusted_progress_scope(
            self.conn, textbook_reference=BOOK, progress_label="第二章"
        )
        self.assertEqual(duplicate.reasons, ("ambiguous_approved_catalog_release",))


if __name__ == "__main__":
    unittest.main()
