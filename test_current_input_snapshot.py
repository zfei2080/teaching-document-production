"""P0-3 tests: current-input snapshots invalidate evidence and fail closed."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from automatic_gate import REQUIRED_VERIFICATION_TYPES, apply_automatic_admission, evaluate_automatic_admission
from input_snapshot import refresh_current_snapshot

ROOT = Path(__file__).parent
SCHEMAS = tuple(f"schema_v2{suffix}.sql" for suffix in (
    "", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14"
))


class CurrentInputSnapshotTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self.conn.execute("INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('s1', 'source.docx', 'source-hash', 'docx')")
        self.conn.execute("INSERT INTO source_fragments (id, source_document_id, location_type, raw_text, raw_hash) VALUES ('f1', 's1', 'paragraph', '原始题干', 'fragment-hash')")
        self.conn.execute(
            """INSERT INTO questions (id, stem, question_type, stage, source_document_id, source_fragment_id, content_hash, quality_status, review_status)
               VALUES ('q1', '题干', '选择题', '初中', 's1', 'f1', 'content-hash', 'needs_review', 'pending')"""
        )
        self.conn.execute("INSERT INTO question_source_fragments (question_id, source_fragment_id, field_name, source_hash) VALUES ('q1', 'f1', 'stem', 'fragment-hash')")

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def add_current_passes(self):
        snapshot_hash = refresh_current_snapshot(self.conn, "q1")
        run_number = self.conn.execute("SELECT COUNT(*) FROM question_verifications").fetchone()[0]
        for index, verification_type in enumerate(sorted(REQUIRED_VERIFICATION_TYPES)):
            self.conn.execute(
                """INSERT INTO question_verifications
                (id, question_id, verification_type, validator_id, validator_version, status, evidence_json, input_hash, verified_at)
                VALUES (?, 'q1', ?, 'test', 'v1', 'pass', ?, ?, ?)""",
                (f"pass-{run_number}-{verification_type}", verification_type, json.dumps({"type": verification_type}), snapshot_hash,
                 f"2026-01-01T00:00:0{index}Z"),
            )
        return snapshot_hash

    def assert_invalidated_and_blocked(self):
        self.assertEqual(
            self.conn.execute("SELECT input_hash, invalidated_at FROM question_input_snapshots WHERE question_id='q1'").fetchone()[0],
            None,
        )
        self.assertEqual(self.conn.execute("SELECT quality_status, review_status FROM questions WHERE id='q1'").fetchone(), ("blocked", "pending"))
        self.assertFalse(evaluate_automatic_admission(self.conn, "q1").eligible)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE questions SET quality_status='approved', review_status='approved' WHERE id='q1'")

    def test_exact_current_snapshot_passes_then_stem_change_invalidates_old_evidence(self):
        self.add_current_passes()
        self.assertTrue(apply_automatic_admission(self.conn, "q1").eligible)
        self.conn.execute("UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id='q1'")
        self.conn.execute("UPDATE questions SET stem='已改题干' WHERE id='q1'")
        self.assert_invalidated_and_blocked()
        self.add_current_passes()
        self.assertTrue(apply_automatic_admission(self.conn, "q1").eligible)

    def test_answer_source_asset_textbook_and_knowledge_mapping_changes_invalidate(self):
        for kind in ("answer", "source", "asset", "textbook", "knowledge"):
            with self.subTest(kind=kind):
                self.add_current_passes()
                self.assertTrue(apply_automatic_admission(self.conn, "q1").eligible)
                self.conn.execute("UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id='q1'")
                if kind == "answer":
                    self.conn.execute("UPDATE questions SET answer='新答案' WHERE id='q1'")
                elif kind == "source":
                    self.conn.execute("UPDATE question_source_fragments SET source_hash='new-fragment-hash' WHERE question_id='q1' AND field_name='stem'")
                elif kind == "asset":
                    self.conn.execute("INSERT INTO question_assets (id, question_id, asset_type, relative_path, checksum) VALUES ('a1', 'q1', 'image', 'a.png', 'asset-hash')")
                elif kind == "textbook":
                    self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('t1', '教材', 'v1')")
                    self.conn.execute("INSERT INTO question_textbooks (question_id, textbook_id, curriculum_node_id) VALUES ('q1', 't1', NULL)")
                else:
                    self.conn.execute("INSERT INTO knowledge_points (id, canonical_name, knowledge_type, stage_scope, version) VALUES ('k1', '知识点', 'concept', '初中', 'v1')")
                    self.conn.execute("INSERT INTO question_knowledge_points (question_id, knowledge_point_id) VALUES ('q1', 'k1')")
                self.assert_invalidated_and_blocked()
                self.conn.execute("DELETE FROM question_verifications")
                self.conn.execute("DELETE FROM question_assets")
                self.conn.execute("DELETE FROM question_textbooks")
                self.conn.execute("DELETE FROM question_knowledge_points")

    def test_evidence_with_old_or_tampered_hash_cannot_promote(self):
        snapshot_hash = self.add_current_passes()
        self.conn.execute("UPDATE question_verifications SET input_hash='forged' WHERE verification_type='asset_semantics'")
        result = evaluate_automatic_admission(self.conn, "q1")
        self.assertFalse(result.eligible)
        self.assertIn("asset_semantics", result.missing_or_nonpassing)
        self.assertNotEqual(snapshot_hash, "forged")

    def test_migration_is_idempotent_and_rolls_back_invalid_script(self):
        self.conn.executescript((ROOT / "schema_v2_14.sql").read_text(encoding="utf-8"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='v2.14-current-input-snapshot-evidence-2026-07-26'").fetchone()[0], 1)
        before = self.conn.execute("SELECT COUNT(*) FROM question_input_snapshots").fetchone()[0]
        with self.assertRaises(sqlite3.Error):
            with self.conn:
                self.conn.executescript("INSERT INTO question_input_snapshots VALUES ('missing', 'x', 1, NULL, NULL, NULL); THIS IS NOT SQL;")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_input_snapshots").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
