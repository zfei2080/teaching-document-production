from __future__ import annotations
from pathlib import Path
import shutil, sqlite3, tempfile, unittest

from apply_schema_v2_30 import apply as apply_v30
from apply_schema_v2_33 import MIGRATION, apply
from p1_4b_isolated_auxiliary_content_contract_audit import P1_2F_BASELINE_DATABASE

class SchemaV233Tests(unittest.TestCase):
 def test_migration_is_idempotent_and_preserves_existing_content_and_questions(self):
  with tempfile.TemporaryDirectory() as directory:
   database=Path(directory)/'copy.db'; shutil.copy2(P1_2F_BASELINE_DATABASE,database)
   connection=sqlite3.connect(database)
   try:
    before={table:connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('questions','controlled_content_sources','controlled_content_segments')}
   finally: connection.close()
   apply_v30(database); apply(database); apply(database)
   connection=sqlite3.connect(database)
   try:
    self.assertEqual(connection.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=?',(MIGRATION,)).fetchone()[0],1)
    self.assertEqual({table:connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in before},before)
    self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name='current_controlled_auxiliary_content_evidence'").fetchone()[0],1)
   finally: connection.close()

if __name__=='__main__': unittest.main()
