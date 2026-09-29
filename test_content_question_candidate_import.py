"""Tests for source-derived question candidate database import."""
from __future__ import annotations

from hashlib import sha256
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apply_schema_v2_30 import apply as apply_v30
from apply_schema_v2_34 import apply as apply_v34
from apply_schema_v2_35 import apply as apply_v35
from apply_schema_v2_36 import apply as apply_v36
from apply_schema_v2_37 import apply as apply_v37
from apply_schema_v2_38 import apply as apply_v38
from apply_schema_v2_39 import apply as apply_v39
from content_library_extraction import import_current_word_source
from content_question_candidate_import import _source_question_keys, import_question_candidates
from source_library_intake import digest_file, register_sources

ROOT = Path(__file__).parent
BASELINE = ROOT / "data" / "dev" / "backups" / "p1-2f" / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db"


def text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest().upper()


class ContentQuestionCandidateImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir=tempfile.TemporaryDirectory()
        self.root=Path(self.tempdir.name)
        self.database=self.root/"content.db"
        self.source_root=self.root/"sources"
        self.source_root.mkdir()
        self.source=self.source_root/"lesson.doc"
        self.source.write_bytes(b"trusted source")
        shutil.copy2(BASELINE,self.database)
        apply_v30(self.database); apply_v34(self.database); apply_v35(self.database); apply_v36(self.database); apply_v37(self.database); apply_v38(self.database); apply_v39(self.database)
        self.connection=sqlite3.connect(self.database)
        self.connection.execute("PRAGMA foreign_keys=ON")
        register_sources(self.connection,source_root=self.source_root,mode="baseline",explicit_paths=None,workspace=ROOT,actor="test")
        self.version=self.connection.execute("select id from content_source_versions where original_relative_path='lesson.doc'").fetchone()[0]
        with patch("content_library_extraction.extract_document", return_value=self.profile()):
            import_current_word_source(self.connection,source_version_id=self.version,source_root=self.source_root,workspace=ROOT,manifest_root=self.root/"manifests",actor="test")

    def tearDown(self) -> None:
        self.connection.close(); self.tempdir.cleanup()

    def profile(self) -> dict:
        texts=[
            "\u4e00.\u9009\u62e9\u9898", "1. \u539f\u9898\u4e00", "A. \u7532 B. \u4e59", "2. \u539f\u9898\u4e8c",
            "\u3010\u7b54\u6848\u4e0e\u89e3\u6790\u3011", "1. \u3010\u7b54\u6848\u3011B;\u3010\u89e3\u6790\u3011\u539f\u89e3\u6790", "2. \u3010\u89e3\u6790\u3011\u7b2c\u4e8c\u9898\u89e3\u6790",
        ]
        return {
            "schema":"word-com-content-extraction-v1", "file_type":"doc", "source_sha256":digest_file(self.source),
            "normalized_content_sha256":text_hash("".join(texts)), "engine_id":"Microsoft Word COM", "engine_version":"test",
            "blocks":[{"ordinal":i,"kind":"paragraph","locator":{"paragraph_index":i+1},"raw_text":text,"normalized_text":text,"raw_sha256":text_hash(text),"normalized_sha256":text_hash(text)} for i,text in enumerate(texts)],
            "assets":[],
        }

    def test_section_qualified_source_keys_preserve_restarted_raw_numbers(self) -> None:
        from types import SimpleNamespace
        candidates = [
            SimpleNamespace(source_question_no="1", question_type="???"),
            SimpleNamespace(source_question_no="1", question_type="???"),
            SimpleNamespace(source_question_no="2", question_type="???"),
        ]
        self.assertEqual(_source_question_keys(candidates), ["???:1", "???:1", "2"])

    def test_creates_student_questions_and_separate_internal_answers(self) -> None:
        result=import_question_candidates(self.connection,source_version_id=self.version,source_root=self.source_root,workspace=ROOT,actor="test")
        self.assertEqual(result.status,"imported")
        self.assertEqual(result.questions,2)
        self.assertEqual(result.content_items,2)
        self.assertEqual(result.internal_answer_evidence,1)
        self.assertEqual(result.internal_analysis_evidence,2)
        self.assertEqual(result.source_asset_evidence,0)
        rows=self.connection.execute("select answer,analysis from questions where source_document_id=(select source_document_id from content_source_versions where id=?) order by source_question_no",(self.version,)).fetchall()
        self.assertEqual([tuple(row) for row in rows],[(None,None),(None,None)])
        self.assertEqual(self.connection.execute("select count(*) from content_items where source_version_id=?",(self.version,)).fetchone()[0],2)
        self.assertEqual(self.connection.execute("select count(*) from content_item_evidence where visibility='student'").fetchone()[0],4)
        self.assertEqual(self.connection.execute("select count(*) from question_internal_evidence").fetchone()[0],3)
        self.assertEqual(self.connection.execute("select count(*) from content_item_math_validation_evidence").fetchone()[0],2)
        self.assertEqual(self.connection.execute("select count(*) from content_item_question_eligibility where content_eligible=1").fetchone()[0],0)
        self.assertEqual(self.connection.execute("select count(*) from questions where source_document_id=(select source_document_id from content_source_versions where id=?) and stage is not null", (self.version,)).fetchone()[0],0)
        tools=self.connection.execute("select distinct tool_id,tool_version from content_change_ledger where import_run_id=?",(result.import_run_id,)).fetchall()
        self.assertEqual([tuple(row) for row in tools],[("content-library-question-candidate-import","2.0.0")])
        duplicate=import_question_candidates(self.connection,source_version_id=self.version,source_root=self.source_root,workspace=ROOT,actor="test")
        self.assertEqual(duplicate.status,"already_segmented")
        self.assertEqual(self.connection.execute("select count(*) from content_items where source_version_id=?",(self.version,)).fetchone()[0],2)


    def asset_only_profile(self, *, asset_offsets: list[int]) -> dict:
        texts = [
            "\u4e00.\u9009\u62e9\u9898", "1. \u770b\u56fe\u9009\u62e9", "A\uff0e \x01 B\uff0e \x01 C\uff0e \x01 D\uff0e \x01",
            "\u3010\u7b54\u6848\u4e0e\u89e3\u6790\u3011", "1. \u3010\u7b54\u6848\u3011A;\u3010\u89e3\u6790\u3011\u539f\u89e3\u6790",
        ]
        starts = [0, 20, 100, 140, 160]
        profile = {
            "schema": "word-com-content-extraction-v1", "file_type": "doc", "source_sha256": "",
            "normalized_content_sha256": text_hash("".join(texts)), "engine_id": "Microsoft Word COM", "engine_version": "test",
            "blocks": [
                {"ordinal": i, "kind": "paragraph", "locator": {"paragraph_index": i + 1, "range_start": starts[i], "range_end": starts[i] + len(text)},
                 "raw_text": text, "normalized_text": text, "raw_sha256": text_hash(text), "normalized_sha256": text_hash(text)}
                for i, text in enumerate(texts)
            ],
            "assets": [
                {"ordinal": i, "kind": "ole_object", "locator": {"inline_shape_index": i + 1, "range_start": 100 + offset, "range_end": 101 + offset}}
                for i, offset in enumerate(asset_offsets)
            ],
        }
        return profile

    def test_asset_only_options_bind_to_exact_source_assets_without_text_fabrication(self) -> None:
        asset_source = self.source_root / "asset-options.doc"
        asset_source.write_bytes(b"asset option source")
        register_sources(self.connection, source_root=self.source_root, mode="delta", explicit_paths=["asset-options.doc"], workspace=ROOT, actor="test")
        version = self.connection.execute("select id from content_source_versions where original_relative_path='asset-options.doc'").fetchone()[0]
        profile = self.asset_only_profile(asset_offsets=[3, 8, 13, 18])
        profile["source_sha256"] = digest_file(asset_source)
        with patch("content_library_extraction.extract_document", return_value=profile):
            import_current_word_source(self.connection, source_version_id=version, source_root=self.source_root, workspace=ROOT, manifest_root=self.root / "asset-manifests", actor="test")
        result = import_question_candidates(self.connection, source_version_id=version, source_root=self.source_root, workspace=ROOT, actor="test")
        self.assertEqual(result.status, "imported")
        self.assertEqual((result.questions, result.source_asset_evidence), (1, 4))
        question_id, options_json, answer, analysis = self.connection.execute(
            "select id,options_json,answer,analysis from questions where source_document_id=(select source_document_id from content_source_versions where id=?)", (version,)
        ).fetchone()
        options = __import__("json").loads(options_json)
        self.assertEqual([option["label"] for option in options], ["A", "B", "C", "D"])
        self.assertTrue(all(option["text"] is None and option["asset_only"] is True for option in options))
        self.assertTrue(all(len(option["source_asset_ids"]) == 1 for option in options))
        self.assertEqual((answer, analysis), (None, None))
        rows = self.connection.execute(
            "select option_label,position,source_content_asset_id,source_block_id from question_source_asset_evidence where question_id=? order by option_label", (question_id,)
        ).fetchall()
        self.assertEqual([tuple(row[:2]) for row in rows], [("A", 0), ("B", 0), ("C", 0), ("D", 0)])
        self.assertEqual({row[2] for row in rows}, {asset_id for option in options for asset_id in option["source_asset_ids"]})
        self.assertTrue(all(row[3] for row in rows))
        duplicate = import_question_candidates(self.connection, source_version_id=version, source_root=self.source_root, workspace=ROOT, actor="test")
        self.assertEqual((duplicate.status, duplicate.source_asset_evidence), ("already_segmented", 4))

    def test_expanded_inline_asset_ranges_bind_by_exact_block_count_and_word_order(self) -> None:
        asset_source = self.source_root / "asset-expanded-ranges.doc"
        asset_source.write_bytes(b"expanded inline object ranges")
        register_sources(self.connection, source_root=self.source_root, mode="delta", explicit_paths=["asset-expanded-ranges.doc"], workspace=ROOT, actor="test")
        version = self.connection.execute("select id from content_source_versions where original_relative_path='asset-expanded-ranges.doc'").fetchone()[0]
        profile = self.asset_only_profile(asset_offsets=[3, 8, 13, 18])
        profile["source_sha256"] = digest_file(asset_source)
        profile["blocks"][2]["locator"]["range_end"] = 600
        profile["assets"] = [
            {"ordinal": index, "kind": "ole_object", "locator": {"inline_shape_index": index + 1, "range_start": start, "range_end": end}}
            for index, (start, end) in enumerate(((102, 180), (200, 280), (300, 380), (400, 480)))
        ]
        with patch("content_library_extraction.extract_document", return_value=profile):
            import_current_word_source(self.connection, source_version_id=version, source_root=self.source_root, workspace=ROOT, manifest_root=self.root / "asset-expanded-manifests", actor="test")
        result = import_question_candidates(self.connection, source_version_id=version, source_root=self.source_root, workspace=ROOT, actor="test")
        self.assertEqual((result.status, result.questions, result.source_asset_evidence), ("imported", 1, 4))
        modes = self.connection.execute(
            "select after_state_json from content_change_ledger where entity_type='question_source_asset_evidence' order by id"
        ).fetchall()
        self.assertEqual({__import__("json").loads(row[0])["association_mode"] for row in modes}, {"block_range_order"})


    def test_asset_only_options_with_asset_count_mismatch_remain_deferred_and_write_nothing(self) -> None:
        asset_source = self.source_root / "asset-mismatch.doc"
        asset_source.write_bytes(b"asset mismatch")
        register_sources(self.connection, source_root=self.source_root, mode="delta", explicit_paths=["asset-mismatch.doc"], workspace=ROOT, actor="test")
        version = self.connection.execute("select id from content_source_versions where original_relative_path='asset-mismatch.doc'").fetchone()[0]
        profile = self.asset_only_profile(asset_offsets=[3, 8, 13])
        profile["source_sha256"] = digest_file(asset_source)
        with patch("content_library_extraction.extract_document", return_value=profile):
            import_current_word_source(self.connection, source_version_id=version, source_root=self.source_root, workspace=ROOT, manifest_root=self.root / "asset-mismatch-manifests", actor="test")
        result = import_question_candidates(self.connection, source_version_id=version, source_root=self.source_root, workspace=ROOT, actor="test")
        self.assertEqual(result.status, "deferred")
        self.assertEqual(self.connection.execute("select count(*) from content_items where source_version_id=?", (version,)).fetchone()[0], 0)
        self.assertEqual(self.connection.execute("select count(*) from question_source_asset_evidence").fetchone()[0], 0)
        reason = self.connection.execute(
            "select reason from content_source_lifecycle_events where source_version_id=? and status='deferred'", (version,)
        ).fetchone()[0]
        self.assertIn("asset_only_option_asset_count_mismatch", reason)


    def test_asset_only_options_with_out_of_range_asset_remain_deferred_and_write_nothing(self) -> None:
        asset_source = self.source_root / "asset-out-of-range.doc"
        asset_source.write_bytes(b"asset outside option range")
        register_sources(self.connection, source_root=self.source_root, mode="delta", explicit_paths=["asset-out-of-range.doc"], workspace=ROOT, actor="test")
        version = self.connection.execute("select id from content_source_versions where original_relative_path='asset-out-of-range.doc'").fetchone()[0]
        profile = self.asset_only_profile(asset_offsets=[3, 8, 13, 50])
        profile["source_sha256"] = digest_file(asset_source)
        with patch("content_library_extraction.extract_document", return_value=profile):
            import_current_word_source(self.connection, source_version_id=version, source_root=self.source_root, workspace=ROOT, manifest_root=self.root / "asset-out-of-range-manifests", actor="test")
        result = import_question_candidates(self.connection, source_version_id=version, source_root=self.source_root, workspace=ROOT, actor="test")
        self.assertEqual(result.status, "deferred")
        self.assertEqual(self.connection.execute("select count(*) from content_items where source_version_id=?", (version,)).fetchone()[0], 0)
        self.assertEqual(self.connection.execute("select count(*) from question_source_asset_evidence").fetchone()[0], 0)
        reason = self.connection.execute(
            "select reason from content_source_lifecycle_events where source_version_id=? and status='deferred'", (version,)
        ).fetchone()[0]
        self.assertIn("asset_only_option_asset_count_mismatch", reason)


    def test_asset_only_unreadable_options_are_deferred_and_recorded_without_question_write(self) -> None:
        asset_source=self.source_root/"asset-only.doc"
        asset_source.write_bytes(b"asset only")
        register_sources(self.connection,source_root=self.source_root,mode="delta",explicit_paths=["asset-only.doc"],workspace=ROOT,actor="test")
        version=self.connection.execute("select id from content_source_versions where original_relative_path='asset-only.doc'").fetchone()[0]
        profile = self.profile()
        profile["source_sha256"] = digest_file(asset_source)
        profile["blocks"][2]["raw_text"] = "A\u0141\u00ae \x01 B\u0141\u00ae \x01 C\u0141\u00ae \x01 D\u0141\u00ae \x01"
        profile["blocks"][2]["normalized_text"] = profile["blocks"][2]["raw_text"]
        profile["blocks"][2]["raw_sha256"] = text_hash(profile["blocks"][2]["raw_text"])
        profile["blocks"][2]["normalized_sha256"] = text_hash(profile["blocks"][2]["raw_text"])
        with patch("content_library_extraction.extract_document", return_value=profile):
            import_current_word_source(self.connection,source_version_id=version,source_root=self.source_root,workspace=ROOT,manifest_root=self.root/"deferred-manifests",actor="test")
        result=import_question_candidates(self.connection,source_version_id=version,source_root=self.source_root,workspace=ROOT,actor="test")
        self.assertEqual(result.status,"deferred")
        self.assertEqual(self.connection.execute("select count(*) from content_items where source_version_id=?",(version,)).fetchone()[0],0)
        self.assertEqual(self.connection.execute("select count(*) from questions where source_document_id=(select source_document_id from content_source_versions where id=?)",(version,)).fetchone()[0],0)
        self.assertEqual(self.connection.execute("select count(*) from content_source_lifecycle_events where source_version_id=? and status='deferred'",(version,)).fetchone()[0],1)


if __name__=='__main__': unittest.main()
