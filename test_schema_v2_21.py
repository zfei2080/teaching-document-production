"""Controlled source intake schema checks using a disposable database."""
from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from apply_schema_v2_21 import apply

ROOT = Path(__file__).parent


class ControlledSourceIntakeSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "controlled_source_intake.db"
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript((ROOT / "schema_v2.sql").read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()
        apply(self.path)

    def tearDown(self) -> None:
        # SQLite closes synchronously, but Windows can briefly retain a file
        # handle after a connection has been finalized. Retry boundedly so this
        # disposable test database never turns a passing test into WinError 32.
        for attempt in range(5):
            try:
                self.tempdir.cleanup()
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def test_trusted_source_requires_original_archive_hash_binding(self) -> None:
        conn = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO source_documents
                    (id, relative_path, file_hash, file_type, original_relative_path,
                     original_file_hash, trusted_source)
                    VALUES ('bad', 'data/history/source-documents/a.docx', 'archive-hash', 'docx',
                            '<local-scratch>/a.docx', 'original-hash', 1)""")
            conn.execute("""INSERT INTO source_documents
                (id, relative_path, file_hash, file_type, original_relative_path,
                 original_file_hash, trusted_source)
                VALUES ('good', 'data/history/source-documents/a.docx', 'same-hash', 'docx',
                        '<local-scratch>/a.docx', 'same-hash', 1)""")
            row = conn.execute("SELECT trusted_source FROM source_documents WHERE id='good'").fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], 1)

    def test_migration_is_idempotent(self) -> None:
        apply(self.path)


if __name__ == "__main__":
    unittest.main()
