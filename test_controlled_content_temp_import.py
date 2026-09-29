from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from controlled_content_temp_import import ControlledContentImportBlockedError, build_manifest, import_manifest, invalidate_changed_content_sources


class P12CTemporaryContentImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir=tempfile.TemporaryDirectory(); self.root=Path(self.tempdir.name)
        self.source=self.root/'source.doc'; self.archive=self.root/'archive.doc'; self.converted=self.root/'converted.docx'
        self.practice_source=self.root/'practice.doc'; self.practice_archive=self.root/'practice-archive.doc'; self.practice_converted=self.root/'practice.docx'
        for path,content in ((self.source,b'original'),(self.archive,b'original'),(self.converted,b'converted'),(self.practice_source,b'practice-original'),(self.practice_archive,b'practice-original'),(self.practice_converted,b'practice-converted')): path.write_bytes(content)
        self.text='等腰三角形基础内容'
        self.audit=self.root/'audit.json'; self._write_audit()

    def tearDown(self)->None: self.tempdir.cleanup()
    def _digest(self,p): return sha256(p.read_bytes()).hexdigest().upper()
    def _write_audit(self, *, segment_hash=None, quarantine_range=None):
        h=sha256(self.text.encode('utf-8')).hexdigest().upper() if segment_hash is None else segment_hash
        record={"status":"candidate_content_only","content_type":"knowledge_explanation","manifest":{"source":{"path":str(self.source),"sha256":self._digest(self.source)},"archive":{"path":str(self.archive),"sha256":self._digest(self.archive)},"conversion":{"converted":{"path":str(self.converted),"sha256":self._digest(self.converted)},"converter":{"fingerprint":"A"*64}},"profiles":{"fidelity_status":"passed","conversion_differences":[],"source":{"normalized_content_text":self.text}},"segments":[{"segment_id":"s1","content_type":"knowledge_explanation","scope_status":"candidate_only","textbook_id":"bsd-math-grade8-lower-2026-spring-extsrc","curriculum_node_id":"bsd-math-8x-2026-node-01-section-02","required_node_ids":["bsd-math-8x-2026-node-01-section-01","bsd-math-8x-2026-node-01-section-02"],"source_character_range":[0,len(self.text)],"normalized_text_sha256":h}],"quarantined_segments":[]}}
        # A second supported record is needed for the representative P1-2c chain.
        practice=json.loads(json.dumps(record)); practice['content_type']='consolidation_practice'; practice['manifest']['segments'][0]['segment_id']='s2'; practice['manifest']['segments'][0]['content_type']='consolidation_practice'
        practice['manifest']['source']={"path":str(self.practice_source),"sha256":self._digest(self.practice_source)}
        practice['manifest']['archive']={"path":str(self.practice_archive),"sha256":self._digest(self.practice_archive)}
        practice['manifest']['conversion']['converted']={"path":str(self.practice_converted),"sha256":self._digest(self.practice_converted)}
        practice_text='?????????'
        practice['manifest']['profiles']['source']['normalized_content_text']=practice_text
        practice['manifest']['segments'][0]['source_character_range']=[0,len(practice_text)]
        practice['manifest']['segments'][0]['normalized_text_sha256']=sha256(practice_text.encode('utf-8')).hexdigest().upper()
        if quarantine_range is not None: record['manifest']['quarantined_segments']=[{"source_character_range":quarantine_range}]
        self.audit.write_text(json.dumps({"schema":"p1-2b-controlled-content-run-v1","records":[record,practice]}),encoding='utf-8')

    def test_imports_only_hashed_unisolated_segments_into_temporary_schema(self):
        manifest=build_manifest(self.audit,workspace=self.root)
        conn=sqlite3.connect(':memory:')
        try:
            conn.executescript((Path(__file__).resolve().parent/'schema_p1_2c_temporary.sql').read_text(encoding='utf-8'))
            result=import_manifest(conn,manifest)
            self.assertEqual(result['segments'],2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM controlled_content_segments').fetchone()[0],2)
            with self.assertRaisesRegex(ControlledContentImportBlockedError,'identical_manifest_already_imported'):
                import_manifest(conn,manifest)
        finally: conn.close()

    def test_source_change_invalidates_only_controlled_content_segments(self):
        manifest=build_manifest(self.audit,workspace=self.root)
        conn=sqlite3.connect(':memory:')
        try:
            conn.executescript((Path(__file__).resolve().parent/'schema_p1_2c_temporary.sql').read_text(encoding='utf-8'))
            import_manifest(conn,manifest)
            self.source.write_bytes(b'tampered-original')
            result=invalidate_changed_content_sources(conn)
            self.assertEqual(result['invalidated_segments'],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM controlled_content_segments WHERE invalidated_at IS NULL').fetchone()[0],1)
        finally: conn.close()


    def test_keyed_audit_selects_only_current_basic_representatives(self):
        payload=json.loads(self.audit.read_text(encoding="utf-8"))
        records=payload["records"]
        records[0]["source_key"]="basic-knowledge-explanation"
        records[1]["source_key"]="basic-consolidation-practice"
        advanced_knowledge=json.loads(json.dumps(records[0])); advanced_knowledge["source_key"]="advanced-knowledge-explanation"
        advanced_practice=json.loads(json.dumps(records[1])); advanced_practice["source_key"]="advanced-consolidation-practice"
        payload["records"] += [advanced_knowledge,advanced_practice]
        self.audit.write_text(json.dumps(payload),encoding="utf-8")
        manifest=build_manifest(self.audit,workspace=self.root)
        self.assertEqual(len(manifest["sources"]),2)
        self.assertEqual({segment["content_type"] for segment in manifest["segments"]},{"knowledge_explanation","consolidation_practice"})

    def test_text_hash_tampering_and_quarantine_overlap_are_blocked(self):
        self._write_audit(segment_hash='B'*64)
        with self.assertRaisesRegex(ControlledContentImportBlockedError,'segment_text_hash_mismatch'):
            build_manifest(self.audit,workspace=self.root)
        self._write_audit(quarantine_range=[0,2])
        with self.assertRaisesRegex(ControlledContentImportBlockedError,'segment_overlaps_quarantine'):
            build_manifest(self.audit,workspace=self.root)


if __name__=='__main__': unittest.main()
