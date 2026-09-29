"""Read-only P1-4b class-progress scope audit.

A class's actual curriculum progress is the authority for question scope. Source
folder labels are intentionally absent from this decision: each candidate still
needs approved question-level textbook, knowledge and dependency evidence.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from typing import Any
import json, sqlite3

from curriculum_progress_scope import resolve_trusted_progress_scope

ROOT=Path(__file__).resolve().parent
DATABASE=ROOT/'data'/'dev'/'teaching_docs_dev.db'
DEFAULT_REPORT=ROOT/'output'/'audits'/'p1-4b_class_progress_scope_audit.json'
TEXTBOOK_ID='bsd-math-grade8-lower-2026-spring-extsrc'
PROGRESS_LABEL='第二章'
TARGET_TOPIC_NODE_ID='bsd-math-8x-2026-node-01-section-02'
SCHEMA='p1-4b-class-progress-scope-audit-v1'

class ClassProgressScopeAuditError(RuntimeError): pass

def _sha(path:Path)->str:return sha256(path.read_bytes()).hexdigest().upper()

def build_class_progress_scope_audit(database_path:str|Path=DATABASE)->dict[str,Any]:
 database=Path(database_path).resolve()
 if not database.is_file():raise ClassProgressScopeAuditError('database_missing')
 before=_sha(database)
 connection=sqlite3.connect(f'file:{database.as_posix()}?mode=ro',uri=True)
 try:
  connection.execute('PRAGMA query_only=ON')
  scope=resolve_trusted_progress_scope(connection,textbook_reference=TEXTBOOK_ID,progress_label=PROGRESS_LABEL)
 finally:connection.close()
 after=_sha(database)
 if before!=after:raise ClassProgressScopeAuditError('read_only_scope_audit_changed_database')
 if scope.status!='resolved':raise ClassProgressScopeAuditError('progress_scope_not_resolved:'+','.join(scope.reasons))
 target_in_scope=TARGET_TOPIC_NODE_ID in set(scope.allowed_node_ids)
 if not target_in_scope:raise ClassProgressScopeAuditError('target_topic_outside_declared_class_progress')
 return {'schema':SCHEMA,'purpose':'read_only_progress_boundary_for_source_question_selection','class_request':{'textbook_id':TEXTBOOK_ID,'progress_label':PROGRESS_LABEL,'target_topic_node_id':TARGET_TOPIC_NODE_ID},'resolved_progress':{'current_node_id':scope.current_node_id,'allowed_node_ids':list(scope.allowed_node_ids),'outside_node_ids':list(scope.outside_node_ids),'catalog_version':scope.catalog_version},'target_topic_within_progress':target_in_scope,'source_directory_labels_used_for_scope_decision':False,'question_selection_requirements':['question_origin_is_local_source_file','approved_question_textbook_mapping','required_knowledge_and_dependency_nodes_within_allowed_progress','same_class_delivery_history_exclusion'],'student_document_generation_authorized':False,'decision':'progress_boundary_resolved_target_topic_in_scope_pending_question_level_supply_and_role_plan','database':{'path':str(database),'sha256_before':before,'sha256_after':after,'unchanged':True}}

def write_class_progress_scope_audit(report:dict[str,Any],path:str|Path)->Path:
 output=Path(path).resolve();output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8');return output
