"""Regression tests for source-bound DOCX media adjacency import and audit."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from asset_adjacency_audit import AssetAdjacencyAuditError, record_asset_adjacency_audit
from controlled_asset_adjacency_import import AssetAdjacencyManifestError, canonical_json_hash, import_asset_adjacency
from docx_forensics import extract_paragraphs
from question_asset_adjacency import build_manifest

ROOT = Path(__file__).parent
SCHEMAS = ("schema_v2.sql", "schema_v2_1.sql", "schema_v2_12.sql")
SOURCE = ROOT / "data" / "dev" / "golden-samples" / "golden_source_001.docx"


class ControlledAssetAdjacencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        for schema in SCHEMAS:
            self.conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
        self.source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        self.conn.execute(
            "INSERT INTO source_documents (id, relative_path, file_hash, file_type) VALUES ('source', ?, ?, 'docx')",
            (SOURCE.relative_to(ROOT).as_posix(), self.source_hash),
        )
        complete = build_manifest(SOURCE)
        assigned = next(item for item in complete["instances"] if item["assignment_status"] == "assigned")
        self.question_no = assigned["source_question_no"]
        self.paragraph = assigned["paragraph_index"]
        source_fragment = next(item for item in extract_paragraphs(SOURCE) if item.index == self.paragraph)
        self.conn.execute(
            "INSERT INTO source_fragments (id, source_document_id, location_type, paragraph_index, raw_text, raw_hash) VALUES ('fragment', 'source', 'paragraph', ?, ?, ?)",
            (self.paragraph, source_fragment.text, source_fragment.sha256),
        )
        self.conn.execute(
            """INSERT INTO questions (id, stem, question_type, stage, source_document_id, source_question_no, content_hash)
               VALUES ('question', '题干', '选择题', '初中', 'source', ?, 'content')""",
            (self.question_no,),
        )
        self.conn.execute(
            "INSERT INTO question_source_fragments (question_id, source_fragment_id, field_name, source_hash) VALUES ('question', 'fragment', 'stem', ?)",
            (source_fragment.sha256,),
        )
        self.conn.commit()
        self.manifest = {**complete, "instances": [assigned]}
        self.manifest["summary"] = {
            "candidate_count": complete["summary"]["candidate_count"], "media_instance_count": 1,
            "assigned_instance_count": 1, "unassigned_instance_count": 0, "ambiguous_instance_count": 0,
        }
        self.manifest.pop("manifest_sha256")
        self.manifest["manifest_sha256"] = canonical_json_hash(self.manifest)
        self.manifest_path = self.root / "adjacency.json"
        self.manifest_path.write_text(json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_import_is_evidence_only_then_audit_materializes_verified_asset(self):
        result = import_asset_adjacency(self.conn, self.manifest_path)
        self.assertEqual(result["status"], "validated")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM question_assets").fetchone()[0], 0)
        run_id = result["import_id"]
        audit = {
            "schema_version": "controlled-asset-adjacency-audit-v1", "import_run_id": run_id,
            "manifest_hash": self.manifest["manifest_sha256"], "source_document_id": "source",
            "source_sha256": self.source_hash, "auditor_id": "test",
            "findings": [{"check": "source-and-instance-identity", "status": "pass", "evidence": "fixture"}],
        }
        audit_path = self.root / "audit.json"
        audit_path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
        approved = record_asset_adjacency_audit(self.conn, audit_path)
        self.assertEqual(approved["verified_assets"], 1)
        asset = self.conn.execute("SELECT status, checksum, source_fragment_id FROM question_assets").fetchone()
        self.assertEqual(asset[0], "verified")
        self.assertEqual(asset[1], self.manifest["instances"][0]["media_sha256"])
        self.assertEqual(asset[2], "fragment")

    def test_tampered_manifest_is_rejected_without_writes(self):
        self.manifest["instances"][0]["assignment_reason"] = "tampered"
        self.manifest_path.write_text(json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(AssetAdjacencyManifestError):
            import_asset_adjacency(self.conn, self.manifest_path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM asset_adjacency_imports").fetchone()[0], 0)

    def test_direct_verified_status_flip_is_rejected(self):
        self.conn.execute("INSERT INTO question_assets (id, question_id, asset_type, status) VALUES ('untrusted', 'question', 'image', 'pending')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE question_assets SET status='verified' WHERE id='untrusted'")


if __name__ == "__main__":
    unittest.main()
