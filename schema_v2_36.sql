BEGIN;
CREATE TABLE question_source_asset_evidence (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    source_content_asset_id TEXT NOT NULL REFERENCES source_content_assets(id),
    source_block_id TEXT NOT NULL REFERENCES source_content_blocks(id),
    option_label TEXT,
    position INTEGER NOT NULL CHECK(position>=0),
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(question_id, source_content_asset_id)
);
CREATE INDEX idx_question_source_asset_evidence_question ON question_source_asset_evidence(question_id,position);
CREATE TRIGGER trg_v36_question_source_asset_evidence_immutable_update
BEFORE UPDATE ON question_source_asset_evidence
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'question source asset evidence is append-only'); END;
CREATE TRIGGER trg_v36_question_source_asset_evidence_immutable_delete
BEFORE DELETE ON question_source_asset_evidence
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'question source asset evidence is append-only'); END;
INSERT INTO schema_migrations(version,applied_at) VALUES('v2.36-question-source-asset-evidence',CURRENT_TIMESTAMP);
INSERT INTO content_change_ledger(id,event_type,entity_type,entity_id,operation,before_state_json,after_state_json,before_hash,after_hash,reason,actor,tool_id,tool_version,import_run_id,transaction_id,git_revision) VALUES('schema:v2.36-question-source-asset-evidence','schema_migration','schema_migrations','v2.36-question-source-asset-evidence','schema',NULL,'{"migration":"v2.36-question-source-asset-evidence"}',NULL,NULL,'add question-to-source-asset evidence for image-only options','migration_runner','apply_schema_v2_36','1.0.0',NULL,'schema:v2.36-question-source-asset-evidence',NULL);
COMMIT;
