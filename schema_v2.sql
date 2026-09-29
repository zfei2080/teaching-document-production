PRAGMA foreign_keys = ON;

-- Unified teaching-document system development schema.
-- This schema creates a new development database only. It does not modify legacy databases.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS textbooks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '数学',
    publisher TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    catalog_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, subject, catalog_version)
);

CREATE TABLE IF NOT EXISTS curriculum_nodes (
    id TEXT PRIMARY KEY,
    textbook_id TEXT NOT NULL REFERENCES textbooks(id),
    parent_id TEXT REFERENCES curriculum_nodes(id),
    stage TEXT NOT NULL CHECK (stage IN ('小学', '初中', '高中')),
    grade_level TEXT NOT NULL,
    node_type TEXT NOT NULL CHECK (node_type IN ('term', 'chapter', 'unit', 'topic')),
    name TEXT NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0,
    catalog_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_points (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    knowledge_type TEXT NOT NULL CHECK (knowledge_type IN ('concept', 'formula', 'property', 'criterion', 'method', 'model', 'error_pattern')),
    stage_scope TEXT NOT NULL,
    definition_text TEXT,
    formulas_json TEXT NOT NULL DEFAULT '[]',
    properties_json TEXT NOT NULL DEFAULT '[]',
    conditions_json TEXT NOT NULL DEFAULT '[]',
    common_errors_json TEXT NOT NULL DEFAULT '[]',
    version TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS curriculum_knowledge_points (
    curriculum_node_id TEXT NOT NULL REFERENCES curriculum_nodes(id) ON DELETE CASCADE,
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'primary' CHECK (relation_type IN ('primary', 'secondary', 'prerequisite')),
    PRIMARY KEY (curriculum_node_id, knowledge_point_id)
);

CREATE TABLE IF NOT EXISTS source_documents (
    id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    file_hash TEXT NOT NULL,
    file_type TEXT NOT NULL CHECK (file_type IN ('docx', 'pdf', 'doc', 'image')),
    source_label TEXT,
    copyright_status TEXT NOT NULL DEFAULT 'unknown' CHECK (copyright_status IN ('unknown', 'owned', 'authorized', 'restricted')),
    parse_status TEXT NOT NULL DEFAULT 'pending' CHECK (parse_status IN ('pending', 'parsed', 'failed')),
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_fragments (
    id TEXT PRIMARY KEY,
    source_document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    location_type TEXT NOT NULL CHECK (location_type IN ('page', 'paragraph', 'table', 'image', 'mixed')),
    page_number INTEGER,
    paragraph_index INTEGER,
    question_number TEXT,
    raw_text TEXT,
    raw_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    stem TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '[]',
    answer TEXT,
    analysis TEXT,
    question_type TEXT NOT NULL,
    difficulty TEXT CHECK (difficulty IN ('基础', '中等', '中等偏难', '高', '拓展')),
    stage TEXT NOT NULL CHECK (stage IN ('小学', '初中', '高中')),
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

CREATE TABLE IF NOT EXISTS question_textbooks (
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    textbook_id TEXT NOT NULL REFERENCES textbooks(id),
    curriculum_node_id TEXT REFERENCES curriculum_nodes(id),
    fit_status TEXT NOT NULL DEFAULT 'pending' CHECK (fit_status IN ('pending', 'approved', 'rejected')),
    PRIMARY KEY (question_id, textbook_id, curriculum_node_id)
);

CREATE TABLE IF NOT EXISTS question_knowledge_points (
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id),
    relation_type TEXT NOT NULL DEFAULT 'primary' CHECK (relation_type IN ('primary', 'secondary')),
    PRIMARY KEY (question_id, knowledge_point_id)
);

CREATE TABLE IF NOT EXISTS question_assets (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('image', 'formula', 'table')),
    relative_path TEXT,
    source_fragment_id TEXT REFERENCES source_fragments(id),
    position INTEGER NOT NULL DEFAULT 0,
    checksum TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'verified', 'missing', 'rejected'))
);

CREATE TABLE IF NOT EXISTS classes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    textbook_id TEXT REFERENCES textbooks(id),
    grade_level TEXT,
    current_curriculum_node_id TEXT REFERENCES curriculum_nodes(id),
    student_profile_json TEXT NOT NULL DEFAULT '{}',
    default_lesson_minutes INTEGER CHECK (default_lesson_minutes IN (40, 60, 90, 120)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rule_sets (
    id TEXT PRIMARY KEY,
    document_type TEXT NOT NULL CHECK (document_type IN ('lecture', 'exercise', 'homework', 'exam', 'knowledge_card', 'teacher_knowledge_file', 'analysis_report')),
    purpose TEXT NOT NULL,
    grade_scope TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_type, purpose, grade_scope, version)
);

