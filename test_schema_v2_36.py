"""Schema checks for question-to-source-asset evidence in v2.36."""
from __future__ import annotations

import sqlite3
import uuid

from apply_schema_v2_35 import apply as apply_v35
from apply_schema_v2_36 import MIGRATION, apply as apply_v36
import test_schema_v2_34 as _v234


class SchemaV236Tests(_v234.SchemaV234Tests):
    # v2.34 is independently covered by test_schema_v2_34; inherit only its fixture helpers.
    test_dependency_requires_exactly_one_target = None
    test_migration_is_idempotent_and_records_its_schema_event = None
    test_new_source_derived_records_require_change_ledger_links_and_stay_append_only = None

    def setUp(self) -> None:
        super().setUp()
        apply_v35(self.database)
        apply_v36(self.database)

    def add_asset(self, conn: sqlite3.Connection, ids: dict[str, str]) -> str:
        asset_id = f"asset:{uuid.uuid4().hex}"
        event_id = f"asset-event:{uuid.uuid4().hex}"
        self.add_event(conn, event_id, entity="source_content_assets")
        conn.execute(
            """INSERT INTO source_content_assets(
                id,extraction_run_id,ordinal,asset_kind,locator_json,package_reference,
                asset_sha256,extraction_status,created_change_event_id
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                asset_id,
                ids["extract"],
                0,
                "ole_object",
                '{"range_start":12,"range_end":13}',
                None,
                _v234.digest("fixture asset"),
                "referenced",
                event_id,
            ),
        )
        return asset_id

    def test_migration_is_idempotent_and_records_schema_ledger_event(self) -> None:
        conn = self.connection()
        try:
            self.assertTrue(conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone())
            self.assertTrue(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_source_asset_evidence'"
            ).fetchone())
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM content_change_ledger WHERE id=?", (f"schema:{MIGRATION}",)).fetchone()[0],
                1,
            )
        finally:
            conn.close()
        apply_v36(self.database)

    def test_source_asset_evidence_requires_real_lineage_and_is_append_only(self) -> None:
        conn = self.connection()
        try:
            ids = self.build_minimal_lineage(conn)
            asset_id = self.add_asset(conn, ids)
            link_id = f"question-asset-evidence:{uuid.uuid4().hex}"
            event_id = f"question-asset-event:{uuid.uuid4().hex}"
            self.add_event(conn, event_id, entity="question_source_asset_evidence")
            conn.execute(
                """INSERT INTO question_source_asset_evidence(
                    id,question_id,source_content_asset_id,source_block_id,option_label,position,
                    evidence_sha256,created_change_event_id
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (link_id, ids["question"], asset_id, ids["block"], "A", 0, _v234.digest("asset option A"), event_id),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT option_label,position FROM question_source_asset_evidence WHERE id=?", (link_id,)
                ).fetchone(),
                ("A", 0),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO question_source_asset_evidence(
                        id,question_id,source_content_asset_id,source_block_id,option_label,position,
                        evidence_sha256,created_change_event_id
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), "missing-question", asset_id, ids["block"], "B", 1, _v234.digest("bad question"), event_id),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO question_source_asset_evidence(
                        id,question_id,source_content_asset_id,source_block_id,option_label,position,
                        evidence_sha256,created_change_event_id
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), ids["question"], "missing-asset", ids["block"], "B", 1, _v234.digest("bad asset"), event_id),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO question_source_asset_evidence(
                        id,question_id,source_content_asset_id,source_block_id,option_label,position,
                        evidence_sha256,created_change_event_id
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), ids["question"], asset_id, "missing-block", "B", 1, _v234.digest("bad block"), event_id),
                )
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                conn.execute("UPDATE question_source_asset_evidence SET option_label='B' WHERE id=?", (link_id,))
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                conn.execute("DELETE FROM question_source_asset_evidence WHERE id=?", (link_id,))
            conn.commit()
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()


if __name__ == "__main__":
    import unittest
    unittest.main()
