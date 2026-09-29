PRAGMA foreign_keys = ON;

-- P0-3: every automatic verification is valid only for the exact, current
-- admission-input snapshot.  NULL means invalidated: it is a deliberate
-- fail-closed state and cannot support approval.
CREATE TABLE IF NOT EXISTS question_input_snapshots (
    question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    input_hash TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    calculated_at TEXT,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    CHECK ((input_hash IS NULL AND invalidated_at IS NOT NULL)
        OR (input_hash IS NOT NULL AND invalidated_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_question_input_snapshots_hash
ON question_input_snapshots(question_id, input_hash);

-- Each invalidation also isolates every affected question.  The application may
-- later calculate a fresh snapshot and rerun all five validators, but neither
-- an old pass nor a directly reattached association can retain approval.
CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_question_inputs
AFTER UPDATE OF stem, options_json, answer, analysis, question_type, difficulty,
                stage, grade_level, source_document_id, source_fragment_id,
                source_question_no, source_page, content_hash, extraction_status ON questions
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='question_input_changed'
     WHERE question_id=NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_source_document_update
AFTER UPDATE OF relative_path, file_hash, file_type, source_label, copyright_status, parse_status ON source_documents
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='source_document_changed'
     WHERE question_id IN (SELECT id FROM questions WHERE source_document_id=NEW.id);
    UPDATE questions SET quality_status='blocked', review_status='pending'
     WHERE source_document_id=NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_source_fragment_update
AFTER UPDATE OF source_document_id, location_type, page_number, paragraph_index,
                question_number, raw_text, raw_hash ON source_fragments
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='source_fragment_changed'
     WHERE question_id IN (
        SELECT question_id FROM question_source_fragments WHERE source_fragment_id=NEW.id
        UNION SELECT question_id FROM question_assets WHERE source_fragment_id=NEW.id
        UNION SELECT id FROM questions WHERE source_fragment_id=NEW.id
     );
    UPDATE questions SET quality_status='blocked', review_status='pending'
     WHERE id IN (
        SELECT question_id FROM question_source_fragments WHERE source_fragment_id=NEW.id
        UNION SELECT question_id FROM question_assets WHERE source_fragment_id=NEW.id
        UNION SELECT id FROM questions WHERE source_fragment_id=NEW.id
     );
END;

CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_provenance_insert
AFTER INSERT ON question_source_fragments
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='source_provenance_changed' WHERE question_id=NEW.question_id;
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id=NEW.question_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_provenance_update
AFTER UPDATE ON question_source_fragments
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='source_provenance_changed'
     WHERE question_id IN (OLD.question_id, NEW.question_id);
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id IN (OLD.question_id, NEW.question_id);
END;
CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_provenance_delete
AFTER DELETE ON question_source_fragments
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='source_provenance_changed' WHERE question_id=OLD.question_id;
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id=OLD.question_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_asset_insert
AFTER INSERT ON question_assets
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='asset_changed' WHERE question_id=NEW.question_id;
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id=NEW.question_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_asset_update
AFTER UPDATE ON question_assets
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='asset_changed'
     WHERE question_id IN (OLD.question_id, NEW.question_id);
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id IN (OLD.question_id, NEW.question_id);
END;
CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_asset_delete
AFTER DELETE ON question_assets
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='asset_changed' WHERE question_id=OLD.question_id;
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id=OLD.question_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_textbook_mapping_insert
AFTER INSERT ON question_textbooks
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='textbook_mapping_changed' WHERE question_id=NEW.question_id;
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id=NEW.question_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_textbook_mapping_update
AFTER UPDATE ON question_textbooks
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='textbook_mapping_changed'
     WHERE question_id IN (OLD.question_id, NEW.question_id);
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id IN (OLD.question_id, NEW.question_id);
END;
CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_textbook_mapping_delete
AFTER DELETE ON question_textbooks
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='textbook_mapping_changed' WHERE question_id=OLD.question_id;
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id=OLD.question_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_knowledge_mapping_insert
AFTER INSERT ON question_knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='knowledge_mapping_changed' WHERE question_id=NEW.question_id;
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id=NEW.question_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_knowledge_mapping_update
AFTER UPDATE ON question_knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='knowledge_mapping_changed'
     WHERE question_id IN (OLD.question_id, NEW.question_id);
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id IN (OLD.question_id, NEW.question_id);
END;
CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_knowledge_mapping_delete
AFTER DELETE ON question_knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='knowledge_mapping_changed' WHERE question_id=OLD.question_id;
    UPDATE questions SET quality_status='blocked', review_status='pending' WHERE id=OLD.question_id;
END;

-- A mapping's meaning also changes when the controlled textbook/node or
-- knowledge-point record it names changes. These changes invalidate all
-- affected snapshots rather than relying on callers to remember a rerun.
CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_textbook_update
AFTER UPDATE OF name, subject, publisher, status, catalog_version ON textbooks
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='textbook_changed'
     WHERE question_id IN (SELECT question_id FROM question_textbooks WHERE textbook_id=NEW.id);
    UPDATE questions SET quality_status='blocked', review_status='pending'
     WHERE id IN (SELECT question_id FROM question_textbooks WHERE textbook_id=NEW.id);
END;

CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_curriculum_node_update
AFTER UPDATE OF textbook_id, parent_id, stage, grade_level, node_type, name, sequence,
                catalog_version, status ON curriculum_nodes
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='curriculum_node_changed'
     WHERE question_id IN (SELECT question_id FROM question_textbooks WHERE curriculum_node_id=NEW.id);
    UPDATE questions SET quality_status='blocked', review_status='pending'
     WHERE id IN (SELECT question_id FROM question_textbooks WHERE curriculum_node_id=NEW.id);
END;

CREATE TRIGGER IF NOT EXISTS trg_snapshot_invalidate_knowledge_point_update
AFTER UPDATE OF canonical_name, knowledge_type, stage_scope, definition_text, formulas_json,
                properties_json, conditions_json, common_errors_json, version, review_status ON knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_input_snapshots SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='knowledge_point_changed'
     WHERE question_id IN (SELECT question_id FROM question_knowledge_points WHERE knowledge_point_id=NEW.id);
    UPDATE questions SET quality_status='blocked', review_status='pending'
     WHERE id IN (SELECT question_id FROM question_knowledge_points WHERE knowledge_point_id=NEW.id);
END;

-- The admission predicate replaces the P0-2a "latest pass" rule.  A pass is
-- usable only when it was recorded for the currently persisted valid snapshot.
DROP TRIGGER IF EXISTS trg_questions_insert_approved_requires_automatic_evidence;
DROP TRIGGER IF EXISTS trg_questions_approval_requires_automatic_evidence;

CREATE TRIGGER IF NOT EXISTS trg_questions_insert_approved_requires_current_snapshot_evidence
BEFORE INSERT ON questions
FOR EACH ROW WHEN NEW.quality_status='approved' OR NEW.review_status='approved'
BEGIN
    SELECT CASE WHEN NEW.quality_status <> 'approved' OR NEW.review_status <> 'approved'
      THEN RAISE(ABORT, 'approved question requires matching quality and review status') END;
    SELECT RAISE(ABORT, 'approved question requires a persisted current input snapshot');
END;

CREATE TRIGGER IF NOT EXISTS trg_questions_approval_requires_current_snapshot_evidence
BEFORE UPDATE OF quality_status, review_status ON questions
FOR EACH ROW WHEN NEW.quality_status='approved' OR NEW.review_status='approved'
BEGIN
    SELECT CASE WHEN NEW.quality_status <> 'approved' OR NEW.review_status <> 'approved'
      THEN RAISE(ABORT, 'approved question requires matching quality and review status') END;
    SELECT CASE WHEN NOT EXISTS (
      SELECT 1 FROM question_input_snapshots s
       WHERE s.question_id=NEW.id AND s.input_hash IS NOT NULL AND s.invalidated_at IS NULL
    ) THEN RAISE(ABORT, 'approved question requires a current input snapshot') END;
    SELECT CASE WHEN EXISTS (
      SELECT 1 FROM (
        SELECT 'source_fidelity' AS verification_type UNION ALL SELECT 'structural_consistency'
        UNION ALL SELECT 'mathematical_independent' UNION ALL SELECT 'textbook_scope'
        UNION ALL SELECT 'asset_semantics'
      ) required
      WHERE NOT EXISTS (
        SELECT 1 FROM question_verifications v JOIN question_input_snapshots s ON s.question_id=v.question_id
         WHERE v.question_id=NEW.id AND v.verification_type=required.verification_type
           AND v.status='pass' AND v.input_hash=s.input_hash
           AND NOT EXISTS (
             SELECT 1 FROM question_verifications newer
              WHERE newer.question_id=v.question_id AND newer.verification_type=v.verification_type
                AND (newer.verified_at > v.verified_at OR (newer.verified_at=v.verified_at AND newer.rowid>v.rowid))
           )
      )
    ) THEN RAISE(ABORT, 'approved question requires five latest pass records for the current input snapshot') END;
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.14-current-input-snapshot-evidence-2026-07-26');
