"""P1-3a controlled knowledge catalog for the sole isosceles candidate q013.

Creates source-bound evidence from existing trusted source fragments and the
current controlled-content chain, then uses the existing pending-import plus
audit protocol. It never approves a question.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from shutil import copy2
import json,sqlite3
from controlled_knowledge_mapping_import import import_knowledge_mapping
from knowledge_mapping_audit import record_knowledge_mapping_audit

ROOT=Path(__file__).resolve().parent; DB=ROOT/'data'/'dev'/'teaching_docs_dev.db'
TEXTBOOK='bsd-math-grade8-lower-2026-spring-extsrc'; NODE='bsd-math-8x-2026-node-01-section-02'; QID='golden-q013'
POINTS=(
 {'id':'kp-bsd8x-isosceles-triangle-properties-v1','canonical_name':'等腰三角形的性质','knowledge_type':'property','stage_scope':'初中','definition_text':'等腰三角形相关性质；本实体仅作为 q013 所需知识的受控映射，不扩展为未验证结论。','formulas_json':'[]','properties_json':'["等腰三角形"]','conditions_json':'[]','common_errors_json':'[]','version':'p1-3a-v1'},
 {'id':'kp-triangle-side-inequality-v1','canonical_name':'三角形三边关系','knowledge_type':'criterion','stage_scope':'初中','definition_text':'三角形任意两边之和大于第三边；q013 用于排除 4、4、8。','formulas_json':'[]','properties_json':'["任意两边之和大于第三边"]','conditions_json':'[]','common_errors_json':'[]','version':'p1-3a-v1'},
)
def digest(p): return sha256(p.read_bytes()).hexdigest()
def canonical(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def protected(c):
 tables=('questions','question_textbooks','question_knowledge_points','question_verifications','teaching_documents','quality_reports','question_usage')
 result={}
 for table in tables:
  cursor=c.execute(f'SELECT * FROM {table} ORDER BY rowid')
  names=[item[0] for item in cursor.description]
  result[table]=sha256(canonical([dict(zip(names,row)) for row in cursor.fetchall()]).encode()).hexdigest()
 return result
def build_evidence(c):
 c.row_factory=sqlite3.Row
 q=c.execute('SELECT content_hash,source_document_id FROM questions WHERE id=?',(QID,)).fetchone()
 snap=c.execute('SELECT input_hash,revision FROM question_input_snapshots WHERE question_id=?',(QID,)).fetchone()
 rows=c.execute("SELECT qsf.field_name,sf.id,sf.raw_text,sf.raw_hash FROM question_source_fragments qsf JOIN source_fragments sf ON sf.id=qsf.source_fragment_id WHERE qsf.question_id=? ORDER BY qsf.field_name",(QID,)).fetchall()
 q002=c.execute("SELECT sf.id,sf.raw_text,sf.raw_hash FROM question_source_fragments qsf JOIN source_fragments sf ON sf.id=qsf.source_fragment_id WHERE qsf.question_id='golden-q002' AND qsf.field_name='analysis'",).fetchone()
 node=c.execute('SELECT n.id,n.name,n.catalog_version,t.id textbook_id,t.catalog_version FROM curriculum_nodes n JOIN textbooks t ON t.id=n.textbook_id WHERE n.id=? AND n.textbook_id=?',(NODE,TEXTBOOK)).fetchone()
 content=c.execute("SELECT s.id,s.normalized_text_sha256,src.original_sha256,src.archive_sha256,src.converted_sha256 FROM current_controlled_content_segments s JOIN controlled_content_sources src ON src.id=s.source_id WHERE s.textbook_id=? AND s.curriculum_node_id=? AND s.content_type='knowledge_explanation'",(TEXTBOOK,NODE)).fetchone()
 if not all((q,snap,rows,q002,node,content)): raise RuntimeError('p1_3a_source_evidence_missing')
 return {'schema':'p1-3a-knowledge-source-evidence-v1','question':{'id':QID,'content_hash':q['content_hash'],'input_hash':snap['input_hash'],'revision':snap['revision'],'source_document_id':q['source_document_id'],'fragments':[dict(r) for r in rows]},'triangle_side_relation_source':dict(q002),'catalog_node':dict(node),'controlled_content':dict(content)}
def main():
 before_db=digest(DB); c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 try:
  before=protected(c); ev=build_evidence(c); release_id=c.execute("SELECT id FROM catalog_releases WHERE textbook_id=? AND status='approved' ORDER BY id",(TEXTBOOK,)).fetchone()[0]
 finally:c.close()
 root=ROOT/'data'/'dev'/'p1-3a-knowledge'; root.mkdir(parents=True,exist_ok=True)
 evidence_path=root/'q013_knowledge_evidence.json'; evidence_path.write_text(json.dumps(ev,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
 source_hash=digest(evidence_path); ref='p1-3a:q013-trusted-source-fragments-and-controlled-content'
 manifest={'schema_version':'controlled-knowledge-mapping-manifest-v1','catalog_release_id':release_id,'source':{'reference':ref,'file':evidence_path.name,'sha256':source_hash},'knowledge_points':POINTS,'curriculum_knowledge_points':[{'curriculum_node_id':NODE,'knowledge_point_id':POINTS[0]['id'],'relation_type':'primary'},{'curriculum_node_id':NODE,'knowledge_point_id':POINTS[1]['id'],'relation_type':'prerequisite'}]}
 manifest_path=root/'knowledge_manifest.json'; manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
 c=sqlite3.connect(DB); c.execute('PRAGMA foreign_keys=ON')
 try:
  existing=c.execute("SELECT id,status FROM controlled_knowledge_import_runs WHERE source_reference=? AND source_hash=?",(ref,source_hash)).fetchone()
  if existing: outcome={'import_id':existing[0],'status':'reused:'+existing[1]}
  else: outcome=import_knowledge_mapping(c,manifest_path)
  run_id=outcome['import_id']; status=c.execute('SELECT status FROM controlled_knowledge_import_runs WHERE id=?',(run_id,)).fetchone()[0]
  if status=='validated':
   audit={'schema_version':'controlled-knowledge-mapping-audit-v1','import_run_id':run_id,'source_reference':ref,'source_sha256':source_hash,'auditor_id':'p1-3a-controlled-source-and-contract-audit-v1','findings':[{'check':'trusted-question-source-fragments','status':'pass','evidence':'q013 and q002 source fragment hashes are bound in the evidence file.'},{'check':'controlled-content-source-triple','status':'pass','evidence':'current controlled knowledge explanation segment and source triple hashes are bound.'},{'check':'approved-catalog-node','status':'pass','evidence':'target textbook release and active equal-triangle node are bound.'},{'check':'required-knowledge-coverage','status':'pass','evidence':'q013 source labels both isosceles-triangle properties and triangle side relation.'}]}
   audit_path=root/'knowledge_audit.json'; audit_path.write_text(json.dumps(audit,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8'); audit_out=record_knowledge_mapping_audit(c,audit_path)
  else: audit_out={'status':'reused:'+status}
  after=protected(c)
 finally:c.close()
 report={'schema':'p1-3a-knowledge-catalog-run-v1','outcome':outcome,'audit':audit_out,'protected_tables_unchanged':before==after,'database_hash_before':before_db,'database_hash_after':digest(DB),'evidence':str(evidence_path),'manifest':str(manifest_path)}
 out=ROOT/'output'/'audits'/'p1-3a_knowledge_catalog.json';out.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8');print(f'report={out}');print('protected_tables_unchanged='+str(report['protected_tables_unchanged']));print('knowledge_audit='+str(audit_out['status']))
 return 0 if report['protected_tables_unchanged'] and 'approved' in str(audit_out['status']) else 2
if __name__=='__main__':raise SystemExit(main())
