PRAGMA foreign_keys = ON;

CREATE TABLE controlled_content_import_runs (
    id TEXT PRIMARY KEY,
    manifest_sha256 TEXT NOT NULL UNIQUE,
    p1_2b_audit_sha256 TEXT NOT NULL,
    importer_id TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('validated')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE controlled_content_sources (
    id TEXT PRIMARY KEY,
    import_run_id TEXT NOT NULL REFERENCES controlled_content_import_runs(id),
    original_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL CHECK (length(original_sha256)=64),
    archive_path TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL CHECK (length(archive_sha256)=64),
    converted_path TEXT NOT NULL,
    converted_sha256 TEXT NOT NULL CHECK (length(converted_sha256)=64),
    converter_fingerprint TEXT NOT NULL CHECK (length(converter_fingerprint)=64),
    fidelity_status TEXT NOT NULL CHECK (fidelity_status='passed'),
    source_profile_sha256 TEXT NOT NULL CHECK (length(source_profile_sha256)=64),
    UNIQUE(original_sha256, converter_fingerprint)
);

CREATE TABLE controlled_content_segments (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES controlled_content_sources(id),
    segment_id TEXT NOT NULL,
    content_type TEXT NOT NULL CHECK (content_type IN ('knowledge_explanation','consolidation_practice')),
    layer TEXT NOT NULL CHECK (layer IN ('public_core','basic_reinforcement','standard_extension','challenge_extension')),
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    allowed_node_ids_json TEXT NOT NULL CHECK (json_valid(allowed_node_ids_json)),
    required_knowledge_json TEXT NOT NULL CHECK (json_valid(required_knowledge_json)),
    source_character_range_json TEXT NOT NULL CHECK (json_valid(source_character_range_json)),
    normalized_text_sha256 TEXT NOT NULL CHECK (length(normalized_text_sha256)=64),
    scope_status TEXT NOT NULL CHECK (scope_status='candidate_only'),
    invalidated_at TEXT,
    invalidation_reason TEXT,
    UNIQUE(source_id, segment_id)
);

CREATE VIEW current_controlled_content_segments AS
SELECT s.*
FROM controlled_content_segments s
JOIN controlled_content_sources src ON src.id=s.source_id
JOIN controlled_content_import_runs r ON r.id=src.import_run_id
WHERE s.invalidated_at IS NULL AND r.status='validated' AND src.fidelity_status='passed';

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.26-controlled-content-evidence', datetime('now'));
