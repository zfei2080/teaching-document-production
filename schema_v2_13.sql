PRAGMA foreign_keys = ON;

-- Question approval is a database-enforced state, not an application convention.
-- SQLite has no per-connection authorization model, so this migration makes the
-- evidence predicate authoritative at the data boundary and locks approved
-- inputs/evidence.  P0-3 will replace the "latest record" predicate below with
-- a complete immutable input-snapshot predicate; until then the system remains
-- fail-closed for every candidate without all five latest passing results.

CREATE TRIGGER IF NOT EXISTS trg_questions_insert_approved_requires_automatic_evidence
BEFORE INSERT ON questions
FOR EACH ROW WHEN NEW.quality_status = 'approved' OR NEW.review_status = 'approved'
BEGIN
    SELECT CASE WHEN NEW.quality_status <> 'approved' OR NEW.review_status <> 'approved'
        THEN RAISE(ABORT, 'approved question requires matching quality and review status') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM (
            SELECT 'source_fidelity' AS verification_type
            UNION ALL SELECT 'structural_consistency'
            UNION ALL SELECT 'mathematical_independent'
            UNION ALL SELECT 'textbook_scope'
            UNION ALL SELECT 'asset_semantics'
        ) required
        WHERE NOT EXISTS (
            SELECT 1
            FROM question_verifications v
            WHERE v.question_id = NEW.id
              AND v.verification_type = required.verification_type
              AND v.status = 'pass'
              AND NOT EXISTS (
                  SELECT 1 FROM question_verifications newer
                  WHERE newer.question_id = v.question_id
                    AND newer.verification_type = v.verification_type
                    AND (newer.verified_at > v.verified_at
                         OR (newer.verified_at = v.verified_at AND newer.rowid > v.rowid))
              )
        )
    ) THEN RAISE(ABORT, 'approved question requires five latest automatic pass records') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_questions_approval_requires_automatic_evidence
BEFORE UPDATE OF quality_status, review_status ON questions
FOR EACH ROW WHEN NEW.quality_status = 'approved' OR NEW.review_status = 'approved'
BEGIN
    SELECT CASE WHEN NEW.quality_status <> 'approved' OR NEW.review_status <> 'approved'
        THEN RAISE(ABORT, 'approved question requires matching quality and review status') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM (
            SELECT 'source_fidelity' AS verification_type
            UNION ALL SELECT 'structural_consistency'
            UNION ALL SELECT 'mathematical_independent'
            UNION ALL SELECT 'textbook_scope'
            UNION ALL SELECT 'asset_semantics'
        ) required
        WHERE NOT EXISTS (
            SELECT 1
            FROM question_verifications v
            WHERE v.question_id = NEW.id
              AND v.verification_type = required.verification_type
              AND v.status = 'pass'
              AND NOT EXISTS (
                  SELECT 1 FROM question_verifications newer
                  WHERE newer.question_id = v.question_id
                    AND newer.verification_type = v.verification_type
                    AND (newer.verified_at > v.verified_at
                         OR (newer.verified_at = v.verified_at AND newer.rowid > v.rowid))
              )
        )
    ) THEN RAISE(ABORT, 'approved question requires five latest automatic pass records') END;
END;

-- A caller that needs to alter either source-bound question content or verifier
-- evidence must first isolate the question.  This prevents evidence deletion,
-- downgrade, or input tampering from leaving an apparently approved record.
CREATE TRIGGER IF NOT EXISTS trg_approved_question_locks_approval_inputs
BEFORE UPDATE OF stem, options_json, answer, analysis, question_type, difficulty,
                 stage, grade_level, source_document_id, source_fragment_id,
                 source_question_no, source_page, content_hash, extraction_status ON questions
FOR EACH ROW WHEN OLD.quality_status = 'approved' OR OLD.review_status = 'approved'
BEGIN
    SELECT RAISE(ABORT, 'isolate approved question before changing approval inputs');
END;

CREATE TRIGGER IF NOT EXISTS trg_approved_question_locks_verification_update
BEFORE UPDATE ON question_verifications
FOR EACH ROW WHEN EXISTS (
    SELECT 1 FROM questions q
    WHERE q.id = OLD.question_id
      AND (q.quality_status = 'approved' OR q.review_status = 'approved')
)
BEGIN
    SELECT RAISE(ABORT, 'isolate approved question before changing verification evidence');
END;

CREATE TRIGGER IF NOT EXISTS trg_approved_question_locks_verification_delete
BEFORE DELETE ON question_verifications
FOR EACH ROW WHEN EXISTS (
    SELECT 1 FROM questions q
    WHERE q.id = OLD.question_id
      AND (q.quality_status = 'approved' OR q.review_status = 'approved')
)
BEGIN
    SELECT RAISE(ABORT, 'isolate approved question before deleting verification evidence');
END;

CREATE TRIGGER IF NOT EXISTS trg_approved_question_locks_verification_insert
BEFORE INSERT ON question_verifications
FOR EACH ROW WHEN EXISTS (
    SELECT 1 FROM questions q
    WHERE q.id = NEW.question_id
      AND (q.quality_status = 'approved' OR q.review_status = 'approved')
)
BEGIN
    SELECT RAISE(ABORT, 'isolate approved question before adding verification evidence');
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.13-question-approval-state-machine-2026-07-26');
