"""Validate source-bound worked-example and summary evidence only in an isolated DB."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import json, shutil, sqlite3

from apply_schema_v2_30 import apply as apply_v30
from apply_schema_v2_33 import MIGRATION, apply as apply_v33
from docx_forensics import extract_paragraphs

ROOT=Path(__file__).resolve().parent
LIVE_DATABASE=ROOT/'data'/'dev'/'teaching_docs_dev.db'
P1_2F_BASELINE_DATABASE=ROOT/'data'/'dev'/'backups'/'p1-2f'/'teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db'
SOURCE_AUDIT=ROOT/'output'/'audits'/'p1-2b_controlled_content_validation.json'
MARKER_AUDIT=ROOT/'output'/'audits'/'p1-4b_g1_g3_g7_explicit_marker_discovery.json'
DEFAULT_REPORT=ROOT/'output'/'audits'/'p1-4b_isolated_auxiliary_content_contract_audit.json'
TEXTBOOK_ID='bsd-math-grade8-lower-2026-spring-extsrc'
NODE_ID='bsd-math-8x-2026-node-01-section-02'
SCHEMA='p1-4b-isolated-auxiliary-content-contract-audit-v1'
PROTECTED_TABLES=('questions','question_textbooks','question_knowledge_points','selection_plans','teaching_documents','document_questions','quality_reports','question_usage','controlled_content_sources','controlled_content_segments')
LIVE_TABLES=('questions','teaching_documents','document_questions','quality_reports','question_usage')

class AuxiliaryContentContractError(RuntimeError): pass

def _sha(path:Path)->str: return sha256(path.read_bytes()).hexdigest().upper()
def _hash(value:object)->str: return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest().upper()
def _counts(c:sqlite3.Connection,tables:tuple[str,...])->dict[str,int]: return {t:c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}
def _read(path:Path)->dict[str,Any]:
    try: value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as exc: raise AuxiliaryContentContractError(f'audit_unreadable:{path.name}') from exc
    if not isinstance(value,dict): raise AuxiliaryContentContractError(f'audit_not_object:{path.name}')
    return value

def _live(path:Path)->dict[str,object]:
    c=sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True)
    try:return {'sha256':_sha(path),'counts':_counts(c,LIVE_TABLES),'v2_33_applied':c.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=?',(MIGRATION,)).fetchone()[0]==1}
    finally:c.close()

def _source_records()->dict[str,dict[str,Any]]:
    payload=_read(SOURCE_AUDIT)
    if payload.get('schema')!='p1-2b-controlled-content-run-v1': raise AuxiliaryContentContractError('source_audit_schema_invalid')
    records=payload.get('records')
    if not isinstance(records,list): raise AuxiliaryContentContractError('source_audit_records_invalid')
    result={str(r.get('source_key')):r for r in records if isinstance(r,dict) and isinstance(r.get('source_key'),str)}
    if 'basic-knowledge-explanation' not in result: raise AuxiliaryContentContractError('basic_knowledge_source_missing')
    return result

def _candidate(marker_payload:dict[str,Any], role:str, marker:str)->dict[str,Any]:
    rows=marker_payload.get('candidates')
    if not isinstance(rows,list): raise AuxiliaryContentContractError('marker_audit_candidates_invalid')
    found=[r for r in rows if isinstance(r,dict) and r.get('source_key')=='basic-knowledge-explanation' and r.get('role_candidate')==role and r.get('marker')==marker]
    if not found: raise AuxiliaryContentContractError(f'candidate_missing:{role}:{marker}')
    return sorted(found,key=lambda r:int(r['paragraph_index']))[0]

def _current_evidence(candidate:dict[str,Any], record:dict[str,Any], evidence_type:str)->dict[str,Any]:
    manifest=record.get('manifest')
    if not isinstance(manifest,dict): raise AuxiliaryContentContractError('source_manifest_invalid')
    source=manifest.get('source') or {}; archive=manifest.get('archive') or {}; converted=(manifest.get('conversion') or {}).get('converted') or {}
    paths={'source':Path(str(source.get('path',''))),'archive':Path(str(archive.get('path',''))),'converted':Path(str(converted.get('path','')))}
    expected={'source':str(source.get('sha256','')),'archive':str(archive.get('sha256','')),'converted':str(converted.get('sha256',''))}
    if any(not p.is_file() or _sha(p).casefold()!=expected[name].casefold() for name,p in paths.items()): raise AuxiliaryContentContractError('controlled_source_not_current')
    if candidate.get('source_sha256')!=expected['source'] or candidate.get('converted_sha256')!=expected['converted']: raise AuxiliaryContentContractError('candidate_source_hash_not_bound')
    paragraphs=tuple(extract_paragraphs(paths['converted']))
    index=candidate.get('paragraph_index')
    if not isinstance(index,int) or index<0: raise AuxiliaryContentContractError('candidate_paragraph_index_invalid')
    matches=[paragraph for paragraph in paragraphs if getattr(paragraph, 'index', None)==index]
    if len(matches)!=1: raise AuxiliaryContentContractError('candidate_paragraph_index_invalid')
    paragraph=matches[0]
    marker=str(candidate.get('marker',''))
    if paragraph.sha256!=candidate.get('paragraph_sha256') or marker not in paragraph.text: raise AuxiliaryContentContractError('candidate_paragraph_not_current')
    fragment_hash=sha256(paragraph.text.encode('utf-8')).hexdigest().upper()
    return {'evidence_type':evidence_type,'source_key':str(candidate['source_key']),'original_sha256':expected['source'],'archive_sha256':expected['archive'],'converted_sha256':expected['converted'],'paragraph_index':paragraph.index,'paragraph_sha256':paragraph.sha256,'marker':marker,'fragment_sha256':fragment_hash,'excerpt':str(candidate.get('excerpt',''))}

def _insert(connection:sqlite3.Connection,evidence:dict[str,Any])->str:
    row=connection.execute('SELECT id FROM controlled_content_sources WHERE original_sha256=? AND archive_sha256=? AND converted_sha256=?',(evidence['original_sha256'],evidence['archive_sha256'],evidence['converted_sha256'])).fetchall()
    if len(row)!=1: raise AuxiliaryContentContractError('current_controlled_source_not_unique')
    source_id=str(row[0][0]); payload={**evidence,'source_id':source_id,'textbook_id':TEXTBOOK_ID,'curriculum_node_id':NODE_ID}; evidence_hash=_hash(payload); evidence_id='aux:'+evidence_hash[:24]
    connection.execute('INSERT INTO controlled_auxiliary_content_evidence(id,import_run_id,controlled_source_id,textbook_id,curriculum_node_id,evidence_type,source_key,original_sha256,archive_sha256,converted_sha256,paragraph_index,paragraph_sha256,marker,fragment_sha256,evidence_json,evidence_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(evidence_id,'aux-run',source_id,TEXTBOOK_ID,NODE_ID,evidence['evidence_type'],evidence['source_key'],evidence['original_sha256'],evidence['archive_sha256'],evidence['converted_sha256'],evidence['paragraph_index'],evidence['paragraph_sha256'],evidence['marker'],evidence['fragment_sha256'],json.dumps(payload,ensure_ascii=False,sort_keys=True),evidence_hash))
    return evidence_id

def run_isolated_auxiliary_content_contract_audit(*,report_path:str|Path=DEFAULT_REPORT,live_database:str|Path=LIVE_DATABASE,baseline_database:str|Path=P1_2F_BASELINE_DATABASE)->dict[str,object]:
    live=Path(live_database).resolve(); baseline=Path(baseline_database).resolve()
    if not live.is_file() or not baseline.is_file(): raise AuxiliaryContentContractError('required_database_missing')
    live_before=_live(live); marker=_read(MARKER_AUDIT)
    if marker.get('schema')!='p1-4b-explicit-teaching-role-marker-discovery-v1': raise AuxiliaryContentContractError('marker_audit_schema_invalid')
    records=_source_records(); worked=_current_evidence(_candidate(marker,'G3_controlled_example_or_method','\u4f8b\u9898'),records['basic-knowledge-explanation'],'worked_example'); summary=_current_evidence(_candidate(marker,'G7_summary_or_self_assessment','\u603b\u7ed3'),records['basic-knowledge-explanation'],'summary')
    with TemporaryDirectory(prefix='p1-4b-auxiliary-') as directory:
      isolated=Path(directory)/'development-copy.db'; shutil.copy2(baseline,isolated); apply_v30(isolated); apply_v33(isolated)
      c=sqlite3.connect(isolated); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON')
      try:
       before=_counts(c,PROTECTED_TABLES)
       manifest_hash=_hash({'worked':worked,'summary':summary})
       c.execute("INSERT INTO controlled_auxiliary_content_import_runs(id,manifest_hash,status) VALUES('aux-run',?,'validated_evidence')",(manifest_hash,))
       ids=[_insert(c,worked),_insert(c,summary)]
       try:
        invalid={**summary,'evidence_type':'self_assessment'}; _insert(c,invalid); self_assessment_rejected=False
       except sqlite3.IntegrityError as exc:
        self_assessment_rejected='self assessment requires explicit self-assessment marker' in str(exc)
       current=[dict(r) for r in c.execute('SELECT evidence_type,source_key,marker,paragraph_index FROM current_controlled_auxiliary_content_evidence ORDER BY evidence_type')]
       after=_counts(c,PROTECTED_TABLES); applied=c.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=?',(MIGRATION,)).fetchone()[0]==1
      finally:c.close()
    live_after=_live(live)
    if live_before!=live_after: raise AuxiliaryContentContractError('isolated_audit_changed_live_database')
    if not applied or before!=after or not self_assessment_rejected: raise AuxiliaryContentContractError('isolated_auxiliary_contract_invariant_failed')
    types={str(r['evidence_type']) for r in current}
    if types!={'worked_example','summary'}: raise AuxiliaryContentContractError('isolated_auxiliary_content_supply_invalid')
    report={'schema':SCHEMA,'purpose':'verify_source_bound_auxiliary_material_evidence_without_role_assignment_or_student_document_generation','decision':'worked_example_evidence_verified_summary_without_self_assessment_remains_blocked','live_database':{'sha256_before':live_before['sha256'],'sha256_after':live_after['sha256'],'unchanged':True,'v2_33_applied':live_after['v2_33_applied'],'counts':live_after['counts']},'isolated_database':{'baseline_path':str(baseline),'v2_33_applied':applied,'protected_counts_before':before,'protected_counts_after':after,'protected_counts_unchanged':before==after},'evidence_ids':ids,'current_evidence':current,'supply':{'worked_example_ready':'worked_example' in types,'summary_ready':'summary' in types,'self_assessment_ready':'self_assessment' in types,'summary_self_assessment_ready':{'summary','self_assessment'}<=types},'self_assessment_from_summary_rejected':True,'question_role_assignment_performed':False,'student_document_generation_authorized':False}
    out=Path(report_path).resolve(); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8'); return report
