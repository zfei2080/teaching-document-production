PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

-- P1-2f: preserve old controlled-content sources as history while allowing a
-- later, independently validated conversion of the same trusted .doc source.
-- The old (original_sha256, converter_fingerprint) uniqueness prevented an
-- append-only recovery after a conversion artifact became unreadable.
DROP VIEW IF EXISTS current_controlled_content_segments;
DROP TRIGGER IF EXISTS trg_v28_controlled_content_sources_immutable_update;
DROP TRIGGER IF EXISTS trg_v28_controlled_content_sources_immutable_delete;
DROP TRIGGER IF EXISTS trg_v28_controlled_content_segments_immutable_contract;

CREATE TABLE controlled_content_sources_v29 (
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
    source_profile_sha256 TEXT NOT NULL CHECK (length(source_profile_sha256)=64)
);

INSERT INTO controlled_content_sources_v29
SELECT id, import_run_id, original_path, original_sha256, archive_path, archive_sha256,
       converted_path, converted_sha256, converter_fingerprint, fidelity_status,
       source_profile_sha256
FROM controlled_content_sources;

CREATE TABLE controlled_content_segments_v29 (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES controlled_content_sources_v29(id),
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

INSERT INTO controlled_content_segments_v29
SELECT id, source_id, segment_id, content_type, layer, textbook_id, curriculum_node_id,
       allowed_node_ids_json, required_knowledge_json, source_character_range_json,
       normalized_text_sha256, scope_status, invalidated_at, invalidation_reason
FROM controlled_content_segments;

DROP TABLE controlled_content_segments;
DROP TABLE controlled_content_sources;
ALTER TABLE controlled_content_sources_v29 RENAME TO controlled_content_sources;
ALTER TABLE controlled_content_segments_v29 RENAME TO controlled_content_segments;

CREATE TABLE controlled_content_source_revisions (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES controlled_content_sources(id) ON DELETE RESTRICT,
    segment_row_id TEXT NOT NULL UNIQUE REFERENCES controlled_content_segments(id) ON DELETE RESTRICT,
    content_type TEXT NOT NULL CHECK (content_type IN ('knowledge_explanation','consolidation_practice')),
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    source_segment_id TEXT NOT NULL,
    validation_manifest_sha256 TEXT NOT NULL CHECK (length(validation_manifest_sha256)=64),
    segment_contract_hash TEXT NOT NULL CHECK (length(segment_contract_hash)=64),
    revision_hash TEXT NOT NULL UNIQUE CHECK (length(revision_hash)=64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, source_segment_id, textbook_id, curriculum_node_id, revision_hash)
);

CREATE TABLE controlled_content_source_revision_heads (
    content_type TEXT NOT NULL CHECK (content_type IN ('knowledge_explanation','consolidation_practice')),
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    current_revision_id TEXT NOT NULL REFERENCES controlled_content_source_revisions(id),
    head_revision INTEGER NOT NULL CHECK (head_revision >= 1),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (content_type, textbook_id, curriculum_node_id)
);

CREATE TABLE controlled_content_source_revision_head_events (
    id TEXT PRIMARY KEY,
    content_type TEXT NOT NULL CHECK (content_type IN ('knowledge_explanation','consolidation_practice')),
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    previous_revision_id TEXT REFERENCES controlled_content_source_revisions(id),
    replacement_revision_id TEXT NOT NULL UNIQUE REFERENCES controlled_content_source_revisions(id),
    expected_head_revision INTEGER NOT NULL CHECK (expected_head_revision >= 0),
    reason TEXT NOT NULL CHECK (TRIM(reason) <> ''),
    event_hash TEXT NOT NULL UNIQUE CHECK (length(event_hash)=64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_controlled_content_source_revisions_identity
ON controlled_content_source_revisions(content_type, textbook_id, curriculum_node_id, created_at);

CREATE VIEW current_controlled_content_source_revisions AS
SELECT r.*, h.head_revision
FROM controlled_content_source_revisions r
JOIN controlled_content_source_revision_heads h
  ON h.current_revision_id=r.id
 AND h.content_type=r.content_type
 AND h.textbook_id=r.textbook_id
 AND h.curriculum_node_id=r.curriculum_node_id
JOIN controlled_content_segments s ON s.id=r.segment_row_id
JOIN controlled_content_sources src ON src.id=r.source_id AND s.source_id=src.id
JOIN controlled_content_import_runs run ON run.id=src.import_run_id
WHERE run.status='validated'
  AND src.fidelity_status='passed'
  AND s.invalidated_at IS NULL
  AND s.content_type=r.content_type
  AND s.textbook_id=r.textbook_id
  AND s.curriculum_node_id=r.curriculum_node_id
  AND s.segment_id=r.source_segment_id;

CREATE VIEW current_controlled_content_segments AS
SELECT s.*
FROM controlled_content_segments s
JOIN current_controlled_content_source_revisions r ON r.segment_row_id=s.id;

CREATE TRIGGER trg_v29_content_source_revision_insert_guards
BEFORE INSERT ON controlled_content_source_revisions
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM controlled_content_segments s
        JOIN controlled_content_sources src ON src.id=s.source_id
        JOIN controlled_content_import_runs run ON run.id=src.import_run_id
        WHERE s.id=NEW.segment_row_id
          AND src.id=NEW.source_id
          AND s.content_type=NEW.content_type
          AND s.textbook_id=NEW.textbook_id
          AND s.curriculum_node_id=NEW.curriculum_node_id
          AND s.segment_id=NEW.source_segment_id
          AND s.invalidated_at IS NULL
          AND src.fidelity_status='passed'
          AND run.status='validated'
    ) THEN RAISE(ABORT, 'content source revision requires a current validated segment and source') END;
END;

CREATE TRIGGER trg_v29_content_source_revision_immutable_update
BEFORE UPDATE ON controlled_content_source_revisions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled content source revisions are append-only');
END;

CREATE TRIGGER trg_v29_content_source_revision_immutable_delete
BEFORE DELETE ON controlled_content_source_revisions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled content source revisions are append-only');
END;

CREATE TRIGGER trg_v29_content_revision_head_event_insert_guards
BEFORE INSERT ON controlled_content_source_revision_head_events
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM controlled_content_source_revisions r
         WHERE r.id=NEW.replacement_revision_id
           AND r.content_type=NEW.content_type
           AND r.textbook_id=NEW.textbook_id
           AND r.curriculum_node_id=NEW.curriculum_node_id
    ) THEN RAISE(ABORT, 'content revision head replacement must match content identity') END;
    SELECT CASE WHEN NEW.previous_revision_id IS NULL AND EXISTS (
        SELECT 1 FROM controlled_content_source_revision_heads h
         WHERE h.content_type=NEW.content_type
           AND h.textbook_id=NEW.textbook_id
           AND h.curriculum_node_id=NEW.curriculum_node_id
    ) THEN RAISE(ABORT, 'first content revision head event cannot replace an existing head') END;
    SELECT CASE WHEN NEW.previous_revision_id IS NULL AND NEW.expected_head_revision <> 0
      THEN RAISE(ABORT, 'first content revision head event must expect revision zero') END;
    SELECT CASE WHEN NEW.previous_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM controlled_content_source_revision_heads h
         WHERE h.content_type=NEW.content_type
           AND h.textbook_id=NEW.textbook_id
           AND h.curriculum_node_id=NEW.curriculum_node_id
           AND h.current_revision_id=NEW.previous_revision_id
           AND h.head_revision=NEW.expected_head_revision
    ) THEN RAISE(ABORT, 'content revision head event does not match the current head') END;
