"""Focused safety tests for ASSET-PIPELINE-PROOF-001."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from asset_pipeline_proof import (
    ALLOWED_STAGE_STATUSES, AssetPipelineProofError, ensure_trial_path, public_summary,
    render_verified_html, stage,
)


class AssetPipelineProofTests(unittest.TestCase):
    def test_production_database_cannot_be_trial_target(self) -> None:
        with self.assertRaises(AssetPipelineProofError):
            ensure_trial_path(Path("data/dev/teaching_docs_dev.db"))

    def test_trial_database_path_must_stay_under_isolated_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "trial"
            root.mkdir()
            self.assertEqual(ensure_trial_path(root / "trial.db", trial_root=root), (root / "trial.db").resolve())
            with self.assertRaises(AssetPipelineProofError):
                ensure_trial_path(Path(directory) / "outside.db", trial_root=root)

    def test_stage_conclusions_are_closed(self) -> None:
        self.assertEqual(set(ALLOWED_STAGE_STATUSES), {"保留", "未提取", "未绑定", "已丢失", "无法比较"})
        with self.assertRaises(AssetPipelineProofError):
            stage("rendered")

    def test_public_summary_excludes_question_text_and_credentials(self) -> None:
        result = {
            "question_id": "q", "source_version_id": "v", "source_relative_path": "relative.doc", "source_question_no": "1", "source_file_sha256": "1234567890abcdef",
            "source_hash_matches": True, "source_asset_evidence_count": 1, "source_asset_kinds": ["ole_object"],
            "stages": {}, "trial_question_assets": 0, "render": {"rendered": False},
            "production_db_sha256_before": "a", "production_db_sha256_after": "a", "trial_db_sha256": "a", "trial_write_run_id": "run",
            "stem": "sensitive source question", "token": "credential-value",
        }
        published = json.dumps(public_summary(result), ensure_ascii=False)
        self.assertNotIn("sensitive source question", published)
        self.assertNotIn("credential-value", published)
        self.assertNotIn("relative.doc", published)

    def test_renderer_never_claims_success_without_verified_materialized_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "trial.db"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE question_assets (question_id TEXT, relative_path TEXT, checksum TEXT, status TEXT, position INTEGER, id TEXT)")
            conn.execute("INSERT INTO question_assets VALUES ('q', NULL, NULL, 'pending', 0, 'a')")
            conn.commit()
            conn.close()
            result = render_verified_html(db, "q", root / "out.html", trial_root=root)
            self.assertFalse(result["rendered"])
            self.assertFalse((root / "out.html").exists())


if __name__ == "__main__":
    unittest.main()
