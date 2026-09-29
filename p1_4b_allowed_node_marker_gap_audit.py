"""Read-only discovery of diagnostic and self-assessment source gaps for P1-4b.

It uses the existing Word COM *read-only* profiler only; it never converts,
modifies, archives, uploads, or repairs a source document.  Full-chapter and directory-labelled prior-node sources are inventory envelopes;
question-level curriculum/dependency evidence, not their folder label, determines
whether a source question can later enter a class-progress scope.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
import json

from word_com_document_profile import profile_document

ROOT=Path(__file__).resolve().parent
SOURCE_ROOT=ROOT/'<local-scratch>'/'知识点'/'北师版初中数学重难点讲义+巩固练习（基础+提高）'
DEFAULT_REPORT=ROOT/'output'/'audits'/'p1-4b_allowed_node_marker_gap_audit.json'
SCHEMA='p1-4b-allowed-node-marker-gap-audit-v1'
SOURCE_SPECS=(
 ('\u30104\u3011\u521d\u4e8c\u4e0b\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/58\u7b49\u8170\u4e09\u89d2\u5f62\uff08\u57fa\u7840\uff09/\u7b49\u8170\u4e09\u89d2\u5f62\uff08\u57fa\u7840\uff09\u77e5\u8bc6\u8bb2\u89e3.doc','current-topic-basic-explanation','current_topic_exact'),
 ('\u30104\u3011\u521d\u4e8c\u4e0b\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/58\u7b49\u8170\u4e09\u89d2\u5f62\uff08\u57fa\u7840\uff09/\u7b49\u8170\u4e09\u89d2\u5f62\uff08\u57fa\u7840\uff09\u5de9\u56fa\u7ec3\u4e60.doc','current-topic-basic-practice','current_topic_exact'),
 ('\u30104\u3011\u521d\u4e8c\u4e0b\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/59\u7b49\u8170\u4e09\u89d2\u5f62\uff08\u63d0\u9ad8\uff09/\u7b49\u8170\u4e09\u89d2\u5f62\uff08\u63d0\u9ad8\uff09\u77e5\u8bc6\u8bb2\u89e3.doc','current-topic-advanced-explanation','current_topic_exact'),
 ('\u30104\u3011\u521d\u4e8c\u4e0b\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/59\u7b49\u8170\u4e09\u89d2\u5f62\uff08\u63d0\u9ad8\uff09/\u7b49\u8170\u4e09\u89d2\u5f62\uff08\u63d0\u9ad8\uff09\u5de9\u56fa\u7ec3\u4e60.doc','current-topic-advanced-practice','current_topic_exact'),
 ('\u30104\u3011\u521d\u4e8c\u4e0b\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/66\u300a\u4e09\u89d2\u5f62\u7684\u8bc1\u660e\u300b\u5168\u7ae0\u590d\u4e60\u4e0e\u5de9\u56fa\uff08\u57fa\u7840\uff09/\u300a\u4e09\u89d2\u5f62\u7684\u8bc1\u660e\u300b\u5168\u7ae0\u590d\u4e60\u4e0e\u5de9\u56fa--\u77e5\u8bc6\u8bb2\u89e3\uff08\u57fa\u7840\uff09.doc','chapter-review-basic-explanation','chapter_mixed_requires_segment_mapping'),
 ('\u30104\u3011\u521d\u4e8c\u4e0b\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/66\u300a\u4e09\u89d2\u5f62\u7684\u8bc1\u660e\u300b\u5168\u7ae0\u590d\u4e60\u4e0e\u5de9\u56fa\uff08\u57fa\u7840\uff09/\u300a\u4e09\u89d2\u5f62\u7684\u8bc1\u660e\u300b\u5168\u7ae0\u590d\u4e60\u4e0e\u5de9\u56fa--\u5de9\u56fa\u7ec3\u4e60\uff08\u57fa\u7840\uff09.doc','chapter-review-basic-practice','chapter_mixed_requires_segment_mapping'),
 ('\u30104\u3011\u521d\u4e8c\u4e0b\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/67\u300a\u4e09\u89d2\u5f62\u7684\u8bc1\u660e\u300b\u5168\u7ae0\u590d\u4e60\u4e0e\u5de9\u56fa\uff08\u63d0\u9ad8\uff09/\u300a\u4e09\u89d2\u5f62\u7684\u8bc1\u660e\u300b\u5168\u7ae0\u590d\u4e60\u4e0e\u5de9\u56fa--\u77e5\u8bc6\u8bb2\u89e3\uff08\u63d0\u9ad8\uff09.doc','chapter-review-advanced-explanation','chapter_mixed_requires_segment_mapping'),
 ('\u30104\u3011\u521d\u4e8c\u4e0b\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/67\u300a\u4e09\u89d2\u5f62\u7684\u8bc1\u660e\u300b\u5168\u7ae0\u590d\u4e60\u4e0e\u5de9\u56fa\uff08\u63d0\u9ad8\uff09/\u300a\u4e09\u89d2\u5f62\u7684\u8bc1\u660e\u300b\u5168\u7ae0\u590d\u4e60\u4e0e\u5de9\u56fa-\u5de9\u56fa\u7ec3\u4e60\uff08\u63d0\u9ad8\uff09.doc','chapter-review-advanced-practice','chapter_mixed_requires_segment_mapping'),
 ('\u30103\u3011\u521d\u4e8c\u4e0a\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/54\u4e09\u89d2\u5f62\u7684\u5185\u89d2\u548c(\u57fa\u7840)/\u4e09\u89d2\u5f62\u7684\u5185\u89d2\u548c(\u57fa\u7840)\u77e5\u8bc6\u8bb2\u89e3.doc','catalog-prior-node-grade-label-conflict-basic-explanation','catalog_prior_node_requires_question_level_mapping'),
 ('\u30103\u3011\u521d\u4e8c\u4e0a\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/54\u4e09\u89d2\u5f62\u7684\u5185\u89d2\u548c(\u57fa\u7840)/\u4e09\u89d2\u5f62\u7684\u5185\u89d2\u548c(\u57fa\u7840)\u5de9\u56fa\u7ec3\u4e60.doc','catalog-prior-node-grade-label-conflict-basic-practice','catalog_prior_node_requires_question_level_mapping'),
 ('\u30103\u3011\u521d\u4e8c\u4e0a\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/55\u4e09\u89d2\u5f62\u7684\u5185\u89d2\u548c(\u63d0\u9ad8)/\u4e09\u89d2\u5f62\u7684\u5185\u89d2\u548c(\u63d0\u9ad8)\u77e5\u8bc6\u8bb2\u89e3.doc','catalog-prior-node-grade-label-conflict-advanced-explanation','catalog_prior_node_requires_question_level_mapping'),
 ('\u30103\u3011\u521d\u4e8c\u4e0a\u518c-\u6570\u5b66\u5317\u5e08\u5927\u7248/55\u4e09\u89d2\u5f62\u7684\u5185\u89d2\u548c(\u63d0\u9ad8)/\u4e09\u89d2\u5f62\u7684\u5185\u89d2\u548c\uff08\u63d0\u9ad8\uff09\u5de9\u56fa\u7ec3\u4e60.doc','catalog-prior-node-grade-label-conflict-advanced-practice','catalog_prior_node_requires_question_level_mapping'),
)
MARKERS={'G1_activation_diagnostic':('课前自测','前置诊断','诊断','自我检测','预习检测'),'G7_self_assessment':('自评','自我评价'),'G7_summary_only':('小结','总结','反思')}
class AllowedNodeMarkerGapAuditError(RuntimeError): pass

def _sha(path:Path)->str:return sha256(path.read_bytes()).hexdigest().upper()
def _hits(text:str,marker:str)->list[int]:
 hits=[]; start=0
 while True:
  found=text.find(marker,start)
  if found<0:return hits
  hits.append(found); start=found+len(marker)

def build_allowed_node_marker_gap_audit(*,source_root:str|Path=SOURCE_ROOT,source_specs:tuple[tuple[str,str,str],...]=SOURCE_SPECS,profiler:Callable[[str|Path],dict[str,Any]]=profile_document)->dict[str,object]:
 root=Path(source_root).resolve(); rows=[]; candidates=[]
 for relative,source_key,scope_status in source_specs:
  path=(root/relative).resolve()
  try:path.relative_to(root)
  except ValueError as exc:raise AllowedNodeMarkerGapAuditError('source_outside_root') from exc
  if path.suffix.lower()!='.doc' or not path.is_file():raise AllowedNodeMarkerGapAuditError(f'source_missing:{source_key}')
  profile=profiler(path); text=profile.get('normalized_content_text')
  if not isinstance(text,str):raise AllowedNodeMarkerGapAuditError(f'profile_text_missing:{source_key}')
  row={'source_key':source_key,'relative_path':relative,'scope_status':scope_status,'original_sha256':_sha(path),'profile_sha256':str(profile.get('normalized_content_sha256','')),'paragraph_count':profile.get('paragraph_count'),'marker_hits':{}}
  for role,markers in MARKERS.items():
   role_hits=[]
   for marker in markers:
    for character_offset in _hits(text,marker):
     hit={'role_candidate':role,'marker':marker,'character_offset':character_offset,'source_key':source_key,'scope_status':scope_status,'original_sha256':row['original_sha256']}
     role_hits.append({'marker':marker,'character_offset':character_offset});candidates.append(hit)
   row['marker_hits'][role]=role_hits
  rows.append(row)
 g1=[x for x in candidates if x['role_candidate']=='G1_activation_diagnostic' and x['scope_status']=='current_topic_exact']
 g7self=[x for x in candidates if x['role_candidate']=='G7_self_assessment' and x['scope_status']=='current_topic_exact']
 return {'schema':SCHEMA,'purpose':'read_only_source_gap_discovery_no_conversion_no_source_modification','source_count':len(rows),'sources':rows,'marker_definitions':{k:list(v) for k,v in MARKERS.items()},'candidate_counts':{role:sum(1 for x in candidates if x['role_candidate']==role) for role in MARKERS},'candidates':candidates,'current_topic_g1_candidate_count':len(g1),'current_topic_g7_self_assessment_candidate_count':len(g7self),'catalog_prior_node_requires_question_level_mapping_marker_count':sum(1 for x in candidates if x['scope_status']=='catalog_prior_node_requires_question_level_mapping'),'directory_labels_not_used_for_scope_decision':True,'marker_scan_is_not_a_role_assignment_gate':True,'student_document_generation_authorized':False,'decision':'observational_marker_scan_requires_separate_question_mapping_and_task_role_contract'}

def write_allowed_node_marker_gap_audit(report:dict[str,object],path:str|Path)->Path:
 out=Path(path).resolve();out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8');return out
