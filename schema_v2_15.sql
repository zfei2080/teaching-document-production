PRAGMA foreign_keys = ON;

-- P0-4: database-enforced controlled delivery state machine.
-- Fail closed: only approved/current-snapshot-backed selections may generate;
-- only passed quality reports may deliver; delivery and per-question usage must
-- be atomically consistent; duplicate/forged/half-written delivery state is blocked.

DROP TRIGGER IF EXISTS trg_production_request_status_guard;
CREATE TRIGGER trg_production_request_status_guard
BEFORE UPDATE OF status ON production_requests
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN NEW.status = OLD.status THEN NULL
        WHEN OLD.status = 'selecting' AND NEW.status IN ('selected', 'blocked', 'failed', 'cancelled') THEN NULL
        WHEN OLD.status = 'selected' AND NEW.status IN ('generating', 'blocked', 'failed', 'cancelled') THEN NULL
        WHEN OLD.status = 'generating' AND NEW.status IN ('validating', 'blocked', 'failed', 'cancelled') THEN NULL
        WHEN OLD.status = 'validating' AND NEW.status IN ('passed', 'blocked', 'failed', 'cancelled') THEN NULL
        WHEN OLD.status = 'passed' AND NEW.status IN ('delivered', 'blocked', 'failed', 'cancelled') THEN NULL
        ELSE RAISE(ABORT, 'invalid production request status transition')
    END;
    SELECT CASE WHEN NEW.status = 'passed' AND NOT EXISTS (
        SELECT 1 FROM teaching_documents td
        WHERE td.request_id = OLD.id AND td.status = 'passed'
    ) THEN RAISE(ABORT, 'production request pass requires passed document') END;
END;

DROP TRIGGER IF EXISTS trg_selection_plan_question_requires_current_approved_question;
CREATE TRIGGER trg_selection_plan_question_requires_current_approved_question
BEFORE INSERT ON selection_plan_questions
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM questions q
        JOIN question_input_snapshots s ON s.question_id = q.id
        WHERE q.id = NEW.question_id
          AND q.quality_status = 'approved'
          AND q.review_status = 'approved'
          AND s.input_hash IS NOT NULL
          AND s.invalidated_at IS NULL
    ) THEN RAISE(ABORT, 'selection plan question requires approved question with current snapshot') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM question_verifications verification
        WHERE verification.question_id = NEW.question_id
          AND verification.verification_type IN (
              'source_fidelity', 'structural_consistency', 'mathematical_independent',
              'textbook_scope', 'asset_semantics'
          )
          AND verification.rowid = (
              SELECT MAX(later.rowid)
              FROM question_verifications later
              WHERE later.question_id = verification.question_id
                AND later.verification_type = verification.verification_type
          )
          AND (
              verification.status <> 'pass'
              OR verification.input_hash <> (
                  SELECT s2.input_hash FROM question_input_snapshots s2 WHERE s2.question_id = NEW.question_id
              )
          )
    ) THEN RAISE(ABORT, 'selection plan question requires five latest pass records for current snapshot') END;
    SELECT CASE WHEN (
        SELECT COUNT(DISTINCT verification_type)
        FROM question_verifications verification
        WHERE verification.question_id = NEW.question_id
          AND verification.status = 'pass'
          AND verification.input_hash = (
              SELECT s3.input_hash FROM question_input_snapshots s3 WHERE s3.question_id = NEW.question_id
          )
          AND verification.verification_type IN (
              'source_fidelity', 'structural_consistency', 'mathematical_independent',
              'textbook_scope', 'asset_semantics'
          )
          AND verification.rowid = (
              SELECT MAX(later.rowid)
              FROM question_verifications later
              WHERE later.question_id = verification.question_id
                AND later.verification_type = verification.verification_type
          )
    ) <> 5 THEN RAISE(ABORT, 'selection plan question requires five latest pass records for current snapshot') END;
END;

DROP TRIGGER IF EXISTS trg_teaching_document_insert_guard;
CREATE TRIGGER trg_teaching_document_insert_guard
BEFORE INSERT ON teaching_documents
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NEW.status <> 'draft'
        THEN RAISE(ABORT, 'teaching document must start at draft') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM production_requests pr
        WHERE pr.id = NEW.request_id AND pr.status = 'selected'
    ) THEN RAISE(ABORT, 'teaching document requires selected production request') END;
    SELECT CASE WHEN NEW.selection_plan_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM selection_plans sp
        WHERE sp.id = NEW.selection_plan_id
          AND sp.request_id = NEW.request_id
          AND sp.status = 'ready'
    ) THEN RAISE(ABORT, 'teaching document requires ready selection plan') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM selection_plan_questions spq
        WHERE spq.selection_plan_id = NEW.selection_plan_id
    ) THEN RAISE(ABORT, 'teaching document requires at least one selected question') END;
