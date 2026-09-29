PRAGMA foreign_keys = ON;

-- Draft-only evidence for automatic question-to-curriculum-node decisions.
-- This is deliberately separate from question_textbooks: recording evidence
-- never creates or promotes a mapping, and its conclusion cannot be approved.
CREATE TABLE IF NOT EXISTS question_curriculum_mapping_evidence (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    textbook_id TEXT NOT NULL REFERENCES textbooks(id) ON DELETE CASCADE,
    curriculum_node_id TEXT NOT NULL REFERENCES curriculum_nodes(id) ON DELETE CASCADE,
    question_input_hash TEXT NOT NULL,
    question_snapshot_revision INTEGER NOT NULL CHECK (question_snapshot_revision >= 0),
    textbook_catalog_version TEXT NOT NULL,
    node_catalog_version TEXT NOT NULL,
    feature_hash TEXT NOT NULL,
    features_json TEXT NOT NULL,
    decider_id TEXT NOT NULL,
    decider_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'unsupported', 'rejected')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    reason TEXT NOT NULL CHECK (TRIM(reason) <> ''),
    evidence_hash TEXT NOT NULL UNIQUE,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((invalidated_at IS NULL AND invalidation_reason IS NULL)
        OR (invalidated_at IS NOT NULL AND invalidation_reason IS NOT NULL)),
    UNIQUE(question_id, curriculum_node_id, question_input_hash, feature_hash,
           decider_id, decider_version)
);

CREATE INDEX IF NOT EXISTS idx_question_curriculum_mapping_evidence_question_current
ON question_curriculum_mapping_evidence(question_id, invalidated_at, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_question_curriculum_mapping_evidence_node_current
ON question_curriculum_mapping_evidence(curriculum_node_id, invalidated_at, created_at DESC);

-- Current evidence is an explicit fail-closed query surface.  Historical rows
-- stay auditable, but cannot satisfy a consumer after an input/catalog change.
CREATE VIEW IF NOT EXISTS current_question_curriculum_mapping_evidence AS
SELECT e.*
FROM question_curriculum_mapping_evidence e
JOIN question_input_snapshots s ON s.question_id = e.question_id
JOIN questions q ON q.id = e.question_id
JOIN source_documents sd ON sd.id = q.source_document_id
JOIN textbooks t ON t.id = e.textbook_id
JOIN curriculum_nodes n ON n.id = e.curriculum_node_id
JOIN catalog_releases cr
  ON cr.textbook_id=t.id AND cr.catalog_version=t.catalog_version
WHERE e.invalidated_at IS NULL
  AND s.input_hash = e.question_input_hash
  AND s.revision = e.question_snapshot_revision
  AND s.invalidated_at IS NULL
  AND sd.trusted_source=1
  AND t.catalog_version = e.textbook_catalog_version
  AND n.catalog_version = e.node_catalog_version
  AND n.textbook_id = e.textbook_id
  AND cr.status='approved'
  AND EXISTS (
      SELECT 1
      FROM catalog_audits ca
      JOIN controlled_import_runs cir ON cir.id=ca.import_run_id
      WHERE ca.catalog_release_id=cr.id
        AND ca.status='approved'
        AND ca.source_reference=cr.source_reference
        AND ca.source_hash=cr.source_hash
        AND cir.import_kind='catalog' AND cir.status='validated'
        AND cir.source_reference=cr.source_reference
        AND cir.source_hash=cr.source_hash
  );

-- An evidence row can only be born against the live, persisted snapshot,
-- trusted question source, and an audit-attested approved catalog release at
-- exact catalog/node versions.  It cannot be used to invent a cross-textbook
-- association, even as a draft.
CREATE TRIGGER IF NOT EXISTS trg_mapping_evidence_insert_requires_current_binding
BEFORE INSERT ON question_curriculum_mapping_evidence
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM question_input_snapshots s
        WHERE s.question_id = NEW.question_id
          AND s.input_hash = NEW.question_input_hash
          AND s.revision = NEW.question_snapshot_revision
          AND s.invalidated_at IS NULL
    ) THEN RAISE(ABORT, 'mapping evidence requires current question input snapshot') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM questions q
        JOIN source_documents sd ON sd.id=q.source_document_id
        WHERE q.id=NEW.question_id AND sd.trusted_source=1
    ) THEN RAISE(ABORT, 'mapping evidence requires a trusted question source') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM textbooks t
        JOIN catalog_releases cr
          ON cr.textbook_id=t.id AND cr.catalog_version=t.catalog_version
        WHERE t.id = NEW.textbook_id
          AND t.catalog_version = NEW.textbook_catalog_version
          AND cr.status='approved'
          AND EXISTS (
              SELECT 1
              FROM catalog_audits ca
              JOIN controlled_import_runs cir ON cir.id=ca.import_run_id
              WHERE ca.catalog_release_id=cr.id
                AND ca.status='approved'
                AND ca.source_reference=cr.source_reference
                AND ca.source_hash=cr.source_hash
                AND cir.import_kind='catalog' AND cir.status='validated'
                AND cir.source_reference=cr.source_reference
                AND cir.source_hash=cr.source_hash
          )
    ) THEN RAISE(ABORT, 'mapping evidence requires audited approved catalog release') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM curriculum_nodes n
        WHERE n.id = NEW.curriculum_node_id
          AND n.textbook_id = NEW.textbook_id
          AND n.catalog_version = NEW.node_catalog_version
    ) THEN RAISE(ABORT, 'mapping evidence node/catalog binding is stale or mismatched') END;