END;

CREATE TRIGGER trg_v29_content_revision_head_event_apply
AFTER INSERT ON controlled_content_source_revision_head_events
FOR EACH ROW
BEGIN
    INSERT INTO controlled_content_source_revision_heads
        (content_type, textbook_id, curriculum_node_id, current_revision_id, head_revision)
    SELECT NEW.content_type, NEW.textbook_id, NEW.curriculum_node_id,
           NEW.replacement_revision_id, 1
     WHERE NEW.previous_revision_id IS NULL;
    UPDATE controlled_content_source_revision_heads
       SET current_revision_id=NEW.replacement_revision_id,
           head_revision=head_revision+1,
           updated_at=CURRENT_TIMESTAMP
     WHERE NEW.previous_revision_id IS NOT NULL
       AND content_type=NEW.content_type
       AND textbook_id=NEW.textbook_id
       AND curriculum_node_id=NEW.curriculum_node_id
       AND current_revision_id=NEW.previous_revision_id
       AND head_revision=NEW.expected_head_revision;
END;

CREATE TRIGGER trg_v29_content_revision_head_insert_guard
BEFORE INSERT ON controlled_content_source_revision_heads
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM controlled_content_source_revision_head_events e
     WHERE e.content_type=NEW.content_type
       AND e.textbook_id=NEW.textbook_id
       AND e.curriculum_node_id=NEW.curriculum_node_id
       AND e.previous_revision_id IS NULL
       AND e.replacement_revision_id=NEW.current_revision_id
       AND e.expected_head_revision=0
)
BEGIN
    SELECT RAISE(ABORT, 'content revision heads are created only through append-only head events');
