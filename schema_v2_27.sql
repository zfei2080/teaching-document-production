PRAGMA foreign_keys = OFF;
DROP VIEW IF EXISTS current_controlled_content_segments;

CREATE TABLE controlled_content_segments_v27 (
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
    UNIQUE(source_id, segment_id, textbook_id, curriculum_node_id, allowed_node_ids_json)
);
INSERT INTO controlled_content_segments_v27 SELECT * FROM controlled_content_segments;
DROP TABLE controlled_content_segments;
ALTER TABLE controlled_content_segments_v27 RENAME TO controlled_content_segments;
CREATE VIEW current_controlled_content_segments AS
SELECT s.* FROM controlled_content_segments s
JOIN controlled_content_sources src ON src.id=s.source_id
JOIN controlled_content_import_runs r ON r.id=src.import_run_id
WHERE s.invalidated_at IS NULL AND r.status='validated' AND src.fidelity_status='passed';
INSERT INTO schema_migrations(version, applied_at) VALUES('v2.27-controlled-content-segment-revisions',datetime('now'));
PRAGMA foreign_keys = ON;
