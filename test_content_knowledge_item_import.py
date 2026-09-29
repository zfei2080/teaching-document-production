"""Tests for source-derived knowledge and worked-example item import."""
from __future__ import annotations
from hashlib import sha256
import shutil,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from apply_schema_v2_30 import apply as apply_v30
from apply_schema_v2_34 import apply as apply_v34
from apply_schema_v2_35 import apply as apply_v35
from content_library_extraction import import_current_word_source
from content_knowledge_item_import import import_knowledge_items
from source_library_intake import digest_file,register_sources
ROOT=Path(__file__).parent
BASELINE=ROOT/"data"/"dev"/"backups"/"p1-2f"/"teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"
def h(value:str)->str:return sha256(value.encode("utf-8")).hexdigest().upper()
class ContentKnowledgeItemImportTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.database=self.root/"db.sqlite";self.source_root=self.root/"sources";self.source_root.mkdir();self.source=self.source_root/"knowledge.doc";self.source.write_bytes(b"source")
  shutil.copy2(BASELINE,self.database);apply_v30(self.database);apply_v34(self.database);apply_v35(self.database);self.c=sqlite3.connect(self.database);self.c.execute("pragma foreign_keys=on");register_sources(self.c,source_root=self.source_root,mode="baseline",explicit_paths=None,workspace=ROOT,actor="test");self.version=self.c.execute("select id from content_source_versions where original_relative_path='knowledge.doc'").fetchone()[0]
  with patch("content_library_extraction.extract_document",return_value=self.profile()):import_current_word_source(self.c,source_version_id=self.version,source_root=self.source_root,workspace=ROOT,manifest_root=self.root/"manifest",actor="test")
 def tearDown(self):self.c.close();self.temp.cleanup()
 def profile(self):
  texts=["\u3010\u5b66\u4e60\u76ee\u6807\u3011","\u539f\u77e5\u8bc6\u5185\u5bb9","\u7c7b\u578b\u4e00\u3001\u6d4b\u8bd5","1\u3001\u539f\u4f8b\u9898","\u3010\u7b54\u6848\u4e0e\u89e3\u6790\u3011","\u539f\u89e3\u6cd5"]
  return {"schema":"word-com-content-extraction-v1","file_type":"doc","source_sha256":digest_file(self.source),"normalized_content_sha256":h("".join(texts)),"engine_id":"Microsoft Word COM","engine_version":"test","blocks":[{"ordinal":i,"kind":"paragraph","locator":{"paragraph_index":i+1},"raw_text":x,"normalized_text":x,"raw_sha256":h(x),"normalized_sha256":h(x)} for i,x in enumerate(texts)],"assets":[]}
 def test_imports_knowledge_and_worked_example_without_creating_question(self):
  r=import_knowledge_items(self.c,source_version_id=self.version,source_root=self.source_root,workspace=ROOT,actor="test")
  self.assertEqual((r.knowledge_notes,r.worked_examples,r.student_evidence,r.internal_evidence),(1,1,3,2))
  self.assertEqual(self.c.execute("select count(*) from questions").fetchone()[0],20)
  self.assertEqual(dict(self.c.execute("select item_kind,count(*) from content_items where source_version_id=? group by item_kind",(self.version,)).fetchall()),{"knowledge_note":1,"worked_example":1})
  self.assertEqual(dict(self.c.execute("select visibility,count(*) from content_item_evidence e join content_items i on i.id=e.content_item_id where i.source_version_id=? group by visibility",(self.version,)).fetchall()),{"internal":2,"student":3})
  self.assertEqual([tuple(x) for x in self.c.execute("select distinct tool_id,tool_version from content_change_ledger where import_run_id=?",(r.import_run_id,)).fetchall()],[("content-library-knowledge-item-import","1.0.0")])
  self.assertEqual(import_knowledge_items(self.c,source_version_id=self.version,source_root=self.source_root,workspace=ROOT,actor="test").status,"already_imported")
if __name__=="__main__":unittest.main()