END;

CREATE TRIGGER trg_v29_content_revision_head_update_guard
BEFORE UPDATE ON controlled_content_source_revision_heads
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM controlled_content_source_revision_head_events e
     WHERE e.content_type=NEW.content_type
       AND e.textbook_id=NEW.textbook_id
       AND e.curriculum_node_id=NEW.curriculum_node_id
       AND e.previous_revision_id=OLD.current_revision_id
       AND e.replacement_revision_id=NEW.current_revision_id
       AND e.expected_head_revision=OLD.head_revision
)
BEGIN
    SELECT RAISE(ABORT, 'content revision heads change only through append-only head events');
END;

CREATE TRIGGER trg_v29_content_revision_head_immutable_delete
BEFORE DELETE ON controlled_content_source_revision_heads
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'content revision heads are append-only');
END;

CREATE TRIGGER trg_v29_content_revision_head_event_immutable_update
BEFORE UPDATE ON controlled_content_source_revision_head_events
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'content revision head events are append-only');
END;

CREATE TRIGGER trg_v29_content_revision_head_event_immutable_delete
BEFORE DELETE ON controlled_content_source_revision_head_events
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'content revision head events are append-only');
END;

CREATE TRIGGER trg_v29_controlled_content_sources_immutable_update
BEFORE UPDATE ON controlled_content_sources
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled content sources are append-only');
END;

CREATE TRIGGER trg_v29_controlled_content_sources_immutable_delete
BEFORE DELETE ON controlled_content_sources
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled content sources are append-only');
END;

CREATE TRIGGER trg_v29_controlled_content_segments_immutable_contract
BEFORE UPDATE OF source_id, segment_id, content_type, layer, textbook_id,
                 curriculum_node_id, allowed_node_ids_json, required_knowledge_json,
                 source_character_range_json, normalized_text_sha256, scope_status
ON controlled_content_segments
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled content segment contracts are immutable; create a new content source revision');
END;

CREATE TRIGGER trg_v29_snapshot_invalidate_content_revision_head
AFTER INSERT ON controlled_content_source_revision_head_events
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='controlled_content_source_revision_changed'
     WHERE question_id IN (
         SELECT question_id FROM question_textbooks
          WHERE textbook_id=NEW.textbook_id AND curriculum_node_id=NEW.curriculum_node_id
     );
END;

PRAGMA foreign_keys = ON;
