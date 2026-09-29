-- schema_v2_20.sql: classifier-runs-and-classification-provenance
-- Adds classifier_runs table and classification provenance columns to
-- question_textbooks and question_knowledge_points.

CREATE TABLE IF NOT EXISTS classifier_runs (
    id TEXT PRIMARY KEY,
    run_kind TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    confidence_threshold REAL NOT NULL DEFAULT 0.75,
    input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    importer_id TEXT NOT NULL DEFAULT 'auto_classifier',
    importer_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

ALTER TABLE question_textbooks ADD COLUMN classifier_run_id TEXT;
ALTER TABLE question_textbooks ADD COLUMN confidence REAL;
ALTER TABLE question_textbooks ADD COLUMN classification_method TEXT;

ALTER TABLE question_knowledge_points ADD COLUMN classifier_run_id TEXT;
ALTER TABLE question_knowledge_points ADD COLUMN confidence REAL;
ALTER TABLE question_knowledge_points ADD COLUMN classification_method TEXT;

-- v2.10/v2.11 guarded only UPDATE.  SQLite INSERT OR REPLACE performs an
-- insert path and could therefore bypass those guards.  Keep trusted catalog
-- release approval untouched, but require the same source-bound evidence for
-- every direct insertion of an approved question or knowledge-point mapping.
CREATE TRIGGER IF NOT EXISTS trg_question_textbook_insert_requires_approved_mapping_audit
BEFORE INSERT ON question_textbooks
FOR EACH ROW WHEN NEW.fit_status = 'approved'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM question_textbook_imports qti
        JOIN controlled_import_runs cir ON cir.id = qti.import_run_id
        JOIN question_mapping_audits qma ON qma.import_run_id = qti.import_run_id
        WHERE qti.question_id = NEW.question_id
          AND qti.textbook_id = NEW.textbook_id
          AND qti.curriculum_node_id = NEW.curriculum_node_id
          AND qma.status = 'approved'
          AND qma.mapping_hash = qti.mapping_hash
          AND qma.source_reference = cir.source_reference
          AND qma.source_hash = cir.source_hash
          AND cir.import_kind = 'question_mapping'
          AND cir.status = 'approved'
    ) THEN RAISE(ABORT, 'question textbook mapping requires approved source-bound mapping audit') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_knowledge_point_insert_requires_approved_knowledge_audit
BEFORE INSERT ON knowledge_points
FOR EACH ROW WHEN NEW.review_status = 'approved'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM knowledge_point_imports kpi
        JOIN controlled_knowledge_import_runs kir ON kir.id = kpi.import_run_id
        JOIN knowledge_mapping_audits kma ON kma.import_run_id = kir.id
        WHERE kpi.knowledge_point_id = NEW.id
          AND kir.status = 'approved'
          AND kma.status = 'approved'
          AND kma.source_reference = kir.source_reference
          AND kma.source_hash = kir.source_hash
    ) THEN RAISE(ABORT, 'knowledge point requires approved source-bound knowledge audit') END;
END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.20', datetime('now'));
