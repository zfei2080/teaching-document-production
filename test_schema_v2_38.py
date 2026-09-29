"""Schema checks for the fail-closed content mathematical-validation contract."""
from __future__ import annotations

import sqlite3
import uuid

from apply_schema_v2_35 import apply as apply_v35
from apply_schema_v2_36 import apply as apply_v36
from apply_schema_v2_37 import apply as apply_v37
from apply_schema_v2_38 import MIGRATION, apply as apply_v38
from apply_schema_v2_39 import apply as apply_v39
import test_schema_v2_34 as base


class SchemaV238Tests(base.SchemaV234Tests):
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

    def test_evidence_is_append_only_and_view_is_fail_closed(self) -> None:
        connection = self.connection()
        try:
            ids = self.build_minimal_lineage(connection)
            self.assertEqual(
                connection.execute(
                    "SELECT content_eligible FROM content_item_question_eligibility WHERE content_item_id=?", (ids["item"],)
                ).fetchone()[0],
                0,
            )
            point_id = f"kp:{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO knowledge_points(
                    id,canonical_name,knowledge_type,stage_scope,definition_text,formulas_json,properties_json,
                    conditions_json,common_errors_json,version,review_status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (point_id, "fixture knowledge", "property", "legacy", None, "[]", "[]", "[]", "[]", "v1", "approved"),
            )
            math_event = f"math-event:{uuid.uuid4().hex}"
            self.add_event(connection, math_event, entity="content_item_math_validation_evidence")
            item_hash = connection.execute("SELECT content_sha256 FROM content_items WHERE id=?", (ids["item"],)).fetchone()[0]
            evidence_id = f"math:{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO content_item_math_validation_evidence(
                    id,content_item_id,question_id,validation_contract_id,validator_id,validator_version,
                    validation_status,computed_answer,source_answer,content_sha256,input_sha256,evidence_json,
                    evidence_sha256,created_change_event_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (evidence_id, ids["item"], ids["question"], "fixture", "fixture-validator", "1", "pass", "2", "2",
                 item_hash, base.digest("input"), "{}", base.digest("evidence"), math_event),
            )
            mapping_event = f"mapping-event:{uuid.uuid4().hex}"
            self.add_event(connection, mapping_event, entity="content_item_knowledge_mappings")
            connection.execute(
                """INSERT INTO content_item_knowledge_mappings(
                    id,content_item_id,knowledge_point_id,relation_type,mapping_status,mapping_method,
                    evidence_json,evidence_sha256,created_change_event_id
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (f"mapping:{uuid.uuid4().hex}", ids["item"], point_id, "primary", "validated", "fixture", "{}", base.digest("mapping"), mapping_event),
            )
            difficulty_event = f"difficulty-event:{uuid.uuid4().hex}"
            self.add_event(connection, difficulty_event, entity="content_item_difficulty_evidence")
            connection.execute(
                """INSERT INTO content_item_difficulty_evidence(
                    id,content_item_id,difficulty,evidence_status,method,evidence_json,evidence_sha256,
                    created_change_event_id
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (f"difficulty:{uuid.uuid4().hex}", ids["item"], "基础", "validated", "fixture", "{}", base.digest("difficulty"), difficulty_event),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT content_eligible FROM content_item_question_eligibility WHERE content_item_id=?", (ids["item"],)
                ).fetchone()[0],
                1,
            )
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                connection.execute(
                    "UPDATE content_item_math_validation_evidence SET validation_status='fail' WHERE id=?", (evidence_id,)
                )
        finally:
            connection.close()

    def test_migration_is_idempotent_and_records_schema_event(self) -> None:
        connection = self.connection()
        try:
            self.assertTrue(connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone())
            self.assertTrue(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='view' AND name='content_item_question_eligibility'"
            ).fetchone())
        finally:
            connection.close()
        apply_v38(self.database)


if __name__ == "__main__":
    import unittest
    unittest.main()
