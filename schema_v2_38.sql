BEGIN;
CREATE TABLE content_item_math_validation_evidence (
    id TEXT PRIMARY KEY,
    content_item_id TEXT NOT NULL REFERENCES content_items(id),
    question_id TEXT NOT NULL REFERENCES questions(id),
    validation_contract_id TEXT NOT NULL,
    validator_id TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK(validation_status IN ('pass','fail','unsupported')),
    computed_answer TEXT,
    source_answer TEXT,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    input_sha256 TEXT NOT NULL CHECK(length(input_sha256)=64),
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(content_item_id, validation_contract_id, validator_id, validator_version, input_sha256)
);
CREATE INDEX idx_content_item_math_validation_item ON content_item_math_validation_evidence(content_item_id,validation_status);
CREATE TRIGGER trg_v38_content_item_math_validation_immutable_update
BEFORE UPDATE ON content_item_math_validation_evidence
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content item mathematical validation evidence is append-only'); END;
CREATE TRIGGER trg_v38_content_item_math_validation_immutable_delete
BEFORE DELETE ON content_item_math_validation_evidence
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'content item mathematical validation evidence is append-only'); END;
CREATE VIEW content_item_question_eligibility AS
SELECT
    ci.id AS content_item_id,
    q.id AS question_id,
    CASE WHEN
        EXISTS (
            SELECT 1 FROM content_item_math_validation_evidence mv
            WHERE mv.content_item_id=ci.id AND mv.question_id=q.id
              AND mv.validation_status='pass' AND mv.content_sha256=ci.content_sha256
        )
        AND EXISTS (
            SELECT 1 FROM content_item_knowledge_mappings km
            JOIN knowledge_points kp ON kp.id=km.knowledge_point_id
            WHERE km.content_item_id=ci.id AND km.relation_type='primary'
              AND km.mapping_status='validated' AND kp.review_status='approved'
        )
        AND EXISTS (
            SELECT 1 FROM content_item_difficulty_evidence de
            WHERE de.content_item_id=ci.id AND de.evidence_status='validated'
        )
        THEN 1 ELSE 0 END AS content_eligible,
    CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM content_item_math_validation_evidence mv
            WHERE mv.content_item_id=ci.id AND mv.question_id=q.id
              AND mv.validation_status='pass' AND mv.content_sha256=ci.content_sha256
        ) THEN 'mathematical_validation_missing_or_nonpassing'
        WHEN NOT EXISTS (
            SELECT 1 FROM content_item_knowledge_mappings km
            JOIN knowledge_points kp ON kp.id=km.knowledge_point_id
            WHERE km.content_item_id=ci.id AND km.relation_type='primary'
              AND km.mapping_status='validated' AND kp.review_status='approved'
        ) THEN 'validated_primary_knowledge_mapping_missing'
        WHEN NOT EXISTS (
            SELECT 1 FROM content_item_difficulty_evidence de
            WHERE de.content_item_id=ci.id AND de.evidence_status='validated'
        ) THEN 'validated_difficulty_evidence_missing'
        ELSE 'eligible_for_content_selection_only'
    END AS eligibility_reason
FROM content_items ci
JOIN content_item_question_links l ON l.content_item_id=ci.id
JOIN questions q ON q.id=l.question_id;
INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.38-content-item-mathematical-validation-contract', CURRENT_TIMESTAMP);
INSERT INTO content_change_ledger(
    id,event_type,entity_type,entity_id,operation,before_state_json,after_state_json,
    before_hash,after_hash,reason,actor,tool_id,tool_version,import_run_id,transaction_id,git_revision
) VALUES(
    'schema:v2.38-content-item-mathematical-validation-contract','schema_migration','schema_migrations',
    'v2.38-content-item-mathematical-validation-contract','schema',NULL,
    '{"migration":"v2.38-content-item-mathematical-validation-contract"}',NULL,NULL,
    'add append-only item-level mathematical validation evidence and fail-closed content eligibility view',
    'migration_runner','apply_schema_v2_38','1.0.0',NULL,
    'schema:v2.38-content-item-mathematical-validation-contract',NULL
);
COMMIT;
