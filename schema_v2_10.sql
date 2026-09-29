PRAGMA foreign_keys = ON;

-- A question-mapping import becomes eligible for automatic textbook-scope
-- validation only after a reproducible, source-bound audit.  The audit is a
-- controlled batch attestation, never an individual teacher-review bypass.
CREATE TABLE IF NOT EXISTS question_mapping_audits (
    id TEXT PRIMARY KEY,
    import_run_id TEXT NOT NULL UNIQUE REFERENCES controlled_import_runs(id) ON DELETE CASCADE,
    mapping_hash TEXT NOT NULL,
    audit_manifest_hash TEXT NOT NULL,
    auditor_id TEXT NOT NULL,
    audit_method TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    findings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(import_run_id, audit_manifest_hash)
);

CREATE INDEX IF NOT EXISTS idx_question_mapping_audits_import_status
ON question_mapping_audits(import_run_id, status);

-- No direct status flip can make a question-to-textbook mapping approved.  It
-- must be bound to an approved audit for its exact mapping batch and source.
CREATE TRIGGER IF NOT EXISTS trg_question_textbook_requires_approved_mapping_audit
BEFORE UPDATE OF fit_status ON question_textbooks
FOR EACH ROW WHEN NEW.fit_status = 'approved' AND OLD.fit_status <> 'approved'
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

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.10-question-mapping-audit-attestation-2026-07-26');
