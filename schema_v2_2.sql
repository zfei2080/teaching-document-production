PRAGMA foreign_keys = ON;

-- Immutable review decisions for source-faithful candidates.
CREATE TABLE IF NOT EXISTS question_reviews (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    review_type TEXT NOT NULL CHECK (review_type IN ('math_correctness', 'teaching_fit', 'textbook_scope', 'asset_match', 'copyright_source')),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'needs_revision')),
    reviewer TEXT NOT NULL,
    rationale TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_question_reviews_question_type
ON question_reviews(question_id, review_type, reviewed_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('v2.2-question-review-audit-2026-07-25');
