"""P1-3a q013 pending mapping, draft evidence and audit; never approves q013."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import json,sqlite3
from controlled_question_mapping_import import import_question_mappings
from question_curriculum_mapping_evidence import MappingEvidence,record_mapping_evidence
from question_auto_mapping_audit import AutoMappingAuditRequest,record_auto_mapping_audit
ROOT=Path(__file__).resolve().parent;DB=ROOT/'data'/'dev'/'teaching_docs_dev.db'
Q='golden-q013';TEXTBOOK='bsd-math-grade8-lower-2026-spring-extsrc';NODE='bsd-math-8x-2026-node-01-section-02';POINTS=('kp-bsd8x-isosceles-triangle-properties-v1','kp-triangle-side-inequality-v1')
def digest(p):return sha256(p.read_bytes()).hexdigest()
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def snapshot(c):
 tables=('questions','question_textbooks','question_knowledge_points','question_verifications','teaching_documents','quality_reports','question_usage')
 out={}
 for t in tables:
  cur=c.execute(f'SELECT * FROM {t} ORDER BY rowid');out[t]=sha256(canonical([dict(zip([x[0] for x in cur.description],r)) for r in cur.fetchall()]).encode()).hexdigest()
 return out
def main():
 root=ROOT/'data'/'dev'/'p1-3a-knowledge'; root.mkdir(parents=True,exist_ok=True); knowledge_report=ROOT/'output'/'audits'/'p1-3a_knowledge_catalog.json'
 c=sqlite3.connect(DB);c.execute('PRAGMA foreign_keys=ON');c.row_factory=sqlite3.Row
 try:
  before=snapshot(c); q=c.execute('SELECT content_hash FROM questions WHERE id=?',(Q,)).fetchone(); snap=c.execute('SELECT input_hash FROM question_input_snapshots WHERE question_id=? AND invalidated_at IS NULL',(Q,)).fetchone(); ka=c.execute("SELECT id,audit_manifest_hash FROM knowledge_mapping_audits WHERE status='approved' ORDER BY created_at DESC LIMIT 1").fetchone()
  if not all((q,snap,ka)) or not knowledge_report.is_file():raise RuntimeError('p1_3a_prerequisite_evidence_missing')
  source={'question_id':Q,'current_input_hash':snap['input_hash'],'question_content_hash':q['content_hash'],'knowledge_audit_id':ka['id'],'knowledge_audit_hash':ka['audit_manifest_hash'],'knowledge_catalog_report_sha256':digest(knowledge_report)}
 finally:c.close()
 source_path=root/'q013_mapping_source.json';source_path.write_text(json.dumps(source,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8');sh=digest(source_path);ref='p1-3a:q013-current-question-and-approved-knowledge-evidence'
 manifest={'schema_version':'controlled-question-mapping-manifest-v1','source':{'reference':ref,'file':source_path.name,'sha256':sh},'mappings':[{'question_id':Q,'textbook_id':TEXTBOOK,'curriculum_node_id':NODE,'knowledge_points':[{'knowledge_point_id':POINTS[0],'relation_type':'primary'},{'knowledge_point_id':POINTS[1],'relation_type':'secondary'}]}]}
 mp=root/'q013_mapping_manifest.json';mp.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
 c=sqlite3.connect(DB);c.execute('PRAGMA foreign_keys=ON');c.row_factory=sqlite3.Row
 try:
  before=snapshot(c); exists=c.execute('SELECT 1 FROM question_textbook_imports WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?',(Q,TEXTBOOK,NODE)).fetchone()
  mapping={'status':'reused'} if exists else import_question_mappings(c,mp)
  features={'exercise':'P1-3a','task_theme':'等腰三角形','core_theme':'等腰三角形一边长分类讨论','exact_theme_anchor':'等腰三角形','required_knowledge':['等腰三角形的性质','三角形三边关系'],'required_knowledge_evidence':{'等腰三角形的性质':{'supported':True,'source':'q013 knowledge fragment + controlled knowledge explanation segment'},'三角形三边关系':{'supported':True,'source':'q013 knowledge fragment + q002 trusted source analysis'}}}
  evidence=MappingEvidence(question_id=Q,textbook_id=TEXTBOOK,curriculum_node_id=NODE,features=features,decider_id='p1-3a-q013-deterministic-scope',decider_version='1.0.0',status='candidate',confidence=1.0,reason='当前输入、可信来源、已批准知识点、目标节点和全部必需知识均有受控证据；仅为草案。')
  existing=c.execute("SELECT id FROM question_curriculum_mapping_evidence WHERE question_id=? AND textbook_id=? AND curriculum_node_id=? AND status='candidate' AND invalidated_at IS NULL",(Q,TEXTBOOK,NODE)).fetchone()
  eid=existing['id'] if existing else record_mapping_evidence(c,evidence)
  audit=record_auto_mapping_audit(c,AutoMappingAuditRequest(evidence_id=eid,reuse_existing=True))
  c.commit()
  after=snapshot(c)
 finally:c.close()
 report={'schema':'p1-3a-q013-mapping-run-v1','mapping_import':mapping,'evidence_id':eid,'audit_status':audit.audit_status,'audit_id':audit.audit_id,'question_approved':False,'protected_unchanged':all(before[t]==after[t] for t in ('questions','question_verifications','teaching_documents','quality_reports','question_usage')),'mapping_tables_changed':before['question_textbooks']!=after['question_textbooks'] or before['question_knowledge_points']!=after['question_knowledge_points']}
 out=ROOT/'output'/'audits'/'p1-3a_q013_mapping_evidence.json';out.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8');print(f'report={out}');print('audit_status='+audit.audit_status);print('question_approved=False')
 return 0 if audit.audit_status=='pass' and report['protected_unchanged'] else 2
if __name__=='__main__':raise SystemExit(main())
