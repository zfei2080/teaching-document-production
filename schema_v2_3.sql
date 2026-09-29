PRAGMA foreign_keys = ON;

-- Review role distinguishes automated/assistant prechecks from the teacher's
-- required final mathematical confirmation before any student-facing delivery.
ALTER TABLE question_reviews ADD COLUMN reviewer_role TEXT NOT NULL DEFAULT 'assistant_precheck';

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('v2.3-teacher-final-review-2026-07-25');
