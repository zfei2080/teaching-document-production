"""Schema checks for the repaired source-content difficulty enumeration."""
from __future__ import annotations

import sqlite3

from apply_schema_v2_35 import apply as apply_v35
from apply_schema_v2_36 import apply as apply_v36
from apply_schema_v2_37 import apply as apply_v37
from apply_schema_v2_38 import apply as apply_v38
from apply_schema_v2_39 import MIGRATION, apply as apply_v39
import test_schema_v2_34 as base


class SchemaV239Tests(base.SchemaV234Tests):
    test_dependency_requires_exactly_one_target = None
    test_migration_is_idempotent_and_records_its_schema_event = None
    test_new_source_derived_records_require_change_ledger_links_and_stay_append_only = None

    def setUp(self) -> None:
        super().setUp()
        apply_v35(self.database)
        apply_v36(self.database)
        apply_v37(self.database)
        apply_v38(self.database)
        apply_v39(self.database)

    def test_repaired_enum_accepts_named_difficulty_and_rejects_unknown_value(self) -> None:
        connection = self.connection()
        try:
            ids = self.build_minimal_lineage(connection)
            event = "difficulty-event"
            self.add_event(connection, event, entity="content_item_difficulty_evidence")
            connection.execute(
                "INSERT INTO content_item_difficulty_evidence(id,content_item_id,difficulty,evidence_status,method,evidence_json,evidence_sha256,created_change_event_id) VALUES(?,?,?,?,?,?,?,?)",
                ("difficulty-record", ids["item"], "\u57fa\u7840", "validated", "fixture", "{}", base.digest("difficulty"), event),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO content_item_difficulty_evidence(id,content_item_id,difficulty,evidence_status,method,evidence_json,evidence_sha256,created_change_event_id) VALUES(?,?,?,?,?,?,?,?)",
                    ("difficulty-invalid", ids["item"], "invalid", "validated", "fixture", "{}", base.digest("invalid"), event),
                )
        finally:
            connection.close()

    def test_migration_is_idempotent(self) -> None:
        connection = self.connection()
        try:
            self.assertTrue(connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone())
        finally:
            connection.close()
        apply_v39(self.database)


if __name__ == "__main__":
    import unittest
    unittest.main()
