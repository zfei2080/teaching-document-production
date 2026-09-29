from __future__ import annotations
import sqlite3, tempfile, unittest
from pathlib import Path
from apply_schema_v2_16 import apply_schema as v16
from apply_schema_v2_17 import apply_schema as v17
from apply_schema_v2_18 import apply_schema as v18
from apply_schema_v2_19 import apply_schema as v19
from apply_schema_v2_20 import apply as v20
from apply_schema_v2_21 import apply as v21
from apply_schema_v2_22 import apply as v22
from apply_schema_v2_24 import apply as v24
from apply_schema_v2_25 import apply as v25
from apply_schema_v2_26 import MIGRATION, apply as v26
ROOT=Path(__file__).parent
BASE=tuple(f'schema_v2{suffix}.sql' for suffix in ('','_1','_2','_3','_4','_5','_6','_7','_8','_9','_10','_11','_12','_13','_14','_15'))
class SchemaV226Tests(unittest.TestCase):
 def test_migration_is_idempotent_and_does_not_change_question_tables(self):
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'db.sqlite'; conn=sqlite3.connect(path)
   try:
    for schema in BASE: conn.executescript((ROOT/schema).read_text(encoding='utf-8'))
    conn.commit()
   finally: conn.close()
   for apply in (v16,v17,v18,v19,v20,v21,v22,v24,v25): apply(path)
   conn=sqlite3.connect(path)
   try:
    before={table:conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('questions','question_textbooks','question_knowledge_points','question_verifications','teaching_documents','quality_reports','question_usage')}
   finally: conn.close()
   v26(path); v26(path)
   conn=sqlite3.connect(path)
   try:
    self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=?',(MIGRATION,)).fetchone()[0],1)
    for table in ('controlled_content_import_runs','controlled_content_sources','controlled_content_segments'):
     self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone())
    self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name='current_controlled_content_segments'").fetchone())
    after={table:conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in before}
    self.assertEqual(before,after)
   finally: conn.close()
if __name__=='__main__': unittest.main()
