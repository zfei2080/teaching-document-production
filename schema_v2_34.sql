BEGIN;

CREATE TABLE content_change_ledger (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'schema_migration','source_registered','source_superseded','source_status_changed',
        'import_run_started','import_run_finished','extraction_started','extraction_finished',
        'content_created','content_status_changed','mapping_created','mapping_status_changed',
        'retry_requested','invalidation','manual_correction'
    )),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN ('create','status_change','supersede','invalidate','retry','schema')),
    before_state_json TEXT CHECK(before_state_json IS NULL OR json_valid(before_state_json)),
    after_state_json TEXT CHECK(after_state_json IS NULL OR json_valid(after_state_json)),
    before_hash TEXT CHECK(before_hash IS NULL OR length(before_hash)=64),
    after_hash TEXT CHECK(after_hash IS NULL OR length(after_hash)=64),
    reason TEXT NOT NULL CHECK(trim(reason)<>''),
    actor TEXT NOT NULL CHECK(trim(actor)<>''),
    tool_id TEXT NOT NULL CHECK(trim(tool_id)<>''),
    tool_version TEXT NOT NULL CHECK(trim(tool_version)<>''),
    import_run_id TEXT,
    transaction_id TEXT NOT NULL CHECK(trim(transaction_id)<>''),
    git_revision TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE content_import_runs (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode IN ('baseline','delta','extract','retry')),
    selection_json TEXT NOT NULL CHECK(json_valid(selection_json)),
    selection_sha256 TEXT NOT NULL CHECK(length(selection_sha256)=64),
    importer_id TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(mode, selection_sha256, importer_id, importer_version)
);

CREATE TABLE content_import_run_events (
    id TEXT PRIMARY KEY,
    import_run_id TEXT NOT NULL REFERENCES content_import_runs(id),
    status TEXT NOT NULL CHECK(status IN ('started','completed','failed','cancelled')),
    totals_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(totals_json)),
    error_code TEXT,
    error_message TEXT,
    change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE content_source_versions (
    id TEXT PRIMARY KEY,
    source_document_id TEXT NOT NULL REFERENCES source_documents(id),
    original_relative_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL CHECK(length(original_sha256)=64),
    file_size_bytes INTEGER NOT NULL CHECK(file_size_bytes>=0),
    file_type TEXT NOT NULL CHECK(file_type IN ('doc','docx','pdf','image','zip')),
    trusted_source INTEGER NOT NULL CHECK(trusted_source IN (0,1)),
    registered_import_run_id TEXT NOT NULL REFERENCES content_import_runs(id),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_document_id, original_sha256)
);

