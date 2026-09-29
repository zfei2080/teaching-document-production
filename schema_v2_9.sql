PRAGMA foreign_keys = ON;

-- Every question knowledge-point relation used by automatic scope validation
-- must be tied to the same controlled question-mapping import as its concrete
-- textbook/course-node mapping.  This prevents an ad-hoc relation from being
-- combined with an otherwise traceable mapping to manufacture scope evidence.
CREATE TABLE IF NOT EXISTS question_knowledge_point_imports (
    question_id TEXT NOT NULL,
    knowledge_point_id TEXT NOT NULL,
    import_run_id TEXT NOT NULL REFERENCES controlled_import_runs(id),
    mapping_hash TEXT NOT NULL,
    PRIMARY KEY (question_id, knowledge_point_id),
    FOREIGN KEY (question_id, knowledge_point_id)
      REFERENCES question_knowledge_points(question_id, knowledge_point_id)
      ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_question_knowledge_point_imports_run
ON question_knowledge_point_imports(import_run_id);

CREATE TRIGGER IF NOT EXISTS trg_question_knowledge_point_import_kind_insert
BEFORE INSERT ON question_knowledge_point_imports
FOR EACH ROW
BEGIN
    SELECT CASE WHEN (SELECT import_kind FROM controlled_import_runs WHERE id = NEW.import_run_id) <> 'question_mapping'
      THEN RAISE(ABORT, 'question knowledge point requires question_mapping import run') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_question_knowledge_point_import_kind_update
BEFORE UPDATE OF import_run_id ON question_knowledge_point_imports
FOR EACH ROW
BEGIN
    SELECT CASE WHEN (SELECT import_kind FROM controlled_import_runs WHERE id = NEW.import_run_id) <> 'question_mapping'
      THEN RAISE(ABORT, 'question knowledge point requires question_mapping import run') END;
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.9-question-knowledge-point-import-provenance-2026-07-26');
