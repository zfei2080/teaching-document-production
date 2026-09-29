"""Offline regression tests for catalog import status normalization."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apply_schema_v2_16 import apply_schema as apply_schema_v2_16
from apply_schema_v2_17 import apply_schema as apply_schema_v2_17
from apply_schema_v2_18 import apply_schema as apply_schema_v2_18
from apply_schema_v2_19 import apply_schema as apply_schema_v2_19
from apply_schema_v2_20 import apply as apply_schema_v2_20
from apply_schema_v2_21 import apply as apply_schema_v2_21
from apply_schema_v2_22 import apply as apply_schema_v2_22
from normalize_catalog_import_status import NormalizationError, normalize_catalog_import_status


ROOT = Path(__file__).parent
BASE_SCHEMAS = tuple(
    f"schema_v2{suffix}.sql"
    for suffix in ("", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9", "_10", "_11", "_12", "_13", "_14", "_15")
)


class NormalizeCatalogImportStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "normalize_catalog_import.db"
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            for schema in BASE_SCHEMAS:
                conn.executescript((ROOT / schema).read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()
        for apply in (
            apply_schema_v2_16,
            apply_schema_v2_17,
            apply_schema_v2_18,
            apply_schema_v2_19,
            apply_schema_v2_20,
            apply_schema_v2_21,
            apply_schema_v2_22,
        ):
            apply(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._seed_fixture()
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def _seed_fixture(self) -> None:
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('book', 'Fixture Book', 'catalog-v1')")
        self.conn.execute(
            """INSERT INTO controlled_import_runs
               (id, import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version, status)
               VALUES ('catalog-import', 'catalog', 'fixture://catalog', 'catalog-hash',
                       'manifest-hash', 'fixture-importer', '1', 'approved')"""
        )
        self.conn.execute(
            """INSERT INTO catalog_releases
               (id, textbook_id, catalog_version, source_reference, source_hash, status)
               VALUES ('catalog-release', 'book', 'catalog-v1', 'fixture://catalog', 'catalog-hash', 'approved')"""
        )
        self.conn.execute(
            "INSERT INTO catalog_release_imports (catalog_release_id, import_run_id) VALUES ('catalog-release', 'catalog-import')"
        )
        self.conn.execute(
            """INSERT INTO catalog_audits
               (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id,
                audit_method, source_reference, source_hash, status, findings_json)
               VALUES ('catalog-audit', 'catalog-release', 'catalog-import', 'audit-hash',
                       'fixture-auditor', 'fixture-method', 'fixture://catalog', 'catalog-hash',
                       'approved', '[{"check":"bound-source","status":"pass","evidence":"fixture"}]')"""
        )

    def test_normalizes_only_the_unique_approved_catalog_triple(self) -> None:
        result = normalize_catalog_import_status(self.conn, 'catalog-import')
        self.assertEqual(result['to_status'], 'validated')
        self.assertEqual(
            self.conn.execute("SELECT status FROM controlled_import_runs WHERE id='catalog-import'").fetchone()[0],
            'validated',
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM catalog_releases WHERE id='catalog-release'").fetchone()[0],
            'approved',
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM catalog_audits WHERE id='catalog-audit'").fetchone()[0],
            'approved',
        )

    def test_missing_audit_fails_closed_with_zero_writes(self) -> None:
        self.conn.execute("DELETE FROM catalog_audits WHERE id='catalog-audit'")
        with self.assertRaisesRegex(NormalizationError, 'approved catalog audit'):
            normalize_catalog_import_status(self.conn, 'catalog-import')
        self.assertEqual(
            self.conn.execute("SELECT status FROM controlled_import_runs WHERE id='catalog-import'").fetchone()[0],
            'approved',
        )

    def test_source_mismatch_fails_closed_with_zero_writes(self) -> None:
        self.conn.execute("UPDATE catalog_audits SET source_hash='other-hash' WHERE id='catalog-audit'")
        with self.assertRaisesRegex(NormalizationError, 'source identity'):
            normalize_catalog_import_status(self.conn, 'catalog-import')
        self.assertEqual(
            self.conn.execute("SELECT status FROM controlled_import_runs WHERE id='catalog-import'").fetchone()[0],
            'approved',
        )

    def test_non_approved_release_fails_closed_with_zero_writes(self) -> None:
        self.conn.execute("UPDATE catalog_releases SET status='draft' WHERE id='catalog-release'")
        with self.assertRaisesRegex(NormalizationError, 'release must be approved'):
            normalize_catalog_import_status(self.conn, 'catalog-import')
        self.assertEqual(
            self.conn.execute("SELECT status FROM controlled_import_runs WHERE id='catalog-import'").fetchone()[0],
            'approved',
        )

    def test_non_catalog_or_non_approved_import_fails_closed_with_zero_writes(self) -> None:
        self.conn.execute("UPDATE controlled_import_runs SET import_kind='question_mapping' WHERE id='catalog-import'")
        with self.assertRaisesRegex(NormalizationError, 'catalog import'):
            normalize_catalog_import_status(self.conn, 'catalog-import')
        self.conn.execute("UPDATE controlled_import_runs SET import_kind='catalog', status='validated' WHERE id='catalog-import'")
        with self.assertRaisesRegex(NormalizationError, 'currently be approved'):
            normalize_catalog_import_status(self.conn, 'catalog-import')

    def test_multiple_linked_releases_fail_closed_with_zero_writes(self) -> None:
        self.conn.execute("INSERT INTO textbooks (id, name, catalog_version) VALUES ('book-2', 'Fixture Book 2', 'catalog-v2')")
        self.conn.execute(
            """INSERT INTO catalog_releases
               (id, textbook_id, catalog_version, source_reference, source_hash, status)
               VALUES ('catalog-release-2', 'book-2', 'catalog-v2', 'fixture://catalog', 'catalog-hash', 'approved')"""
        )
        self.conn.execute(
            "INSERT INTO catalog_release_imports (catalog_release_id, import_run_id) VALUES ('catalog-release-2', 'catalog-import')"
        )
        self.conn.execute(
            """INSERT INTO catalog_audits
               (id, catalog_release_id, import_run_id, audit_manifest_hash, auditor_id,
                audit_method, source_reference, source_hash, status, findings_json)
               VALUES ('catalog-audit-2', 'catalog-release-2', 'catalog-import', 'audit-hash-2',
                       'fixture-auditor', 'fixture-method', 'fixture://catalog', 'catalog-hash',
                       'approved', '[{"check":"bound-source","status":"pass","evidence":"fixture-2"}]')"""
        )
        with self.assertRaisesRegex(NormalizationError, 'exactly one catalog release'):
            normalize_catalog_import_status(self.conn, 'catalog-import')
        self.assertEqual(
            self.conn.execute("SELECT status FROM controlled_import_runs WHERE id='catalog-import'").fetchone()[0],
            'approved',
        )


if __name__ == '__main__':
    unittest.main()
