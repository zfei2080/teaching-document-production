from __future__ import annotations
import sqlite3,tempfile,unittest
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
from apply_schema_v2_26 import apply as v26
from apply_schema_v2_27 import MIGRATION,apply as v27
ROOT=Path(__file__).parent; BASE=tuple(f'schema_v2{n}.sql' for n in ('','_1','_2','_3','_4','_5','_6','_7','_8','_9','_10','_11','_12','_13','_14','_15'))
class SchemaV227Tests(unittest.TestCase):
 def test_revision_migration_is_idempotent_and_allows_corrected_segment_contract(self):
  with tempfile.TemporaryDirectory() as temp:
   p=Path(temp)/'db.sqlite'; c=sqlite3.connect(p)
   try:
    for f in BASE:c.executescript((ROOT/f).read_text(encoding='utf-8'))
    c.commit()
   finally:c.close()
   for fn in (v16,v17,v18,v19,v20,v21,v22,v24,v25,v26):fn(p)
   c=sqlite3.connect(p)
   try:
    c.execute("INSERT INTO controlled_content_import_runs VALUES('r','A','B','i','v','validated','x')")
    c.execute("INSERT INTO controlled_content_sources VALUES(?,?,?,?,?,?,?,?,?,?,?)",('s','r','o','A'*64,'a','A'*64,'c','B'*64,'C'*64,'passed','D'*64))
    c.execute("INSERT INTO controlled_content_segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('old','s','seg','knowledge_explanation','public_core','oldbook','oldnode','[]','[]','[0,1]','E'*64,'candidate_only',None,None))
    c.commit()
   finally:c.close()
   v27(p);v27(p)
   c=sqlite3.connect(p)
   try:
    self.assertEqual(c.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=?',(MIGRATION,)).fetchone()[0],1)
    c.execute("INSERT INTO controlled_content_segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('new','s','seg','knowledge_explanation','public_core','newbook','newnode','[]','[]','[0,1]','E'*64,'candidate_only',None,None))
    self.assertEqual(c.execute('SELECT COUNT(*) FROM controlled_content_segments').fetchone()[0],2)
   finally:c.close()
if __name__=='__main__':unittest.main()
