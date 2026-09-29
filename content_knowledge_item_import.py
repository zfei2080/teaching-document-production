"""Persist source-derived knowledge notes and worked examples with internal solution isolation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from content_library_extraction import _require_current_source_version
from source_content_knowledge_segmentation import ContentCandidate, ContentEvidence, segment_knowledge_and_examples
from source_library_intake import _git_revision, _insert_event, _stable_id, canonical, digest_text

SCHEMA="content-library-knowledge-item-import-v1"
IMPORTER_ID="content-library-knowledge-item-import"
IMPORTER_VERSION="1.0.0"


class ContentKnowledgeItemImportError(RuntimeError): pass


@dataclass(frozen=True)
class KnowledgeItemImportResult:
    source_version_id:str
    import_run_id:str
    status:str
    knowledge_notes:int
    worked_examples:int
    student_evidence:int
    internal_evidence:int
    change_events_created:int
    def as_dict(self)->dict[str,object]:
        return {"schema":SCHEMA,"source_version_id":self.source_version_id,"import_run_id":self.import_run_id,"status":self.status,"knowledge_notes":self.knowledge_notes,"worked_examples":self.worked_examples,"student_evidence":self.student_evidence,"internal_evidence":self.internal_evidence,"change_events_created":self.change_events_created}


def _extracted_blocks(connection:sqlite3.Connection,source_version_id:str)->tuple[str,list[dict[str,object]]]:
    connection.row_factory=sqlite3.Row
    row=connection.execute("""select er.id from content_extraction_runs er join content_import_runs ir on ir.id=er.import_run_id where er.source_version_id=? and ir.importer_id='content-library-word-extraction' order by er.created_at desc,er.id desc limit 1""",(source_version_id,)).fetchone()
    if row is None: raise ContentKnowledgeItemImportError("word_source_blocks_not_extracted")
    blocks=[{"id":r["id"],"ordinal":r["ordinal"],"raw_text":r["raw_text"]} for r in connection.execute("select id,ordinal,raw_text from source_content_blocks where extraction_run_id=? order by ordinal",(row["id"],)).fetchall()]
    if not blocks: raise ContentKnowledgeItemImportError("word_source_blocks_missing")
    return row["id"],blocks


def _run_id(extraction_run_id:str)->str:
    return _stable_id("content-import-run",{"mode":"extract","extraction_run_id":extraction_run_id,"importer_id":IMPORTER_ID,"importer_version":IMPORTER_VERSION})


def _existing(connection:sqlite3.Connection,source_version_id:str,extraction_run_id:str)->KnowledgeItemImportResult|None:
    run_id=_run_id(extraction_run_id)
    if connection.execute("select 1 from content_import_runs where id=?",(run_id,)).fetchone() is None:return None
    grouped=dict(connection.execute("select item_kind,count(*) from content_items where source_version_id=? group by item_kind",(source_version_id,)).fetchall())
    evidence=dict(connection.execute("select visibility,count(*) from content_item_evidence e join content_items i on i.id=e.content_item_id where i.source_version_id=? group by visibility",(source_version_id,)).fetchall())
    return KnowledgeItemImportResult(source_version_id,run_id,"already_imported",grouped.get("knowledge_note",0),grouped.get("worked_example",0),evidence.get("student",0),evidence.get("internal",0),0)


def _create_run(connection:sqlite3.Connection,*,source_version_id:str,extraction_run_id:str,workspace:Path,actor:str)->tuple[str,str,int]:
    run_id=_run_id(extraction_run_id); selection={"schema":SCHEMA,"source_version_id":source_version_id,"extraction_run_id":extraction_run_id}; selection_hash=digest_text(canonical(selection)); tx=_stable_id("transaction",{"run_id":run_id,"operation":"knowledge_item_import"}); rev=_git_revision(workspace)
    event,created=_insert_event(connection,event_type="import_run_started",entity_type="content_import_runs",entity_id=run_id,operation="create",reason="create explicit source-derived knowledge item import",actor=actor,import_run_id=None,transaction_id=tx,after_state=selection,after_hash=selection_hash,git_revision=rev,tool_id=IMPORTER_ID,tool_version=IMPORTER_VERSION)
    connection.execute("insert into content_import_runs(id,mode,selection_json,selection_sha256,importer_id,importer_version,created_change_event_id) values(?,?,?,?,?,?,?)",(run_id,"extract",canonical(selection),selection_hash,IMPORTER_ID,IMPORTER_VERSION,event))
    state_id=f"{run_id}:started"; state_event,state_created=_insert_event(connection,event_type="import_run_started",entity_type="content_import_run_events",entity_id=state_id,operation="status_change",reason="start source-derived knowledge item import",actor=actor,import_run_id=run_id,transaction_id=tx,after_state={"status":"started"},git_revision=rev,tool_id=IMPORTER_ID,tool_version=IMPORTER_VERSION)
    connection.execute("insert into content_import_run_events(id,import_run_id,status,totals_json,change_event_id) values(?,?,?,?,?)",(state_id,run_id,"started","{}",state_event))
    return run_id,tx,int(created)+int(state_created)


def _evidence_insert(connection:sqlite3.Connection,*,item_id:str,field_name:str,visibility:str,evidence:ContentEvidence,run_id:str,tx:str,rev:str|None,actor:str)->int:
    if evidence.end<=evidence.start: raise ContentKnowledgeItemImportError("content_evidence_range_invalid")
    h=digest_text(evidence.text); eid=_stable_id("content-item-evidence",{"item":item_id,"field":field_name,"visibility":visibility,"block":evidence.block_id,"range":[evidence.start,evidence.end],"hash":h})
    event,created=_insert_event(connection,event_type="content_created",entity_type="content_item_evidence",entity_id=eid,operation="create",reason="bind source-derived knowledge field to exact source range",actor=actor,import_run_id=run_id,transaction_id=tx,after_state={"field_name":field_name,"visibility":visibility,"source_block_id":evidence.block_id,"character_range":[evidence.start,evidence.end]},after_hash=h,git_revision=rev,tool_id=IMPORTER_ID,tool_version=IMPORTER_VERSION)
    connection.execute("insert into content_item_evidence(id,content_item_id,field_name,visibility,source_block_id,character_range_json,evidence_text,evidence_sha256,created_change_event_id) values(?,?,?,?,?,?,?,?,?)",(eid,item_id,field_name,visibility,evidence.block_id,canonical([evidence.start,evidence.end]),evidence.text,h,event))
    return int(created)


def import_knowledge_items(connection:sqlite3.Connection,*,source_version_id:str,source_root:str|Path,workspace:str|Path,actor:str="codex")->KnowledgeItemImportResult:
    root=Path(source_root).resolve(strict=True); work=Path(workspace).resolve(strict=True); connection.execute("pragma foreign_keys=on")
    _require_current_source_version(connection,source_version_id=source_version_id,source_root=root)
    extraction_run_id,blocks=_extracted_blocks(connection,source_version_id)
    prior=_existing(connection,source_version_id,extraction_run_id)
    if prior is not None:return prior
    candidates=segment_knowledge_and_examples(blocks)
    events=student=internal=0; notes=examples=0
    with connection:
        run_id,tx,created=_create_run(connection,source_version_id=source_version_id,extraction_run_id=extraction_run_id,workspace=work,actor=actor); events+=created; rev=_git_revision(work)
        for order,candidate in enumerate(candidates):
            payload={"title":candidate.title,"content_text":candidate.student_text,"source_block_ids":list(candidate.source_block_ids)}; h=digest_text(canonical(payload)); item_id=_stable_id("content-item",{"source_version_id":source_version_id,"item_kind":candidate.item_kind,"source_block_ids":candidate.source_block_ids,"content_sha256":h})
            event,created=_insert_event(connection,event_type="content_created",entity_type="content_items",entity_id=item_id,operation="create",reason="persist reusable source-derived knowledge or worked-example item",actor=actor,import_run_id=run_id,transaction_id=tx,after_state={"item_kind":candidate.item_kind,"title":candidate.title},after_hash=h,git_revision=rev,tool_id=IMPORTER_ID,tool_version=IMPORTER_VERSION); events+=int(created)
            connection.execute("insert into content_items(id,source_version_id,item_kind,student_payload_json,display_order,content_sha256,created_change_event_id) values(?,?,?,?,?,?,?)",(item_id,source_version_id,candidate.item_kind,canonical(payload),order,h,event))
            for evidence in candidate.student_evidence:
                events+=_evidence_insert(connection,item_id=item_id,field_name="content",visibility="student",evidence=evidence,run_id=run_id,tx=tx,rev=rev,actor=actor); student+=1
            for evidence in candidate.internal_evidence:
                events+=_evidence_insert(connection,item_id=item_id,field_name="solution",visibility="internal",evidence=evidence,run_id=run_id,tx=tx,rev=rev,actor=actor); internal+=1
            if candidate.item_kind=="knowledge_note":notes+=1
            else:examples+=1
        totals={"knowledge_notes":notes,"worked_examples":examples,"student_evidence":student,"internal_evidence":internal}; finish_id=f"{run_id}:completed"; finish_event,finish_created=_insert_event(connection,event_type="import_run_finished",entity_type="content_import_run_events",entity_id=finish_id,operation="status_change",reason="complete source-derived knowledge item import",actor=actor,import_run_id=run_id,transaction_id=tx,after_state={"status":"completed","totals":totals},after_hash=digest_text(canonical(totals)),git_revision=rev,tool_id=IMPORTER_ID,tool_version=IMPORTER_VERSION); events+=int(finish_created)
        connection.execute("insert into content_import_run_events(id,import_run_id,status,totals_json,change_event_id) values(?,?,?,?,?)",(finish_id,run_id,"completed",canonical(totals),finish_event))
    return KnowledgeItemImportResult(source_version_id,run_id,"imported",notes,examples,student,internal,events)
