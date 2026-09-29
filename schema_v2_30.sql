PRAGMA foreign_keys = ON;

-- P1-4: a current P1-3c mapping approval is an auditable alternative to the
-- legacy textbook-body path. The legacy path remains valid. The alternative
-- requires the same current v2 revision, snapshot, evidence and automatic
-- audit that were required to approve the textbook mapping itself.
DROP TRIGGER IF EXISTS trg_questions_block_approval_without_active_textbook_body_evidence;

CREATE TRIGGER trg_questions_block_approval_without_active_textbook_body_evidence
BEFORE UPDATE OF quality_status, review_status ON questions
FOR EACH ROW WHEN NEW.quality_status='approved' OR NEW.review_status='approved'
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM question_textbooks qt WHERE qt.question_id=NEW.id
    ) AND NOT EXISTS (
        SELECT 1
          FROM question_body_unit_mappings qbm
          JOIN textbook_body_unit_snapshots s ON s.body_unit_id=qbm.body_unit_id
          JOIN textbook_body_units bu ON bu.id=qbm.body_unit_id
          JOIN textbook_sources ts ON ts.id=bu.textbook_source_id
         WHERE qbm.question_id=NEW.id
           AND qbm.status='active'
           AND s.invalidated_at IS NULL
           AND ts.status='active'
           AND qbm.source_file_hash=ts.source_file_hash
    ) AND NOT EXISTS (
        SELECT 1
          FROM question_textbooks qt
          JOIN current_question_mapping_source_revisions r
            ON r.question_id=qt.question_id
           AND r.textbook_id=qt.textbook_id
           AND r.curriculum_node_id=qt.curriculum_node_id
          JOIN question_approval_input_snapshots_v2 s
            ON s.question_id=qt.question_id
          JOIN current_question_mapping_approval_evidence_v2 e
            ON e.question_id=qt.question_id
           AND e.textbook_id=qt.textbook_id
           AND e.curriculum_node_id=qt.curriculum_node_id
           AND e.source_revision_id=r.id
           AND e.approval_input_hash=s.input_hash
           AND e.approval_snapshot_revision=s.revision
          JOIN current_question_mapping_auto_audits_v2 aa
            ON aa.evidence_id=e.id
           AND aa.source_revision_id=r.id
           AND aa.approval_input_hash=s.input_hash
           AND aa.approval_snapshot_revision=s.revision
          JOIN question_mapping_approval_audits_v2 a
            ON a.source_revision_id=r.id
           AND a.evidence_id=e.id
           AND a.auto_audit_id=aa.id
           AND a.approval_input_hash=s.input_hash
           AND a.approval_snapshot_revision=s.revision
           AND a.status='approved'
         WHERE qt.question_id=NEW.id
           AND qt.fit_status='approved'
           AND s.invalidated_at IS NULL
    ) THEN RAISE(ABORT, 'approved textbook-mapped question requires active textbook body evidence or current P1-3c mapping evidence') END;
END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.30-p14-current-p13c-question-admission-path', datetime('now'));
