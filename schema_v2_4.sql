PRAGMA foreign_keys = ON;

-- Automated evidence records are the primary path to delivery eligibility.
-- Unsupported or failed verification remains isolated and can never approve a question.
CREATE TABLE IF NOT EXISTS question_verifications (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    verification_type TEXT NOT NULL CHECK (verification_type IN ('source_fidelity', 'structural_consistency', 'mathematical_independent', 'textbook_scope', 'asset_semantics')),
    validator_id TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass', 'fail', 'unsupported')),
    computed_answer TEXT,
    evidence_json TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_question_verifications_question_type
ON question_verifications(question_id, verification_type, verified_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('v2.4-automatic-verification-2026-07-25');
