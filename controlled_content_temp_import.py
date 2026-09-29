"""P1-2c fail-closed temporary import for P1-2b controlled content segments."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import sqlite3


SCHEMA = "p1-2c-controlled-content-manifest-v1"
IMPORTER_ID = "p1-2c-controlled-content-import"
IMPORTER_VERSION = "1.0.0"
TEXTBOOK = "bsd-math-grade8-lower-2026-spring-extsrc"
NODE = "bsd-math-8x-2026-node-01-section-02"
ALLOWED = ("bsd-math-8x-2026-node-01-section-01", NODE)
LAYERS = {"knowledge_explanation": "public_core", "consolidation_practice": "basic_reinforcement"}


class ControlledContentImportBlockedError(RuntimeError): pass


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _inside(path: Path, root: Path) -> bool:
    try: path.resolve().relative_to(root.resolve()); return True
    except ValueError: return False


def build_manifest(audit_path: str | Path, *, workspace: str | Path) -> dict[str, Any]:
    audit = Path(audit_path).resolve(); root = Path(workspace).resolve()
    payload = json.loads(audit.read_text(encoding="utf-8"))
    if payload.get("schema") != "p1-2b-controlled-content-run-v1":
        raise ControlledContentImportBlockedError("p1_2b_audit_schema_invalid")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ControlledContentImportBlockedError("p1_2b_representative_records_missing")
    keyed_records = [record for record in records if isinstance(record, dict) and "source_key" in record]
    if keyed_records:
        if len(keyed_records) != len(records):
            raise ControlledContentImportBlockedError("p1_2b_source_key_shape_mixed")
        expected = {
            "basic-knowledge-explanation": "knowledge_explanation",
            "basic-consolidation-practice": "consolidation_practice",
        }
        by_key: dict[str, dict[str, Any]] = {}
        for record in keyed_records:
            source_key = record.get("source_key")
            if not isinstance(source_key, str) or source_key in by_key:
                raise ControlledContentImportBlockedError("p1_2b_representative_source_key_invalid")
            by_key[source_key] = record
        if not expected.keys() <= by_key.keys() or any(
            by_key[key].get("content_type") != content_type
            for key, content_type in expected.items()
        ):
            raise ControlledContentImportBlockedError("p1_2b_representative_records_missing")
        representative_records = [by_key[key] for key in sorted(expected)]
    else:
        if len(records) != 2:
            raise ControlledContentImportBlockedError("p1_2b_representative_records_missing")
        representative_records = records
    sources=[]; segments=[]
    for record in representative_records:
        if not isinstance(record, dict) or record.get("status") != "candidate_content_only":
            raise ControlledContentImportBlockedError("p1_2b_record_not_candidate_only")
        content_type=record.get("content_type")
        if content_type not in LAYERS: raise ControlledContentImportBlockedError("unsupported_content_type")
        m=record.get("manifest") or {}; profiles=m.get("profiles") or {}
        if profiles.get("fidelity_status") != "passed" or profiles.get("conversion_differences"):
            raise ControlledContentImportBlockedError("conversion_fidelity_not_pass")
        source=m.get("source") or {}; archive=m.get("archive") or {}; conv=(m.get("conversion") or {}).get("converted") or {}
        paths=[Path(source.get("path", "")),Path(archive.get("path", "")),Path(conv.get("path", ""))]
        expected=[source.get("sha256"),archive.get("sha256"),conv.get("sha256")]
        if not all(p.is_file() and _inside(p,root) for p in paths):
            raise ControlledContentImportBlockedError("controlled_content_path_invalid")
        if any(not isinstance(h,str) or digest(p)!=h for p,h in zip(paths,expected)):
            raise ControlledContentImportBlockedError("controlled_content_hash_mismatch")
        if source["sha256"] != archive["sha256"]: raise ControlledContentImportBlockedError("original_archive_hash_mismatch")
        profile=profiles.get("source") or {}; text=profile.get("normalized_content_text")
        if not isinstance(text,str): raise ControlledContentImportBlockedError("source_text_missing")
        valid=m.get("segments") or []
        if len(valid)!=1: raise ControlledContentImportBlockedError("expected_one_unisolated_segment")
        seg=valid[0]
        if seg.get("content_type")!=content_type or seg.get("scope_status")!="candidate_only":
            raise ControlledContentImportBlockedError("segment_status_invalid")
        if seg.get("textbook_id")!=TEXTBOOK or seg.get("curriculum_node_id")!=NODE or tuple(seg.get("required_node_ids",()))!=ALLOWED:
            raise ControlledContentImportBlockedError("segment_contract_invalid")
        rng=seg.get("source_character_range")
        if not (isinstance(rng,list) and len(rng)==2 and all(isinstance(x,int) for x in rng) and 0<=rng[0]<rng[1]<=len(text)):
            raise ControlledContentImportBlockedError("segment_range_invalid")
        snippet=text[rng[0]:rng[1]]
        if digest_bytes(snippet.encode("utf-8")) != seg.get("normalized_text_sha256"):
            raise ControlledContentImportBlockedError("segment_text_hash_mismatch")
        for q in m.get("quarantined_segments") or []:
            qr=q.get("source_character_range")
            if isinstance(qr,list) and len(qr)==2 and not (rng[1]<=qr[0] or rng[0]>=qr[1]):
                raise ControlledContentImportBlockedError("segment_overlaps_quarantine")
        source_id="ccs:"+source["sha256"][:24]
        sources.append({"id":source_id,"original":source,"archive":archive,"converted":conv,"converter_fingerprint":(m.get("conversion") or {}).get("converter",{}).get("fingerprint"),"source_profile_sha256":digest_bytes(canonical(profile).encode("utf-8"))})
        segments.append({"id":"ccseg:"+digest_bytes(canonical({"text":seg["normalized_text_sha256"],"textbook":TEXTBOOK,"node":NODE,"allowed":ALLOWED}).encode("utf-8"))[:24],"source_id":source_id,"segment_id":seg["segment_id"],"content_type":content_type,"layer":LAYERS[content_type],"textbook_id":TEXTBOOK,"curriculum_node_id":NODE,"allowed_node_ids":list(ALLOWED),"required_knowledge":["\u7b49\u8170\u4e09\u89d2\u5f62"],"source_character_range":rng,"normalized_text_sha256":seg["normalized_text_sha256"]})
    return {"schema":SCHEMA,"p1_2b_audit_path":str(audit),"p1_2b_audit_sha256":digest(audit),"sources":sources,"segments":segments}

def digest_bytes(value: bytes) -> str: return sha256(value).hexdigest().upper()

def import_manifest(connection: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("schema")!=SCHEMA: raise ControlledContentImportBlockedError("manifest_schema_invalid")
    mh=digest_bytes(canonical(manifest).encode("utf-8")); run_id="ccir:"+mh[:24]
    if connection.execute("SELECT 1 FROM controlled_content_import_runs WHERE manifest_sha256=?",(mh,)).fetchone():
        raise ControlledContentImportBlockedError("identical_manifest_already_imported")
    with connection:
        connection.execute("INSERT INTO controlled_content_import_runs(id,manifest_sha256,p1_2b_audit_sha256,importer_id,importer_version,status) VALUES(?,?,?,?,?,'validated')",(run_id,mh,manifest["p1_2b_audit_sha256"],IMPORTER_ID,IMPORTER_VERSION))
        for s in manifest["sources"]:
            existing=connection.execute("SELECT original_sha256,archive_sha256,converted_sha256,converter_fingerprint FROM controlled_content_sources WHERE id=?",(s["id"],)).fetchone()
            expected=(s["original"]["sha256"],s["archive"]["sha256"],s["converted"]["sha256"],s["converter_fingerprint"])
            if existing is None:
                connection.execute("INSERT INTO controlled_content_sources VALUES(?,?,?,?,?,?,?,?,?,?,?)",(s["id"],run_id,s["original"]["path"],s["original"]["sha256"],s["archive"]["path"],s["archive"]["sha256"],s["converted"]["path"],s["converted"]["sha256"],s["converter_fingerprint"],"passed",s["source_profile_sha256"]))
            elif tuple(existing) != expected:
                raise ControlledContentImportBlockedError("existing_source_evidence_conflict")
        for x in manifest["segments"]:
            connection.execute("INSERT INTO controlled_content_segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",(x["id"],x["source_id"],x["segment_id"],x["content_type"],x["layer"],x["textbook_id"],x["curriculum_node_id"],canonical(x["allowed_node_ids"]),canonical(x["required_knowledge"]),canonical(x["source_character_range"]),x["normalized_text_sha256"],"candidate_only"))
    return {"run_id":run_id,"manifest_sha256":mh,"sources":len(manifest["sources"]),"segments":len(manifest["segments"]),"status":"validated"}


def invalidate_changed_content_sources(connection: sqlite3.Connection) -> dict[str, int]:
    """Fail closed when any formally imported source triple changes on disk.

    Only controlled-content rows may be changed; questions and delivery tables are
    never touched. The caller must use this before content selection or delivery.
    """
    rows=connection.execute("SELECT id,original_path,original_sha256,archive_path,archive_sha256,converted_path,converted_sha256 FROM controlled_content_sources").fetchall()
    invalidated=0
    with connection:
        for source_id,op,oh,ap,ah,cp,ch in rows:
            triples=((Path(op),oh),(Path(ap),ah),(Path(cp),ch))
            mismatch=next((str(path) for path,expected in triples if not path.is_file() or digest(path)!=expected),None)
            if mismatch:
                cursor=connection.execute("UPDATE controlled_content_segments SET invalidated_at=datetime('now'), invalidation_reason='content_source_hash_mismatch' WHERE source_id=? AND invalidated_at IS NULL",(source_id,))
                invalidated+=cursor.rowcount
    return {"invalidated_segments":invalidated}
