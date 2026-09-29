PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS question_auto_mapping_audits (
    id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL REFERENCES question_curriculum_mapping_evidence(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    textbook_id TEXT NOT NULL REFERENCES textbooks(id) ON DELETE CASCADE,
    curriculum_node_id TEXT NOT NULL REFERENCES curriculum_nodes(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('pass', 'blocked')),
    blocked_reason TEXT,
    question_input_hash TEXT NOT NULL,
    question_snapshot_revision INTEGER NOT NULL CHECK (question_snapshot_revision >= 0),
    evidence_input_hash TEXT NOT NULL,
    evidence_question_snapshot_revision INTEGER NOT NULL CHECK (evidence_question_snapshot_revision >= 0),
    textbook_catalog_version TEXT NOT NULL,
    node_catalog_version TEXT NOT NULL,
    validator_bundle_id TEXT NOT NULL,
    validator_bundle_version TEXT NOT NULL,
    validator_results_hash TEXT NOT NULL,
    validator_results_json TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    audit_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((status = 'pass' AND blocked_reason IS NULL) OR (status = 'blocked' AND TRIM(COALESCE(blocked_reason, '')) <> '')),
    UNIQUE(question_id, question_input_hash, status),
    UNIQUE(evidence_id, question_input_hash)
);

CREATE INDEX IF NOT EXISTS idx_question_auto_mapping_audits_question
ON question_auto_mapping_audits(question_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_question_auto_mapping_audits_evidence
ON question_auto_mapping_audits(evidence_id, created_at DESC);

CREATE VIEW IF NOT EXISTS current_question_auto_mapping_audits AS
SELECT a.*
FROM question_auto_mapping_audits a
JOIN question_curriculum_mapping_evidence e ON e.id = a.evidence_id
JOIN question_input_snapshots s ON s.question_id = a.question_id
JOIN questions q ON q.id = a.question_id
JOIN source_documents sd ON sd.id = q.source_document_id
JOIN textbooks t ON t.id = a.textbook_id
JOIN curriculum_nodes n ON n.id = a.curriculum_node_id
JOIN catalog_releases cr
  ON cr.textbook_id = t.id AND cr.catalog_version = t.catalog_version
WHERE a.status = 'pass'
  AND s.input_hash = a.question_input_hash
  AND s.revision = a.question_snapshot_revision
  AND s.invalidated_at IS NULL
  AND e.question_id = a.question_id
  AND e.textbook_id = a.textbook_id
  AND e.curriculum_node_id = a.curriculum_node_id
  AND e.status = 'candidate'
  AND e.invalidated_at IS NULL
  AND e.question_input_hash = a.evidence_input_hash
  AND e.question_snapshot_revision = a.evidence_question_snapshot_revision
  AND e.question_input_hash = a.question_input_hash
  AND e.question_snapshot_revision = a.question_snapshot_revision
  AND t.catalog_version = a.textbook_catalog_version
  AND n.catalog_version = a.node_catalog_version
  AND n.textbook_id = a.textbook_id
  AND sd.trusted_source = 1
  AND cr.status = 'approved'
  AND EXISTS (
      SELECT 1
      FROM catalog_audits ca
      JOIN controlled_import_runs cir ON cir.id = ca.import_run_id
      WHERE ca.catalog_release_id = cr.id
        AND ca.status = 'approved'
        AND ca.source_reference = cr.source_reference
        AND ca.source_hash = cr.source_hash
        AND cir.import_kind = 'catalog'
        AND cir.status = 'validated'
        AND cir.source_reference = cr.source_reference
        AND cir.source_hash = cr.source_hash
  )
  AND NOT EXISTS (
      SELECT 1
      FROM question_curriculum_mapping_evidence other
      WHERE other.id <> e.id
        AND other.question_id = a.question_id
        AND other.question_input_hash = a.question_input_hash
        AND other.status = 'candidate'
        AND other.invalidated_at IS NULL
        AND (other.textbook_id <> a.textbook_id OR other.curriculum_node_id <> a.curriculum_node_id)
  )
  AND NOT EXISTS (
      SELECT 1
      FROM json_each(a.validator_results_json) AS item
      WHERE json_extract(item.value, '$.bundle_id') IS NULL
         OR json_extract(item.value, '$.bundle_version') IS NULL
         OR json_extract(item.value, '$.validator_id') IS NULL
         OR json_extract(item.value, '$.validator_version') IS NULL
         OR json_extract(item.value, '$.status') <> 'pass'
         OR json_extract(item.value, '$.input_hash') <> a.question_input_hash
  );

CREATE TRIGGER IF NOT EXISTS trg_question_auto_mapping_audits_insert_requires_current_candidate
BEFORE INSERT ON question_auto_mapping_audits
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM question_curriculum_mapping_evidence e
        JOIN question_input_snapshots s ON s.question_id = e.question_id
        JOIN questions q ON q.id = e.question_id
        JOIN source_documents sd ON sd.id = q.source_document_id
        JOIN textbooks t ON t.id = e.textbook_id
        JOIN curriculum_nodes n ON n.id = e.curriculum_node_id
        JOIN catalog_releases cr
          ON cr.textbook_id = t.id AND cr.catalog_version = t.catalog_version
        WHERE e.id = NEW.evidence_id
          AND e.status = 'candidate'
          AND e.invalidated_at IS NULL
          AND e.question_id = NEW.question_id
          AND e.textbook_id = NEW.textbook_id
          AND e.curriculum_node_id = NEW.curriculum_node_id
          AND e.question_input_hash = NEW.evidence_input_hash
          AND e.question_snapshot_revision = NEW.evidence_question_snapshot_revision
          AND e.question_input_hash = NEW.question_input_hash
          AND e.question_snapshot_revision = NEW.question_snapshot_revision
          AND s.input_hash = NEW.question_input_hash
          AND s.revision = NEW.question_snapshot_revision
          AND s.invalidated_at IS NULL
          AND sd.trusted_source = 1
          AND t.catalog_version = NEW.textbook_catalog_version
          AND n.catalog_version = NEW.node_catalog_version
          AND n.textbook_id = NEW.textbook_id
          AND cr.status = 'approved'
          AND EXISTS (
              SELECT 1
              FROM catalog_audits ca
              JOIN controlled_import_runs cir ON cir.id = ca.import_run_id
              WHERE ca.catalog_release_id = cr.id
                AND ca.status = 'approved'
                AND ca.source_reference = cr.source_reference
                AND ca.source_hash = cr.source_hash
                AND cir.import_kind = 'catalog'
                AND cir.status = 'validated'
                AND cir.source_reference = cr.source_reference
                AND cir.source_hash = cr.source_hash
          )
    ) THEN RAISE(ABORT, 'auto mapping audit requires current candidate evidence binding') END;
    SELECT CASE WHEN NEW.status = 'pass' AND EXISTS (
        SELECT 1
        FROM question_curriculum_mapping_evidence other
        WHERE other.question_id = NEW.question_id
          AND other.question_input_hash = NEW.question_input_hash
          AND other.status = 'candidate'
          AND other.invalidated_at IS NULL
          AND other.id <> NEW.evidence_id
          AND (other.textbook_id <> NEW.textbook_id OR other.curriculum_node_id <> NEW.curriculum_node_id)
    ) THEN RAISE(ABORT, 'auto mapping audit pass requires a single current candidate mapping') END;
    SELECT CASE WHEN NEW.status = 'pass' AND EXISTS (
        SELECT 1
        FROM question_auto_mapping_audits existing
        WHERE existing.question_id = NEW.question_id
          AND existing.question_input_hash = NEW.question_input_hash
          AND existing.status = 'pass'
    ) THEN RAISE(ABORT, 'auto mapping audit pass already exists for current question input') END;
    SELECT CASE WHEN NEW.validator_bundle_id = '' OR NEW.validator_bundle_version = ''
      THEN RAISE(ABORT, 'auto mapping audit requires validator bundle identity') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_question_auto_mapping_audits_immutable
BEFORE UPDATE ON question_auto_mapping_audits
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'question auto mapping audits are append-only');
END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.23-question-auto-mapping-audits', datetime('now'));
