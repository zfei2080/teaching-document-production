BEGIN;

CREATE TABLE content_source_intake_records (
    id TEXT PRIMARY KEY,
    original_relative_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL CHECK(length(original_sha256)=64),
    file_size_bytes INTEGER NOT NULL CHECK(file_size_bytes>=0),
    detected_file_type TEXT NOT NULL CHECK(detected_file_type IN ('doc','docx','pdf','image','zip','other')),
    supported_for_extraction INTEGER NOT NULL CHECK(supported_for_extraction IN (0,1)),
    source_document_id TEXT REFERENCES source_documents(id),
    registered_import_run_id TEXT NOT NULL REFERENCES content_import_runs(id),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(original_relative_path, original_sha256),
    CHECK((supported_for_extraction=1 AND source_document_id IS NOT NULL) OR (supported_for_extraction=0 AND source_document_id IS NULL))
);

CREATE TABLE content_source_intake_lifecycle_events (
    id TEXT PRIMARY KEY,
    intake_record_id TEXT NOT NULL REFERENCES content_source_intake_records(id),
    status TEXT NOT NULL CHECK(status IN ('registered','deferred','unsupported','failed','superseded','invalidated')),
    reason TEXT NOT NULL CHECK(trim(reason)<>''),
    retry_eligible INTEGER NOT NULL CHECK(retry_eligible IN (0,1)),
    change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE content_source_versions
ADD COLUMN intake_record_id TEXT REFERENCES content_source_intake_records(id);

CREATE INDEX idx_content_source_intake_path ON content_source_intake_records(original_relative_path, created_at);
CREATE INDEX idx_content_source_intake_status ON content_source_intake_lifecycle_events(intake_record_id, created_at);

CREATE TRIGGER trg_v35_source_intake_records_immutable_update
BEFORE UPDATE ON content_source_intake_records
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content source intake records are append-only'); END;
CREATE TRIGGER trg_v35_source_intake_records_immutable_delete
BEFORE DELETE ON content_source_intake_records
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content source intake records are append-only'); END;
CREATE TRIGGER trg_v35_source_intake_events_immutable_update
BEFORE UPDATE ON content_source_intake_lifecycle_events
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content source intake lifecycle is append-only'); END;
CREATE TRIGGER trg_v35_source_intake_events_immutable_delete
BEFORE DELETE ON content_source_intake_lifecycle_events
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content source intake lifecycle is append-only'); END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.35-database-enrichment-complete-source-inventory', CURRENT_TIMESTAMP);

INSERT INTO content_change_ledger(
    id,event_type,entity_type,entity_id,operation,before_state_json,after_state_json,
    before_hash,after_hash,reason,actor,tool_id,tool_version,import_run_id,transaction_id,git_revision
) VALUES(
    'schema:v2.35-database-enrichment-complete-source-inventory','schema_migration','schema_migrations',
    'v2.35-database-enrichment-complete-source-inventory','schema',NULL,
    '{"migration":"v2.35-database-enrichment-complete-source-inventory"}',NULL,NULL,
    'add complete source inventory records for unsupported formats','migration_runner','apply_schema_v2_35','1.0.0',NULL,
    'schema:v2.35-database-enrichment-complete-source-inventory',NULL
);

COMMIT;