CREATE TABLE IF NOT EXISTS production_requests (
    id TEXT PRIMARY KEY,
    raw_request TEXT NOT NULL,
    request_type TEXT NOT NULL,
    parsed_spec_json TEXT NOT NULL DEFAULT '{}',
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    clarification_json TEXT NOT NULL DEFAULT '[]',
    class_id TEXT REFERENCES classes(id),
    status TEXT NOT NULL CHECK (status IN ('received', 'parsed', 'clarification_required', 'planned', 'selecting', 'selected', 'generating', 'validating', 'passed', 'delivered', 'blocked', 'failed', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS selection_plans (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES production_requests(id) ON DELETE CASCADE,
    ruleset_id TEXT NOT NULL REFERENCES rule_sets(id),
    filters_json TEXT NOT NULL,
    requirements_json TEXT NOT NULL,
    shortages_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('draft', 'ready', 'blocked', 'voided')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS selection_plan_questions (
    selection_plan_id TEXT NOT NULL REFERENCES selection_plans(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES questions(id),
    section TEXT NOT NULL,
    layer TEXT,
    sort_order INTEGER NOT NULL,
    selection_reason TEXT NOT NULL,
    PRIMARY KEY (selection_plan_id, question_id)
);

CREATE TABLE IF NOT EXISTS teaching_documents (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES production_requests(id),
    selection_plan_id TEXT REFERENCES selection_plans(id),
    ruleset_id TEXT NOT NULL REFERENCES rule_sets(id),
    class_id TEXT REFERENCES classes(id),
    document_type TEXT NOT NULL,
    audience TEXT NOT NULL CHECK (audience IN ('student', 'teacher', 'internal')),
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK (status IN ('draft', 'pending_review', 'passed', 'delivered', 'blocked', 'voided')),
    output_path TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(request_id, document_type, audience, version)
);

CREATE TABLE IF NOT EXISTS document_questions (
    document_id TEXT NOT NULL REFERENCES teaching_documents(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES questions(id),
    section TEXT NOT NULL,
    layer TEXT,
    sort_order INTEGER NOT NULL,
    PRIMARY KEY (document_id, question_id)
);

CREATE TABLE IF NOT EXISTS question_usage (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id),
    class_id TEXT NOT NULL REFERENCES classes(id),
    document_id TEXT NOT NULL REFERENCES teaching_documents(id),
    section TEXT,
    usage_type TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0 CHECK (delivered IN (0, 1)),
    reuse_allowed INTEGER NOT NULL DEFAULT 0 CHECK (reuse_allowed IN (0, 1)),
    reuse_reason TEXT,
    used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(question_id, class_id, document_id)
);

CREATE TABLE IF NOT EXISTS quality_reports (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES teaching_documents(id) ON DELETE CASCADE,
    data_gate TEXT NOT NULL CHECK (data_gate IN ('pass', 'fail', 'not_run')),
    rule_gate TEXT NOT NULL CHECK (rule_gate IN ('pass', 'fail', 'not_run')),
    fidelity_gate TEXT NOT NULL CHECK (fidelity_gate IN ('pass', 'fail', 'not_run')),
    artifact_gate TEXT NOT NULL CHECK (artifact_gate IN ('pass', 'fail', 'not_run')),
    findings_json TEXT NOT NULL DEFAULT '[]',
    blockers_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('pass', 'blocked', 'failed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_curriculum_textbook ON curriculum_nodes(textbook_id, grade_level, sequence);
CREATE INDEX IF NOT EXISTS idx_questions_quality ON questions(quality_status, review_status, stage, grade_level);
CREATE INDEX IF NOT EXISTS idx_questions_source ON questions(source_document_id, source_page);
CREATE INDEX IF NOT EXISTS idx_question_assets_question ON question_assets(question_id, status);
CREATE INDEX IF NOT EXISTS idx_question_usage_class_delivery ON question_usage(class_id, question_id, delivered);
CREATE INDEX IF NOT EXISTS idx_documents_class_status ON teaching_documents(class_id, status);
CREATE INDEX IF NOT EXISTS idx_requests_class_status ON production_requests(class_id, status);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('v2-initial-2026-07-25');