END;

-- Decision records are append-only.  The only allowed later change is a
-- database-triggered invalidation, preserving the original decision record.
CREATE TRIGGER IF NOT EXISTS trg_mapping_evidence_binding_is_immutable
BEFORE UPDATE OF question_id, textbook_id, curriculum_node_id, question_input_hash,
                 question_snapshot_revision, textbook_catalog_version,
                 node_catalog_version, feature_hash, features_json, decider_id,
                 decider_version, status, confidence, reason, evidence_hash
ON question_curriculum_mapping_evidence
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping evidence is immutable; record a new decision');
END;

CREATE TRIGGER IF NOT EXISTS trg_mapping_evidence_cannot_be_revalidated
BEFORE UPDATE OF invalidated_at, invalidation_reason ON question_curriculum_mapping_evidence
FOR EACH ROW WHEN OLD.invalidated_at IS NOT NULL AND NEW.invalidated_at IS NULL
BEGIN
    SELECT RAISE(ABORT, 'invalidated mapping evidence cannot become current again');
END;

CREATE TRIGGER IF NOT EXISTS trg_mapping_evidence_invalidate_question_change
AFTER UPDATE ON questions
FOR EACH ROW
BEGIN
    UPDATE question_curriculum_mapping_evidence
       SET invalidated_at = CURRENT_TIMESTAMP,
           invalidation_reason = 'question_changed'
     WHERE question_id = NEW.id AND invalidated_at IS NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_mapping_evidence_invalidate_textbook_change
AFTER UPDATE ON textbooks
FOR EACH ROW
BEGIN
    UPDATE question_curriculum_mapping_evidence
       SET invalidated_at = CURRENT_TIMESTAMP,
           invalidation_reason = 'textbook_catalog_changed'
     WHERE textbook_id = NEW.id AND invalidated_at IS NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_mapping_evidence_invalidate_node_change
AFTER UPDATE ON curriculum_nodes
FOR EACH ROW
BEGIN
    UPDATE question_curriculum_mapping_evidence
       SET invalidated_at = CURRENT_TIMESTAMP,
           invalidation_reason = 'curriculum_node_changed'
     WHERE curriculum_node_id = NEW.id AND invalidated_at IS NULL;
END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.22-draft-question-curriculum-mapping-evidence', datetime('now'));
