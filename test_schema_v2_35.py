"""Schema checks for complete source inventory support in v2.35."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from apply_schema_v2_30 import apply as apply_v30
from apply_schema_v2_34 import apply as apply_v34
from apply_schema_v2_35 import MIGRATION, apply as apply_v35

ROOT = Path(__file__).parent
BASELINE = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
)


class SchemaV235Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "development-copy.db"
        shutil.copy2(BASELINE, self.database)
        apply_v30(self.database)
        apply_v34(self.database)
        apply_v35(self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_migration_is_idempotent_and_supports_unsupported_file_inventory(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            self.assertTrue(connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone())
            self.assertTrue(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_source_intake_records'").fetchone())
            self.assertTrue(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_source_intake_lifecycle_events'").fetchone())
            self.assertTrue(connection.execute("SELECT 1 FROM pragma_table_info('content_source_versions') WHERE name='intake_record_id'").fetchone())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_change_ledger WHERE id=?", (f"schema:{MIGRATION}",)).fetchone()[0], 1)
        finally:
            connection.close()
        apply_v35(self.database)


if __name__ == "__main__":
    unittest.main()
