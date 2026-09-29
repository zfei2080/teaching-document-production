"""P1-2d real-development-database migration and two-segment import exercise."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from shutil import copy2
import json, sqlite3
from apply_schema_v2_26 import apply
from apply_schema_v2_27 import apply as apply_v27
from controlled_content_temp_import import build_manifest, import_manifest, digest_bytes, canonical, invalidate_changed_content_sources
ROOT=Path(__file__).resolve().parent
DB=ROOT/'data'/'dev'/'teaching_docs_dev.db'
PROTECTED=('questions','question_textbooks','question_knowledge_points','question_verifications','teaching_documents','quality_reports','question_usage')
def digest(path): return sha256(path.read_bytes()).hexdigest().upper()
def table_hashes(path):
 conn=sqlite3.connect(path); conn.row_factory=sqlite3.Row
 try:
  result={}
  for table in PROTECTED:
   rows=[dict(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]
   result[table]=digest_bytes(canonical(rows).encode('utf-8'))
  return result
 finally: conn.close()
def main():
 before_db=digest(DB); before_tables=table_hashes(DB)
 backups=ROOT/'data'/'dev'/'backups'; backups.mkdir(parents=True,exist_ok=True); backup=backups/f'p1-2d_before_{before_db[:16]}.db'
 if not backup.exists(): copy2(DB,backup)
 apply(DB)
 apply_v27(DB)
 manifest=build_manifest(ROOT/'output'/'audits'/'p1-2b_controlled_content_validation.json',workspace=ROOT)
 mh=digest_bytes(canonical(manifest).encode('utf-8'))
 conn=sqlite3.connect(DB)
 try:
  corrected=conn.execute("UPDATE controlled_content_segments SET invalidated_at=datetime('now'), invalidation_reason='catalog_contract_identifier_corrected' WHERE invalidated_at IS NULL AND (textbook_id='bsd-math-8x-2026-extsrc' OR curriculum_node_id='bsd-math-8x-2026-topic-003')").rowcount
  exists=conn.execute('SELECT id FROM controlled_content_import_runs WHERE manifest_sha256=?',(mh,)).fetchone()
  outcome={'status':'reused','run_id':exists[0]} if exists else import_manifest(conn,manifest)
  invalidation=invalidate_changed_content_sources(conn)
  invalidation['catalog_contract_corrections']=corrected
  counts={t:conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('controlled_content_import_runs','controlled_content_sources','controlled_content_segments','current_controlled_content_segments')}
 finally: conn.close()
 after_tables=table_hashes(DB); after_db=digest(DB)
 report={'schema':'p1-2d-real-content-import-v1','backup':str(backup),'migration':'v2.26+v2.27-controlled-content-evidence','outcome':outcome,'content_invalidation':invalidation,'content_counts':counts,'protected_table_hashes_before':before_tables,'protected_table_hashes_after':after_tables,'protected_tables_unchanged':before_tables==after_tables,'database_hash_before':before_db,'database_hash_after':after_db}
 dest=ROOT/'output'/'audits'/'p1-2d_real_content_import.json'; dest.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
 print(f'report={dest}'); print('protected_tables_unchanged='+str(report['protected_tables_unchanged'])); print('current_segments='+str(counts['current_controlled_content_segments']))
 return 0 if report['protected_tables_unchanged'] and counts['current_controlled_content_segments']==2 else 2
if __name__=='__main__': raise SystemExit(main())
