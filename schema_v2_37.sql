BEGIN;
CREATE TABLE questions_v2_37 (
    id TEXT PRIMARY KEY,
    stem TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '[]',
    answer TEXT,
    analysis TEXT,
    question_type TEXT NOT NULL,
    difficulty TEXT CHECK (difficulty IN ('基础', '中等', '中等偏难', '高', '拓展')),
    stage TEXT CHECK (stage IN ('小学', '初中', '高中')),
    grade_level TEXT,
    source_document_id TEXT NOT NULL REFERENCES source_documents(id),
    source_fragment_id TEXT REFERENCES source_fragments(id),
    source_question_no TEXT,
    source_page INTEGER,
    content_hash TEXT NOT NULL UNIQUE,
    extraction_status TEXT NOT NULL DEFAULT 'candidate' CHECK (extraction_status IN ('candidate', 'structured', 'rejected')),
    quality_status TEXT NOT NULL DEFAULT 'pending' CHECK (quality_status IN ('pending', 'needs_review', 'approved', 'blocked')),
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO questions_v2_37(
    id,stem,options_json,answer,analysis,question_type,difficulty,stage,grade_level,
    source_document_id,source_fragment_id,source_question_no,source_page,content_hash,
    extraction_status,quality_status,review_status,created_at,updated_at
)
SELECT
    id,stem,options_json,answer,analysis,question_type,difficulty,stage,grade_level,
    source_document_id,source_fragment_id,source_question_no,source_page,content_hash,
    extraction_status,quality_status,review_status,created_at,updated_at
FROM questions;
DROP TABLE questions;
ALTER TABLE questions_v2_37 RENAME TO questions;
INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.37-question-stage-optional-for-content-library', CURRENT_TIMESTAMP);
INSERT INTO content_change_ledger(
    id,event_type,entity_type,entity_id,operation,before_state_json,after_state_json,
    before_hash,after_hash,reason,actor,tool_id,tool_version,import_run_id,transaction_id,git_revision
) VALUES(
    'schema:v2.37-question-stage-optional-for-content-library','schema_migration','schema_migrations',
    'v2.37-question-stage-optional-for-content-library','schema',NULL,
    '{"migration":"v2.37-question-stage-optional-for-content-library"}',NULL,NULL,
    'allow source-derived content questions to remain without a static stage or grade label',
    'migration_runner','apply_schema_v2_37','1.0.0',NULL,
    'schema:v2.37-question-stage-optional-for-content-library',NULL
);
COMMIT;