END;

DROP TRIGGER IF EXISTS trg_teaching_document_status_guard;
CREATE TRIGGER trg_teaching_document_status_guard
BEFORE UPDATE OF status ON teaching_documents
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN NEW.status = OLD.status THEN NULL
        WHEN OLD.status = 'draft' AND NEW.status IN ('pending_review', 'blocked', 'voided') THEN NULL
        WHEN OLD.status = 'pending_review' AND NEW.status IN ('passed', 'blocked', 'voided') THEN NULL
        WHEN OLD.status = 'passed' AND NEW.status IN ('delivered', 'blocked', 'voided') THEN NULL
        ELSE RAISE(ABORT, 'invalid teaching document status transition')
    END;
    SELECT CASE WHEN NEW.status = 'pending_review' AND (NEW.content_hash IS NULL OR TRIM(NEW.content_hash) = '')
        THEN RAISE(ABORT, 'pending review document requires content hash') END;
    SELECT CASE WHEN NEW.status = 'delivered' AND OLD.status <> 'passed'
        THEN RAISE(ABORT, 'document may be delivered only after pass') END;
END;

DROP TRIGGER IF EXISTS trg_document_questions_insert_guard;
CREATE TRIGGER trg_document_questions_insert_guard
BEFORE INSERT ON document_questions
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM teaching_documents td
        WHERE td.id = NEW.document_id AND td.status = 'draft'
    ) THEN RAISE(ABORT, 'document questions may only be attached while document is draft') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM teaching_documents td
        JOIN selection_plan_questions spq ON spq.selection_plan_id = td.selection_plan_id
        WHERE td.id = NEW.document_id AND spq.question_id = NEW.question_id
    ) THEN RAISE(ABORT, 'document question must come from its selection plan') END;
END;

DROP TRIGGER IF EXISTS trg_quality_report_insert_guard;
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

DROP TRIGGER IF EXISTS trg_quality_report_single_per_document;
CREATE TRIGGER trg_quality_report_single_per_document
BEFORE INSERT ON quality_reports
FOR EACH ROW
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM quality_reports qr WHERE qr.document_id = NEW.document_id
    ) THEN RAISE(ABORT, 'quality report already exists for document') END;
END;

DROP TRIGGER IF EXISTS trg_question_usage_insert_guard;
CREATE TRIGGER trg_question_usage_insert_guard
BEFORE INSERT ON question_usage
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NEW.delivered <> 1
        THEN RAISE(ABORT, 'question usage rows must be inserted as delivered') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM teaching_documents td
        WHERE td.id = NEW.document_id AND td.status = 'passed'
    ) THEN RAISE(ABORT, 'question usage requires passed document inside delivery transaction') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM teaching_documents td
        WHERE td.id = NEW.document_id AND td.class_id <> NEW.class_id
    ) THEN RAISE(ABORT, 'question usage class must match document class') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM document_questions dq
        WHERE dq.document_id = NEW.document_id AND dq.question_id = NEW.question_id
    ) THEN RAISE(ABORT, 'question usage requires document question membership') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM question_usage qu
        WHERE qu.question_id = NEW.question_id
          AND qu.class_id = NEW.class_id
          AND qu.delivered = 1
    ) AND COALESCE(NEW.reuse_allowed, 0) = 0
    THEN RAISE(ABORT, 'duplicate delivered question requires explicit reuse authorization') END;
    SELECT CASE WHEN COALESCE(NEW.reuse_allowed, 0) = 1 AND (NEW.reuse_reason IS NULL OR TRIM(NEW.reuse_reason) = '')
        THEN RAISE(ABORT, 'reuse_reason is required when reuse_allowed=1') END;
END;

DROP TRIGGER IF EXISTS trg_question_usage_immutable;
CREATE TRIGGER trg_question_usage_immutable
BEFORE UPDATE ON question_usage
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'question usage is immutable after insert');
END;

DROP TRIGGER IF EXISTS trg_question_usage_no_delete;
CREATE TRIGGER trg_question_usage_no_delete
BEFORE DELETE ON question_usage
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'question usage cannot be deleted');
END;

DROP TRIGGER IF EXISTS trg_delivery_requires_complete_usage;
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

DROP TRIGGER IF EXISTS trg_request_delivered_requires_document_delivery;
CREATE TRIGGER trg_request_delivered_requires_document_delivery
BEFORE UPDATE OF status ON production_requests
FOR EACH ROW WHEN NEW.status = 'delivered'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM teaching_documents td
        WHERE td.request_id = NEW.id AND td.status = 'delivered'
    ) THEN RAISE(ABORT, 'production request delivered requires delivered document') END;
END;

DROP TRIGGER IF EXISTS trg_document_pass_sets_request_validating_precondition;
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
VALUES ('v2.15-controlled-delivery-state-machine-2026-07-26');
