"""Regression tests for v2.34 database-enrichment storage and audit invariants."""
from __future__ import annotations

from hashlib import sha256
import json
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from apply_schema_v2_30 import apply as apply_v30
from apply_schema_v2_34 import MIGRATION, apply as apply_v34

ROOT = Path(__file__).parent
BASELINE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest().upper()


class SchemaV234Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "development-copy.db"
        shutil.copy2(BASELINE, self.database)
        apply_v30(self.database)
        apply_v34(self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def add_event(self, conn: sqlite3.Connection, event_id: str, *, entity: str, operation: str = "create") -> None:
        conn.execute(
            """INSERT INTO content_change_ledger(
                id,event_type,entity_type,entity_id,operation,reason,actor,tool_id,tool_version,transaction_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (event_id, "source_registered", entity, event_id, operation, "test mutation", "test", "test_schema_v2_34", "1.0.0", "tx-test"),
        )

    def build_minimal_lineage(self, conn: sqlite3.Connection) -> dict[str, str]:
        ids = {name: f"{name}:{uuid.uuid4().hex}" for name in (
            "source_document", "run_event", "run", "run_started", "source_event", "source_version",
            "source_state", "extract_event", "extract", "block_event", "block", "item_event", "item",
            "item_evidence_event", "item_evidence", "question", "link_event", "internal_event", "internal",
        )}
        conn.execute(
            "INSERT INTO source_documents(id,relative_path,file_hash,file_type,parse_status) VALUES(?,?,?,?,?)",
            (ids["source_document"], "fixtures/source.doc", digest("source"), "doc", "pending"),
        )
        self.add_event(conn, ids["run_event"], entity="content_import_runs")
        conn.execute(
            """INSERT INTO content_import_runs(
                id,mode,selection_json,selection_sha256,importer_id,importer_version,created_change_event_id
            ) VALUES(?,?,?,?,?,?,?)""",
            (ids["run"], "baseline", json.dumps({"paths": ["fixtures/source.doc"]}), digest("selection"), "test", "1.0.0", ids["run_event"]),
        )
        self.add_event(conn, ids["run_started"], entity="content_import_run_events")
        conn.execute(
            "INSERT INTO content_import_run_events(id,import_run_id,status,change_event_id) VALUES(?,?,?,?)",
            (str(uuid.uuid4()), ids["run"], "started", ids["run_started"]),
        )
        self.add_event(conn, ids["source_event"], entity="content_source_versions")
        conn.execute(
            """INSERT INTO content_source_versions(
                id,source_document_id,original_relative_path,original_sha256,file_size_bytes,file_type,
                trusted_source,registered_import_run_id,created_change_event_id
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (ids["source_version"], ids["source_document"], "fixtures/source.doc", digest("source"), 6, "doc", 1, ids["run"], ids["source_event"]),
        )
        self.add_event(conn, ids["source_state"], entity="content_source_lifecycle_events", operation="status_change")
        conn.execute(
            """INSERT INTO content_source_lifecycle_events(
                id,source_version_id,status,reason,retry_eligible,change_event_id
            ) VALUES(?,?,?,?,?,?)""",
            (str(uuid.uuid4()), ids["source_version"], "registered", "baseline registration", 1, ids["source_state"]),
        )
        self.add_event(conn, ids["extract_event"], entity="content_extraction_runs")
        conn.execute(
            """INSERT INTO content_extraction_runs(
                id,source_version_id,import_run_id,engine_id,engine_version,profile_schema,
                extraction_manifest_path,extraction_manifest_sha256,created_change_event_id
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (ids["extract"], ids["source_version"], ids["run"], "word-com", "test", "word-com-content-extraction-v1", "fixture.json", digest("manifest"), ids["extract_event"]),
        )
        raw = "1. 1+1=?"
        self.add_event(conn, ids["block_event"], entity="source_content_blocks")
        conn.execute(
            """INSERT INTO source_content_blocks(
                id,extraction_run_id,ordinal,block_kind,locator_json,raw_text,normalized_text,
                raw_sha256,normalized_sha256,created_change_event_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (ids["block"], ids["extract"], 0, "paragraph", json.dumps({"paragraph_index": 1}), raw, raw, digest(raw), digest(raw), ids["block_event"]),
        )
        self.add_event(conn, ids["item_event"], entity="content_items")
        conn.execute(
            """INSERT INTO content_items(
                id,source_version_id,item_kind,student_payload_json,content_sha256,created_change_event_id
            ) VALUES(?,?,?,?,?,?)""",
            (ids["item"], ids["source_version"], "question", json.dumps({"stem": raw, "options": []}), digest(raw), ids["item_event"]),
        )
        self.add_event(conn, ids["item_evidence_event"], entity="content_item_evidence")
        conn.execute(
            """INSERT INTO content_item_evidence(
                id,content_item_id,field_name,visibility,source_block_id,character_range_json,
                evidence_text,evidence_sha256,created_change_event_id
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (ids["item_evidence"], ids["item"], "stem", "student", ids["block"], "[0,8]", raw, digest(raw), ids["item_evidence_event"]),
        )
        conn.execute(
            """INSERT INTO questions(
                id,stem,options_json,answer,analysis,question_type,stage,source_document_id,
                source_fragment_id,content_hash,extraction_status,quality_status,review_status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ids["question"], raw, "[]", None, None, "\u586b\u7a7a\u9898", "\u521d\u4e2d", ids["source_document"], None, digest("question"), "structured", "pending", "pending"),
        )
        self.add_event(conn, ids["link_event"], entity="content_item_question_links")
        conn.execute(
            "INSERT INTO content_item_question_links(content_item_id,question_id,created_change_event_id) VALUES(?,?,?)",
            (ids["item"], ids["question"], ids["link_event"]),
        )
        self.add_event(conn, ids["internal_event"], entity="question_internal_evidence")
        conn.execute(
            """INSERT INTO question_internal_evidence(
                id,question_id,field_name,source_block_id,character_range_json,internal_payload,
                evidence_sha256,created_change_event_id
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (ids["internal"], ids["question"], "answer", ids["block"], "[0,8]", "2", digest("2"), ids["internal_event"]),
        )
        return ids

    def test_migration_is_idempotent_and_records_its_schema_event(self) -> None:
        conn = self.connection()
        try:
            self.assertTrue(conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone())
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM content_change_ledger WHERE id=?", (f"schema:{MIGRATION}",)).fetchone()[0],
                1,
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM content_items").fetchone()[0], 0)
        finally:
            conn.close()
        apply_v34(self.database)

    def test_new_source_derived_records_require_change_ledger_links_and_stay_append_only(self) -> None:
        conn = self.connection()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO content_import_runs(
                        id,mode,selection_json,selection_sha256,importer_id,importer_version,created_change_event_id
                    ) VALUES(?,?,?,?,?,?,?)""",
                    ("missing-event", "baseline", "{}", digest("missing"), "test", "1", "not-a-ledger-event"),
                )
            ids = self.build_minimal_lineage(conn)
            self.assertEqual(conn.execute("SELECT answer,analysis FROM questions WHERE id=?", (ids["question"],)).fetchone(), (None, None))
            self.assertEqual(
                conn.execute("SELECT internal_payload FROM question_internal_evidence WHERE question_id=?", (ids["question"],)).fetchone()[0],
                "2",
            )
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                conn.execute("UPDATE content_change_ledger SET reason='rewrite' WHERE id=?", (ids["run_event"],))
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                conn.execute("UPDATE source_content_blocks SET raw_text='rewrite' WHERE id=?", (ids["block"],))
            conn.commit()
        finally:
            conn.close()

    def test_dependency_requires_exactly_one_target(self) -> None:
        conn = self.connection()
        try:
            ids = self.build_minimal_lineage(conn)
            event = f"edge-event:{uuid.uuid4().hex}"
            self.add_event(conn, event, entity="content_dependency_edges")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO content_dependency_edges(
                        id,from_content_item_id,to_content_item_id,to_knowledge_point_id,relation_type,
                        edge_status,evidence_json,evidence_sha256,created_change_event_id
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), ids["item"], None, None, "requires", "candidate", "{}", digest("edge"), event),
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