CREATE TABLE content_source_version_heads (
    source_document_id TEXT PRIMARY KEY REFERENCES source_documents(id),
    current_source_version_id TEXT NOT NULL REFERENCES content_source_versions(id),
    change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE content_source_lifecycle_events (
    id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL REFERENCES content_source_versions(id),
    status TEXT NOT NULL CHECK(status IN (
        'registered','queued','extracting','extracted','deferred','unsupported','failed','superseded','invalidated'
    )),
    reason TEXT NOT NULL CHECK(trim(reason)<>''),
    retry_eligible INTEGER NOT NULL CHECK(retry_eligible IN (0,1)),
    change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE content_extraction_runs (
    id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL REFERENCES content_source_versions(id),
    import_run_id TEXT NOT NULL REFERENCES content_import_runs(id),
    engine_id TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    profile_schema TEXT NOT NULL,
    extraction_manifest_path TEXT,
    extraction_manifest_sha256 TEXT CHECK(extraction_manifest_sha256 IS NULL OR length(extraction_manifest_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_version_id, extraction_manifest_sha256)
);

CREATE TABLE source_content_blocks (
    id TEXT PRIMARY KEY,
    extraction_run_id TEXT NOT NULL REFERENCES content_extraction_runs(id),
    ordinal INTEGER NOT NULL CHECK(ordinal>=0),
    block_kind TEXT NOT NULL CHECK(block_kind IN ('paragraph','table_cell','header','footer','footnote','comment','mixed')),
    parent_block_id TEXT REFERENCES source_content_blocks(id),
    locator_json TEXT NOT NULL CHECK(json_valid(locator_json)),
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL CHECK(length(raw_sha256)=64),
    normalized_sha256 TEXT NOT NULL CHECK(length(normalized_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(extraction_run_id, ordinal)
);

CREATE TABLE source_content_assets (
    id TEXT PRIMARY KEY,
    extraction_run_id TEXT NOT NULL REFERENCES content_extraction_runs(id),
    ordinal INTEGER NOT NULL CHECK(ordinal>=0),
    asset_kind TEXT NOT NULL CHECK(asset_kind IN ('inline_shape','shape','ole_object','formula','embedded_media','unknown')),
    locator_json TEXT NOT NULL CHECK(json_valid(locator_json)),
    package_reference TEXT,
    asset_sha256 TEXT CHECK(asset_sha256 IS NULL OR length(asset_sha256)=64),
    extraction_status TEXT NOT NULL CHECK(extraction_status IN ('referenced','extracted','deferred','unsupported','failed')),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(extraction_run_id, ordinal)
);

CREATE TABLE content_items (
    id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL REFERENCES content_source_versions(id),
    item_kind TEXT NOT NULL CHECK(item_kind IN (
        'knowledge_note','formula','property','method','common_error','worked_example','exercise_set','question','other'
    )),
    student_payload_json TEXT NOT NULL CHECK(json_valid(student_payload_json)),
    display_order INTEGER NOT NULL DEFAULT 0,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_version_id, item_kind, content_sha256)
);

CREATE TABLE content_item_evidence (
    id TEXT PRIMARY KEY,
    content_item_id TEXT NOT NULL REFERENCES content_items(id),
    field_name TEXT NOT NULL CHECK(trim(field_name)<>''),
    visibility TEXT NOT NULL CHECK(visibility IN ('student','internal')),
    source_block_id TEXT NOT NULL REFERENCES source_content_blocks(id),
    character_range_json TEXT NOT NULL CHECK(
        json_valid(character_range_json)
        AND json_type(character_range_json)='array'
        AND json_array_length(character_range_json)=2
        AND json_type(character_range_json,'$[0]')='integer'
        AND json_type(character_range_json,'$[1]')='integer'
        AND json_extract(character_range_json,'$[0]')>=0
        AND json_extract(character_range_json,'$[1]')>json_extract(character_range_json,'$[0]')
    ),
    evidence_text TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE content_item_question_links (
    content_item_id TEXT PRIMARY KEY REFERENCES content_items(id),
    question_id TEXT NOT NULL UNIQUE REFERENCES questions(id),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE question_internal_evidence (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id),
    field_name TEXT NOT NULL CHECK(field_name IN ('answer','analysis','scoring','solution')),
    source_block_id TEXT NOT NULL REFERENCES source_content_blocks(id),
    character_range_json TEXT NOT NULL CHECK(
        json_valid(character_range_json)
        AND json_type(character_range_json)='array'
        AND json_array_length(character_range_json)=2
        AND json_type(character_range_json,'$[0]')='integer'
        AND json_type(character_range_json,'$[1]')='integer'
        AND json_extract(character_range_json,'$[0]')>=0
        AND json_extract(character_range_json,'$[1]')>json_extract(character_range_json,'$[0]')
    ),
    internal_payload TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(question_id, field_name, source_block_id, evidence_sha256)
);

CREATE TABLE content_item_curriculum_mappings (
    id TEXT PRIMARY KEY,
    content_item_id TEXT NOT NULL REFERENCES content_items(id),
    textbook_id TEXT NOT NULL REFERENCES textbooks(id),
    curriculum_node_id TEXT NOT NULL REFERENCES curriculum_nodes(id),
    mapping_status TEXT NOT NULL CHECK(mapping_status IN ('unmapped','candidate','validated','blocked','rejected','superseded')),
    mapping_method TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(content_item_id, textbook_id, curriculum_node_id, evidence_sha256)
);

CREATE TABLE content_item_knowledge_mappings (
    id TEXT PRIMARY KEY,
    content_item_id TEXT NOT NULL REFERENCES content_items(id),
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id),
    relation_type TEXT NOT NULL CHECK(relation_type IN ('primary','secondary','prerequisite','method','error_pattern')),
    mapping_status TEXT NOT NULL CHECK(mapping_status IN ('unmapped','candidate','validated','blocked','rejected','superseded')),
    mapping_method TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(content_item_id, knowledge_point_id, relation_type, evidence_sha256)
);

CREATE TABLE content_dependency_edges (
    id TEXT PRIMARY KEY,
    from_content_item_id TEXT NOT NULL REFERENCES content_items(id),
    to_content_item_id TEXT REFERENCES content_items(id),
    to_knowledge_point_id TEXT REFERENCES knowledge_points(id),
    relation_type TEXT NOT NULL CHECK(relation_type IN ('requires','uses','extends','contrasts')),
    edge_status TEXT NOT NULL CHECK(edge_status IN ('candidate','validated','blocked','rejected','superseded')),
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK((to_content_item_id IS NOT NULL) <> (to_knowledge_point_id IS NOT NULL)),
    UNIQUE(from_content_item_id, to_content_item_id, to_knowledge_point_id, relation_type, evidence_sha256)
);

CREATE TABLE content_item_difficulty_evidence (
    id TEXT PRIMARY KEY,
    content_item_id TEXT NOT NULL REFERENCES content_items(id),
    difficulty TEXT NOT NULL CHECK(difficulty IN ('??','??','????','?','??')),
    evidence_status TEXT NOT NULL CHECK(evidence_status IN ('candidate','validated','blocked','rejected','superseded')),
    method TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(content_item_id, difficulty, evidence_sha256)
);

CREATE TABLE content_item_role_suitability (
    id TEXT PRIMARY KEY,
    content_item_id TEXT NOT NULL REFERENCES content_items(id),
    role_candidate TEXT NOT NULL CHECK(role_candidate IN ('public_core','basic_reinforcement','standard_extension','challenge_extension','self_assessment')),
    suitability_status TEXT NOT NULL CHECK(suitability_status IN ('candidate','validated','blocked','rejected','superseded')),
    reason_json TEXT NOT NULL CHECK(json_valid(reason_json)),
    reason_sha256 TEXT NOT NULL CHECK(length(reason_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(content_item_id, role_candidate, reason_sha256)
);

CREATE INDEX idx_content_ledger_entity ON content_change_ledger(entity_type, entity_id, created_at);
CREATE INDEX idx_content_source_versions_document ON content_source_versions(source_document_id, created_at);
CREATE INDEX idx_content_blocks_extraction ON source_content_blocks(extraction_run_id, ordinal);
CREATE INDEX idx_content_items_source ON content_items(source_version_id, item_kind);
CREATE INDEX idx_question_internal_evidence_question ON question_internal_evidence(question_id, field_name);
CREATE INDEX idx_content_curriculum_mapping_lookup ON content_item_curriculum_mappings(textbook_id, curriculum_node_id, mapping_status);
CREATE INDEX idx_content_knowledge_mapping_lookup ON content_item_knowledge_mappings(knowledge_point_id, mapping_status);

CREATE TRIGGER trg_v34_change_ledger_immutable_update
BEFORE UPDATE ON content_change_ledger
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content change ledger is append-only'); END;
CREATE TRIGGER trg_v34_change_ledger_immutable_delete
BEFORE DELETE ON content_change_ledger
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content change ledger is append-only'); END;

CREATE TRIGGER trg_v34_source_version_immutable_update
BEFORE UPDATE ON content_source_versions
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content source versions are append-only'); END;
CREATE TRIGGER trg_v34_source_version_immutable_delete
BEFORE DELETE ON content_source_versions
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content source versions are append-only'); END;

CREATE TRIGGER trg_v34_source_blocks_immutable_update
BEFORE UPDATE ON source_content_blocks
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'source content blocks are append-only'); END;
CREATE TRIGGER trg_v34_source_blocks_immutable_delete
BEFORE DELETE ON source_content_blocks
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'source content blocks are append-only'); END;

CREATE TRIGGER trg_v34_content_items_immutable_update
BEFORE UPDATE ON content_items
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content items are append-only'); END;
CREATE TRIGGER trg_v34_content_items_immutable_delete
BEFORE DELETE ON content_items
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content items are append-only'); END;

CREATE TRIGGER trg_v34_content_evidence_immutable_update
BEFORE UPDATE ON content_item_evidence
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content item evidence is append-only'); END;
CREATE TRIGGER trg_v34_content_evidence_immutable_delete
BEFORE DELETE ON content_item_evidence
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content item evidence is append-only'); END;

CREATE TRIGGER trg_v34_question_internal_evidence_immutable_update
BEFORE UPDATE ON question_internal_evidence
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'question internal evidence is append-only'); END;
CREATE TRIGGER trg_v34_question_internal_evidence_immutable_delete
BEFORE DELETE ON question_internal_evidence
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'question internal evidence is append-only'); END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.34-database-enrichment-change-ledger', CURRENT_TIMESTAMP);

INSERT INTO content_change_ledger(
    id,event_type,entity_type,entity_id,operation,before_state_json,after_state_json,
    before_hash,after_hash,reason,actor,tool_id,tool_version,import_run_id,transaction_id,git_revision
) VALUES(
    'schema:v2.34-database-enrichment-change-ledger','schema_migration','schema_migrations',
    'v2.34-database-enrichment-change-ledger','schema',NULL,
    '{"migration":"v2.34-database-enrichment-change-ledger"}',NULL,NULL,
    'apply database enrichment change-ledger schema','migration_runner','apply_schema_v2_34','1.0.0',NULL,
    'schema:v2.34-database-enrichment-change-ledger',NULL
);

COMMIT;
