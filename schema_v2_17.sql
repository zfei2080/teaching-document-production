PRAGMA foreign_keys = ON;

-- P1-4: expand quality gate enums so unsupported/missing remain first-class,
-- audit-explainable blockers instead of being folded into plain fail.
-- SQLite cannot ALTER CHECK constraints in place, so rebuild quality_reports.

DROP TRIGGER IF EXISTS trg_quality_report_insert_guard;
DROP TRIGGER IF EXISTS trg_quality_report_single_per_document;
DROP TRIGGER IF EXISTS trg_delivery_requires_complete_usage;
DROP TRIGGER IF EXISTS trg_document_pass_sets_request_validating_precondition;

CREATE TABLE IF NOT EXISTS quality_reports_v2_17 (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES teaching_documents(id) ON DELETE CASCADE,
    data_gate TEXT NOT NULL CHECK (data_gate IN ('pass', 'fail', 'not_run', 'unsupported', 'missing')),
    rule_gate TEXT NOT NULL CHECK (rule_gate IN ('pass', 'fail', 'not_run', 'unsupported', 'missing')),
    fidelity_gate TEXT NOT NULL CHECK (fidelity_gate IN ('pass', 'fail', 'not_run', 'unsupported', 'missing')),
    artifact_gate TEXT NOT NULL CHECK (artifact_gate IN ('pass', 'fail', 'not_run', 'unsupported', 'missing')),
    findings_json TEXT NOT NULL DEFAULT '[]',
    blockers_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('pass', 'blocked', 'failed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO quality_reports_v2_17 (
    id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate,
    findings_json, blockers_json, status, created_at
)
SELECT
    id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate,
    findings_json, blockers_json, status, created_at
FROM quality_reports;

DROP TABLE quality_reports;
ALTER TABLE quality_reports_v2_17 RENAME TO quality_reports;

CREATE TRIGGER trg_quality_report_insert_guard
BEFORE INSERT ON quality_reports
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM teaching_documents td
        WHERE td.id = NEW.document_id AND td.status = 'pending_review'
    ) THEN RAISE(ABORT, 'quality report requires pending review document') END;
    SELECT CASE WHEN NEW.status = 'pass' AND (
        NEW.data_gate <> 'pass' OR NEW.rule_gate <> 'pass' OR NEW.fidelity_gate <> 'pass' OR NEW.artifact_gate <> 'pass'
    ) THEN RAISE(ABORT, 'passing quality report requires all gates pass') END;
    SELECT CASE WHEN NEW.status <> 'pass' AND (
        NEW.data_gate = 'pass' AND NEW.rule_gate = 'pass' AND NEW.fidelity_gate = 'pass' AND NEW.artifact_gate = 'pass'
    ) THEN RAISE(ABORT, 'all-pass gates must use pass report status') END;
END;

CREATE TRIGGER trg_quality_report_single_per_document
BEFORE INSERT ON quality_reports
FOR EACH ROW
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM quality_reports qr WHERE qr.document_id = NEW.document_id
    ) THEN RAISE(ABORT, 'quality report already exists for document') END;
END;

CREATE TRIGGER trg_delivery_requires_complete_usage
BEFORE UPDATE OF status ON teaching_documents
FOR EACH ROW WHEN NEW.status = 'delivered'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM quality_reports qr
        WHERE qr.document_id = NEW.id AND qr.status = 'pass'
    ) THEN RAISE(ABORT, 'delivered document requires passing quality report') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM document_questions dq WHERE dq.document_id = NEW.id
    ) THEN RAISE(ABORT, 'delivered document requires document questions') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM document_questions dq
        WHERE dq.document_id = NEW.id
          AND NOT EXISTS (
              SELECT 1 FROM question_usage qu
              WHERE qu.document_id = NEW.id AND qu.question_id = dq.question_id AND qu.delivered = 1
          )
    ) THEN RAISE(ABORT, 'delivered document requires usage rows for every document question') END;
    SELECT CASE WHEN (
        SELECT COUNT(*) FROM question_usage qu WHERE qu.document_id = NEW.id AND qu.delivered = 1
    ) <> (
        SELECT COUNT(*) FROM document_questions dq WHERE dq.document_id = NEW.id
    ) THEN RAISE(ABORT, 'delivered document usage count must match document questions') END;
END;

CREATE TRIGGER trg_document_pass_sets_request_validating_precondition
BEFORE UPDATE OF status ON teaching_documents
FOR EACH ROW WHEN NEW.status = 'passed'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM quality_reports qr WHERE qr.document_id = NEW.id AND qr.status = 'pass'
    ) THEN RAISE(ABORT, 'document pass requires passing quality report') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM production_requests pr WHERE pr.id = OLD.request_id AND pr.status = 'validating'
    ) THEN RAISE(ABORT, 'document pass requires validating production request') END;
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.17-quality-gate-unsupported-missing-2026-07-26');
