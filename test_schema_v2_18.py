from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apply_schema_v2_16 import apply_schema as apply_schema_v2_16
from apply_schema_v2_17 import apply_schema as apply_schema_v2_17
from apply_schema_v2_18 import apply_schema as apply_schema_v2_18
from automatic_gate import REQUIRED_VERIFICATION_TYPES
from input_snapshot import refresh_current_snapshot

ROOT = Path(__file__).parent
BASE_SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15")
)
MIGRATION = 'v2.18-textbook-body-snapshot-chain-2026-07-26'


class SchemaV218MigrationTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.path = Path(handle.name)
        conn = sqlite3.connect(self.path)
        conn.execute('PRAGMA foreign_keys=ON')
        for schema in BASE_SCHEMAS:
            conn.executescript((ROOT / schema).read_text(encoding='utf-8'))
        conn.close()
        apply_schema_v2_16(self.path)
        apply_schema_v2_17(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute('PRAGMA foreign_keys=ON')
        self._seed_base()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _seed_base(self):
        self.conn.execute("INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('s1', 'source.docx', 'hash-s1', 'docx')")
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('tb1', '教材', 'v1')")
        self.conn.execute(
            """INSERT INTO curriculum_nodes (id, textbook_id, stage, grade_level, node_type, name, sequence, catalog_version, status)
            VALUES ('n1', 'tb1', '初中', '七上', 'topic', '当前节点', 1, 'v1', 'active')"""
        )
        self.conn.execute(
            """INSERT INTO questions (id, stem, question_type, stage, source_document_id, content_hash, quality_status, review_status)
            VALUES ('q1', '题干', '选择题', '初中', 's1', 'content-hash', 'needs_review', 'pending')"""
        )
        self.conn.execute(
            "INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id, fit_status) VALUES ('q1', 'tb1', 'n1', 'approved')"
        )
        self.conn.execute(
            """INSERT INTO knowledge_points
            (id, canonical_name, knowledge_type, stage_scope, version, review_status)
            VALUES ('kp1', '整式加减', 'concept', '初中', 'v1', 'approved')"""
        )
        self.conn.execute(
            "INSERT INTO question_knowledge_points (question_id, knowledge_point_id, relation_type) VALUES ('q1', 'kp1', 'primary')"
        )
        snapshot_hash = refresh_current_snapshot(self.conn, 'q1')
        for index, verification_type in enumerate(sorted(REQUIRED_VERIFICATION_TYPES)):
            self.conn.execute(
                """INSERT INTO question_verifications
                (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash, verified_at)
                VALUES (?, 'q1', ?, 'test', 'v1', 'pass', '{\"ok\": true}', ?, ?)""",
                (
                    f"q1-{verification_type}",
                    verification_type,
                    snapshot_hash,
                    f"2026-01-01T00:00:{index:02d}Z",
                ),
            )
        self.conn.commit()

    def _insert_source_and_body(self):
        self.conn.execute(
            """INSERT INTO textbook_sources
            (id, textbook_id, source_document_id, source_file_hash, status)
            VALUES ('ts1', 'tb1', 's1', 'src-hash-1', 'active')"""
        )
        self.conn.execute(
            """INSERT INTO textbook_body_units
            (id, textbook_source_id, ordinal, locator, text, raw_hash, normalized_hash, structural_hash)
            VALUES ('bu1', 'ts1', 1, '七上/第一章/1', '这是教材正文', 'raw-1', 'norm-1', 'struct-1')"""
        )
        self.conn.commit()

    def test_apply_schema_on_v217_is_successful_and_idempotent(self):
        apply_schema_v2_18(self.path)
        apply_schema_v2_18(self.path)
        count = self.conn.execute(
            'SELECT COUNT(*) FROM schema_migrations WHERE version=?',
            (MIGRATION,),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_tables_and_constraints_exist(self):
        apply_schema_v2_18(self.path)
        table_names = {
            row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('textbook_sources', 'textbook_body_units', 'question_body_unit_mappings', 'knowledge_body_unit_mappings', 'textbook_body_unit_snapshots')"
            )
        }
        self.assertEqual(
            table_names,
            {'textbook_sources', 'textbook_body_units', 'question_body_unit_mappings', 'knowledge_body_unit_mappings', 'textbook_body_unit_snapshots'},
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO textbook_sources (id, textbook_id, source_document_id, source_file_hash, status)
                VALUES ('ts-bad', 'tb1', 's1', '', 'active')"""
            )
        self._insert_source_and_body()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO textbook_body_units
                (id, textbook_source_id, ordinal, locator, text, raw_hash, normalized_hash, structural_hash)
                VALUES ('bu-bad', 'ts1', 2, '七上/第一章/2', '', 'raw-2', 'norm-2', 'struct-2')"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO textbook_body_units
                (id, textbook_source_id, ordinal, locator, text, raw_hash, normalized_hash, structural_hash)
                VALUES ('bu-bad2', 'ts1', 2, '七上/第一章/2', '正文', '', 'norm-2', 'struct-2')"""
            )

    def test_body_change_invalidates_mappings_and_snapshot(self):
        apply_schema_v2_18(self.path)
        self._insert_source_and_body()
        self.conn.execute(
            """INSERT INTO question_body_unit_mappings
            (question_id, body_unit_id, source_file_hash)
            VALUES ('q1', 'bu1', 'src-hash-1')"""
        )
        self.conn.execute(
            """INSERT INTO knowledge_body_unit_mappings
            (knowledge_point_id, body_unit_id, source_file_hash)
            VALUES ('kp1', 'bu1', 'src-hash-1')"""
        )
        self.conn.commit()
        self.conn.execute("UPDATE textbook_body_units SET text='这是变更后的教材正文' WHERE id='bu1'")
        q_row = self.conn.execute(
            "SELECT status, invalidation_reason, invalidated_at FROM question_body_unit_mappings WHERE question_id='q1' AND body_unit_id='bu1'"
        ).fetchone()
        k_row = self.conn.execute(
            "SELECT status, invalidation_reason, invalidated_at FROM knowledge_body_unit_mappings WHERE knowledge_point_id='kp1' AND body_unit_id='bu1'"
        ).fetchone()
        snapshot_row = self.conn.execute(
            "SELECT source_file_hash, raw_hash, normalized_hash, structural_hash, invalidation_reason, invalidated_at, revision FROM textbook_body_unit_snapshots WHERE body_unit_id='bu1'"
        ).fetchone()
        self.assertEqual(q_row[0], 'invalidated')
        self.assertEqual(q_row[1], 'textbook_body_unit_changed')
        self.assertIsNotNone(q_row[2])
        self.assertEqual(k_row[0], 'invalidated')
        self.assertEqual(k_row[1], 'textbook_body_unit_changed')
        self.assertIsNotNone(k_row[2])
        self.assertEqual(snapshot_row[:4], (None, None, None, None))
        self.assertEqual(snapshot_row[4], 'textbook_body_unit_changed')
        self.assertIsNotNone(snapshot_row[5])
        self.assertEqual(snapshot_row[6], 1)

    def test_without_active_textbook_source_mapping_is_blocked(self):
        apply_schema_v2_18(self.path)
        self.conn.execute(
            """INSERT INTO textbook_sources
            (id, textbook_id, source_document_id, source_file_hash, status, invalidated_at, invalidation_reason)
            VALUES ('ts0', 'tb1', 's1', 'src-hash-0', 'invalidated', CURRENT_TIMESTAMP, 'not_authorized')"""
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO textbook_body_units
                (id, textbook_source_id, ordinal, locator, text, raw_hash, normalized_hash, structural_hash)
                VALUES ('bu0', 'ts0', 1, '七上/第一章/0', '正文', 'raw-0', 'norm-0', 'struct-0')"""
            )
        self._insert_source_and_body()
        self.conn.execute("UPDATE textbook_sources SET status='replaced' WHERE id='ts1'")
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO question_body_unit_mappings
                (question_id, body_unit_id, source_file_hash)
                VALUES ('q1', 'bu1', 'src-hash-1')"""
            )

    def test_approval_is_blocked_without_active_body_evidence(self):
        apply_schema_v2_18(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE questions SET quality_status='approved', review_status='approved' WHERE id='q1'")
        self._insert_source_and_body()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE questions SET quality_status='approved', review_status='approved' WHERE id='q1'")
        self.conn.execute(
            """INSERT INTO question_body_unit_mappings
            (question_id, body_unit_id, source_file_hash)
            VALUES ('q1', 'bu1', 'src-hash-1')"""
        )
        self.conn.commit()
        self.conn.execute("UPDATE questions SET quality_status='approved', review_status='approved' WHERE id='q1'")
        row = self.conn.execute("SELECT quality_status, review_status FROM questions WHERE id='q1'").fetchone()
        self.assertEqual(row, ('approved', 'approved'))


if __name__ == '__main__':
    unittest.main()
