BEGIN;
CREATE TABLE content_item_difficulty_evidence_v2_39 (
    id TEXT PRIMARY KEY,
    content_item_id TEXT NOT NULL REFERENCES content_items(id),
    difficulty TEXT NOT NULL CHECK(difficulty IN ('基础','中等','中等偏难','高','拓展')),
    evidence_status TEXT NOT NULL CHECK(evidence_status IN ('candidate','validated','blocked','rejected','superseded')),
    method TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(content_item_id, difficulty, evidence_sha256)
);
DROP TABLE content_item_difficulty_evidence;
ALTER TABLE content_item_difficulty_evidence_v2_39 RENAME TO content_item_difficulty_evidence;
CREATE INDEX idx_content_item_difficulty_evidence_item ON content_item_difficulty_evidence(content_item_id,evidence_status);
INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.39-repair-content-item-difficulty-enum', CURRENT_TIMESTAMP);
INSERT INTO content_change_ledger(
    id,event_type,entity_type,entity_id,operation,before_state_json,after_state_json,
    before_hash,after_hash,reason,actor,tool_id,tool_version,import_run_id,transaction_id,git_revision
) VALUES(
    'schema:v2.39-repair-content-item-difficulty-enum','schema_migration','schema_migrations',
    'v2.39-repair-content-item-difficulty-enum','schema',NULL,
    '{"migration":"v2.39-repair-content-item-difficulty-enum"}',NULL,NULL,
    'repair malformed difficulty enumeration before source-derived difficulty evidence is admitted',
    'migration_runner','apply_schema_v2_39','1.0.0',NULL,
    'schema:v2.39-repair-content-item-difficulty-enum',NULL
);
COMMIT;
