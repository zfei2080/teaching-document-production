PRAGMA foreign_keys = ON;

-- P1-4: bind deliverability to exact current content, question set, snapshot state,
-- and validated artifact evidence. Delivery remains fail-closed.

CREATE TABLE IF NOT EXISTS artifact_validation_evidence (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES teaching_documents(id) ON DELETE CASCADE,
    quality_report_id TEXT NOT NULL REFERENCES quality_reports(id) ON DELETE CASCADE,
    artifact_role TEXT NOT NULL CHECK (artifact_role IN ('docx_student', 'docx_teacher', 'pdf')),
    content_hash TEXT NOT NULL,
    question_set_hash TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    manifest_hash TEXT,
    artifact_hash TEXT,
    gate TEXT NOT NULL CHECK (gate IN ('pass', 'fail', 'not_run', 'unsupported', 'missing')),
    checks_json TEXT NOT NULL DEFAULT '[]',
    findings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_id, quality_report_id, artifact_role),
    CHECK (TRIM(document_id) <> ''),
    CHECK (TRIM(quality_report_id) <> ''),
    CHECK (TRIM(content_hash) <> ''),
    CHECK (TRIM(question_set_hash) <> ''),
    CHECK (TRIM(snapshot_hash) <> '')
);

CREATE INDEX IF NOT EXISTS idx_artifact_validation_evidence_lookup
ON artifact_validation_evidence(document_id, content_hash, question_set_hash, snapshot_hash, gate, created_at DESC, id DESC);

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
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM quality_reports qr
        JOIN artifact_validation_evidence ave ON ave.quality_report_id = qr.id AND ave.document_id = qr.document_id
        WHERE qr.document_id = NEW.id
          AND qr.status = 'pass'
          AND qr.data_gate = 'pass'
          AND qr.rule_gate = 'pass'
          AND qr.fidelity_gate = 'pass'
          AND qr.artifact_gate = 'pass'
          AND ave.gate = 'pass'
          AND ave.content_hash = COALESCE(NEW.content_hash, '')
          AND ave.question_set_hash = (
              SELECT group_concat(question_id, '|')
              FROM (
                  SELECT dq2.question_id AS question_id
                  FROM document_questions dq2
                  WHERE dq2.document_id = NEW.id
                  ORDER BY dq2.sort_order ASC, dq2.question_id ASC
              )
          )
          AND ave.snapshot_hash = (
              SELECT group_concat(question_id || ':' || input_hash, '|')
              FROM (
                  SELECT dq3.question_id AS question_id, qis.input_hash AS input_hash
                  FROM document_questions dq3
                  JOIN question_input_snapshots qis ON qis.question_id = dq3.question_id
                  WHERE dq3.document_id = NEW.id
                    AND qis.input_hash IS NOT NULL
                    AND qis.invalidated_at IS NULL
                  ORDER BY dq3.sort_order ASC, dq3.question_id ASC
              )
          )
          AND (
              (NEW.document_type = 'pdf' AND ave.artifact_role = 'pdf')
              OR (NEW.document_type <> 'pdf' AND ave.artifact_role = ('docx_' || NEW.audience))
          )
          AND qr.created_at = (
              SELECT MAX(qr2.created_at)
              FROM quality_reports qr2
              WHERE qr2.document_id = NEW.id AND qr2.status = 'pass'
          )
    ) THEN RAISE(ABORT, 'delivered document requires matching passing artifact evidence for current content/question set/snapshot') END;
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.19-delivery-quality-gate-binding-2026-07-26');
