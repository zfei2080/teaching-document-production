PRAGMA foreign_keys = ON;

-- Add field-level provenance for source-fidelity verification.
CREATE TABLE IF NOT EXISTS question_source_fragments (
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    source_fragment_id TEXT NOT NULL REFERENCES source_fragments(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL CHECK (field_name IN ('stem', 'options', 'answer', 'knowledge_point', 'analysis', 'asset')),
    source_hash TEXT NOT NULL,
    PRIMARY KEY (question_id, source_fragment_id, field_name)
);

CREATE INDEX IF NOT EXISTS idx_question_source_fragments_question
ON question_source_fragments(question_id, field_name);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('v2.1-field-provenance-2026-07-25');
