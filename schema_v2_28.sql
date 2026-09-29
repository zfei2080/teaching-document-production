PRAGMA foreign_keys = ON;

-- P1-3c keeps mapping-source revisions append-only.  A mutable head points at
-- the current revision while immutable head events preserve every transition.
CREATE TABLE question_mapping_source_revisions (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL,
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    import_run_id TEXT NOT NULL UNIQUE REFERENCES controlled_import_runs(id),
    mapping_hash TEXT NOT NULL CHECK (length(mapping_hash)=64),
    source_reference TEXT NOT NULL,
    source_hash TEXT NOT NULL CHECK (length(source_hash)=64),
    source_file_path TEXT NOT NULL,
    knowledge_points_json TEXT NOT NULL CHECK (json_valid(knowledge_points_json)),
    revision_hash TEXT NOT NULL UNIQUE CHECK (length(revision_hash)=64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(question_id, textbook_id, curriculum_node_id, revision_hash),
    FOREIGN KEY (question_id, textbook_id, curriculum_node_id)
      REFERENCES question_textbooks(question_id, textbook_id, curriculum_node_id)
      ON DELETE RESTRICT
);

CREATE TABLE question_mapping_source_revision_heads (
    question_id TEXT NOT NULL,
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    current_revision_id TEXT NOT NULL REFERENCES question_mapping_source_revisions(id),
    head_revision INTEGER NOT NULL CHECK (head_revision >= 1),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (question_id, textbook_id, curriculum_node_id),
    FOREIGN KEY (question_id, textbook_id, curriculum_node_id)
      REFERENCES question_textbooks(question_id, textbook_id, curriculum_node_id)
      ON DELETE RESTRICT
);

CREATE TABLE question_mapping_source_revision_knowledge_points (
    source_revision_id TEXT NOT NULL REFERENCES question_mapping_source_revisions(id) ON DELETE RESTRICT,
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id),
    relation_type TEXT NOT NULL CHECK (relation_type IN ('primary', 'secondary')),
    PRIMARY KEY (source_revision_id, knowledge_point_id)
);

CREATE TABLE question_mapping_source_revision_head_events (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL,
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    previous_revision_id TEXT REFERENCES question_mapping_source_revisions(id),
    replacement_revision_id TEXT NOT NULL UNIQUE REFERENCES question_mapping_source_revisions(id),
    expected_head_revision INTEGER NOT NULL CHECK (expected_head_revision >= 0),
    reason TEXT NOT NULL CHECK (TRIM(reason) <> ''),
    event_hash TEXT NOT NULL UNIQUE CHECK (length(event_hash)=64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_question_mapping_source_revisions_mapping
ON question_mapping_source_revisions(question_id, textbook_id, curriculum_node_id, created_at);

CREATE INDEX idx_question_mapping_source_revision_knowledge_points_point
ON question_mapping_source_revision_knowledge_points(knowledge_point_id);

CREATE VIEW current_question_mapping_source_revisions AS
SELECT r.*,
       h.head_revision
FROM question_mapping_source_revisions r
JOIN question_mapping_source_revision_heads h
  ON h.current_revision_id=r.id
 AND h.question_id=r.question_id
 AND h.textbook_id=r.textbook_id
 AND h.curriculum_node_id=r.curriculum_node_id
JOIN controlled_import_runs cir ON cir.id=r.import_run_id
WHERE cir.import_kind='question_mapping'
  AND cir.status IN ('validated', 'approved')
  AND cir.source_reference=r.source_reference
  AND cir.source_hash=r.source_hash
  AND EXISTS (
      SELECT 1
      FROM question_mapping_source_revision_knowledge_points rkp
      JOIN knowledge_points kp ON kp.id=rkp.knowledge_point_id
      JOIN curriculum_knowledge_points ckp
        ON ckp.knowledge_point_id=rkp.knowledge_point_id
       AND ckp.curriculum_node_id=r.curriculum_node_id
      WHERE rkp.source_revision_id=r.id
        AND kp.review_status='approved'
  )
  AND NOT EXISTS (
      SELECT 1
      FROM question_mapping_source_revision_knowledge_points rkp
      LEFT JOIN knowledge_points kp ON kp.id=rkp.knowledge_point_id
      LEFT JOIN curriculum_knowledge_points ckp
        ON ckp.knowledge_point_id=rkp.knowledge_point_id
       AND ckp.curriculum_node_id=r.curriculum_node_id
      WHERE rkp.source_revision_id=r.id
        AND (kp.id IS NULL OR kp.review_status <> 'approved' OR ckp.knowledge_point_id IS NULL)
  )
  AND (
      SELECT COUNT(*) FROM question_mapping_source_revision_knowledge_points rkp
       WHERE rkp.source_revision_id=r.id
  ) = (
      SELECT COUNT(*) FROM json_each(r.knowledge_points_json)
  );

CREATE TRIGGER trg_v28_mapping_source_revision_insert_guards
BEFORE INSERT ON question_mapping_source_revisions
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM question_textbooks qt
         WHERE qt.question_id=NEW.question_id
           AND qt.textbook_id=NEW.textbook_id
           AND qt.curriculum_node_id=NEW.curriculum_node_id
           AND qt.fit_status='pending'
    ) THEN RAISE(ABORT, 'mapping source revision requires an existing pending textbook mapping') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM controlled_import_runs cir
         WHERE cir.id=NEW.import_run_id
           AND cir.import_kind='question_mapping'
           AND cir.status='validated'
           AND cir.source_reference=NEW.source_reference
           AND cir.source_hash=NEW.source_hash
    ) THEN RAISE(ABORT, 'mapping source revision requires a validated source-bound import') END;
END;

CREATE TRIGGER trg_v28_mapping_source_revision_immutable_update
BEFORE UPDATE ON question_mapping_source_revisions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping source revisions are immutable');
END;

CREATE TRIGGER trg_v28_mapping_source_revision_immutable_delete
BEFORE DELETE ON question_mapping_source_revisions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping source revisions are append-only');
END;

CREATE TRIGGER trg_v28_mapping_source_revision_knowledge_insert_guards
BEFORE INSERT ON question_mapping_source_revision_knowledge_points
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM question_mapping_source_revisions r
        JOIN knowledge_points kp ON kp.id=NEW.knowledge_point_id
        JOIN curriculum_knowledge_points ckp
          ON ckp.knowledge_point_id=kp.id
         AND ckp.curriculum_node_id=r.curriculum_node_id
        WHERE r.id=NEW.source_revision_id
          AND kp.review_status='approved'
    ) THEN RAISE(ABORT, 'mapping revision knowledge point must be approved and linked to its curriculum node') END;
END;

CREATE TRIGGER trg_v28_mapping_source_revision_knowledge_immutable_update
BEFORE UPDATE ON question_mapping_source_revision_knowledge_points
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping revision knowledge bindings are immutable');
END;

CREATE TRIGGER trg_v28_mapping_source_revision_knowledge_immutable_delete
BEFORE DELETE ON question_mapping_source_revision_knowledge_points
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping revision knowledge bindings are append-only');
END;

CREATE TRIGGER trg_v28_mapping_revision_head_event_insert_guards
BEFORE INSERT ON question_mapping_source_revision_head_events
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM question_mapping_source_revisions r
         WHERE r.id=NEW.replacement_revision_id
           AND r.question_id=NEW.question_id
           AND r.textbook_id=NEW.textbook_id
           AND r.curriculum_node_id=NEW.curriculum_node_id
    ) THEN RAISE(ABORT, 'mapping revision head replacement must match its mapping identity') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM question_mapping_source_revision_knowledge_points
         WHERE source_revision_id=NEW.replacement_revision_id
    ) THEN RAISE(ABORT, 'mapping revision head replacement requires approved knowledge bindings') END;
    SELECT CASE WHEN (
        SELECT COUNT(*) FROM question_mapping_source_revision_knowledge_points
         WHERE source_revision_id=NEW.replacement_revision_id
    ) <> (
        SELECT COUNT(*) FROM json_each((
            SELECT knowledge_points_json FROM question_mapping_source_revisions
             WHERE id=NEW.replacement_revision_id
        ))
    ) THEN RAISE(ABORT, 'mapping revision knowledge bindings must cover the source revision contract') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM json_each((
            SELECT knowledge_points_json FROM question_mapping_source_revisions
             WHERE id=NEW.replacement_revision_id
        )) item
        WHERE json_type(item.value) <> 'object'
           OR NOT EXISTS (
               SELECT 1
               FROM question_mapping_source_revision_knowledge_points rkp
               WHERE rkp.source_revision_id=NEW.replacement_revision_id
                 AND rkp.knowledge_point_id=json_extract(item.value, '$.knowledge_point_id')
                 AND rkp.relation_type=json_extract(item.value, '$.relation_type')
           )
    ) THEN RAISE(ABORT, 'mapping revision knowledge bindings do not match the source revision contract') END;
    SELECT CASE WHEN NEW.previous_revision_id IS NULL AND EXISTS (
        SELECT 1 FROM question_mapping_source_revision_heads h
         WHERE h.question_id=NEW.question_id
           AND h.textbook_id=NEW.textbook_id
           AND h.curriculum_node_id=NEW.curriculum_node_id
    ) THEN RAISE(ABORT, 'first mapping revision head event cannot replace an existing head') END;
    SELECT CASE WHEN NEW.previous_revision_id IS NULL AND NEW.expected_head_revision <> 0
      THEN RAISE(ABORT, 'first mapping revision head event must expect revision zero') END;
    SELECT CASE WHEN NEW.previous_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM question_mapping_source_revision_heads h
         WHERE h.question_id=NEW.question_id
           AND h.textbook_id=NEW.textbook_id
           AND h.curriculum_node_id=NEW.curriculum_node_id
           AND h.current_revision_id=NEW.previous_revision_id
           AND h.head_revision=NEW.expected_head_revision
    ) THEN RAISE(ABORT, 'mapping revision head event does not match the current head') END;
END;

CREATE TRIGGER trg_v28_mapping_revision_head_event_apply
AFTER INSERT ON question_mapping_source_revision_head_events
FOR EACH ROW
BEGIN
    INSERT INTO question_mapping_source_revision_heads
        (question_id, textbook_id, curriculum_node_id, current_revision_id, head_revision)
    SELECT NEW.question_id, NEW.textbook_id, NEW.curriculum_node_id,
           NEW.replacement_revision_id, 1
     WHERE NEW.previous_revision_id IS NULL;
    UPDATE question_mapping_source_revision_heads
       SET current_revision_id=NEW.replacement_revision_id,
           head_revision=head_revision+1,
           updated_at=CURRENT_TIMESTAMP
     WHERE NEW.previous_revision_id IS NOT NULL
       AND question_id=NEW.question_id
       AND textbook_id=NEW.textbook_id
       AND curriculum_node_id=NEW.curriculum_node_id
       AND current_revision_id=NEW.previous_revision_id
       AND head_revision=NEW.expected_head_revision;
END;

CREATE TRIGGER trg_v28_mapping_revision_head_insert_guard
BEFORE INSERT ON question_mapping_source_revision_heads
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM question_mapping_source_revision_head_events e
     WHERE e.question_id=NEW.question_id
       AND e.textbook_id=NEW.textbook_id
       AND e.curriculum_node_id=NEW.curriculum_node_id
       AND e.previous_revision_id IS NULL
       AND e.replacement_revision_id=NEW.current_revision_id
       AND e.expected_head_revision=0
)
BEGIN
    SELECT RAISE(ABORT, 'mapping revision heads are created only through append-only head events');
END;

CREATE TRIGGER trg_v28_mapping_revision_head_event_immutable_update
BEFORE UPDATE ON question_mapping_source_revision_head_events
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping revision head events are immutable');
END;

CREATE TRIGGER trg_v28_mapping_revision_head_event_immutable_delete
BEFORE DELETE ON question_mapping_source_revision_head_events
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping revision head events are append-only');
END;

CREATE TRIGGER trg_v28_mapping_revision_head_update_guard
BEFORE UPDATE ON question_mapping_source_revision_heads
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM question_mapping_source_revision_head_events e
     WHERE e.question_id=NEW.question_id
       AND e.textbook_id=NEW.textbook_id
       AND e.curriculum_node_id=NEW.curriculum_node_id
       AND e.previous_revision_id=OLD.current_revision_id
       AND e.replacement_revision_id=NEW.current_revision_id
       AND e.expected_head_revision=OLD.head_revision
)
BEGIN
    SELECT RAISE(ABORT, 'mapping revision heads change only through append-only head events');
END;

CREATE TRIGGER trg_v28_mapping_revision_head_immutable_delete
BEFORE DELETE ON question_mapping_source_revision_heads
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping revision heads are append-only');
END;

-- This v2 snapshot is the approval boundary.  It deliberately excludes the
-- final fit_status output while retaining all inputs that give a mapping meaning.
CREATE TABLE question_approval_input_snapshots_v2 (
    question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    input_hash TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    calculated_at TEXT,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    CHECK ((input_hash IS NULL AND invalidated_at IS NOT NULL)
        OR (input_hash IS NOT NULL AND invalidated_at IS NULL))
);

CREATE INDEX idx_question_approval_input_snapshots_v2_hash
ON question_approval_input_snapshots_v2(question_id, input_hash);

CREATE TABLE question_mapping_approval_evidence_v2 (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    textbook_id TEXT NOT NULL REFERENCES textbooks(id),
    curriculum_node_id TEXT NOT NULL REFERENCES curriculum_nodes(id),
    source_revision_id TEXT NOT NULL REFERENCES question_mapping_source_revisions(id),
    approval_input_hash TEXT NOT NULL CHECK (length(approval_input_hash)=64),
    approval_snapshot_revision INTEGER NOT NULL CHECK (approval_snapshot_revision >= 0),
    feature_hash TEXT NOT NULL CHECK (length(feature_hash)=64),
    features_json TEXT NOT NULL CHECK (json_valid(features_json)),
    decider_id TEXT NOT NULL,
    decider_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'unsupported', 'rejected')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    reason TEXT NOT NULL CHECK (TRIM(reason) <> ''),
    evidence_hash TEXT NOT NULL UNIQUE CHECK (length(evidence_hash)=64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(question_id, curriculum_node_id, source_revision_id, approval_input_hash,
           feature_hash, decider_id, decider_version)
);

CREATE TABLE question_mapping_auto_audits_v2 (
    id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE REFERENCES question_mapping_approval_evidence_v2(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    textbook_id TEXT NOT NULL REFERENCES textbooks(id),
    curriculum_node_id TEXT NOT NULL REFERENCES curriculum_nodes(id),
    source_revision_id TEXT NOT NULL REFERENCES question_mapping_source_revisions(id),
    approval_input_hash TEXT NOT NULL CHECK (length(approval_input_hash)=64),
    approval_snapshot_revision INTEGER NOT NULL CHECK (approval_snapshot_revision >= 0),
    validation_input_hash TEXT NOT NULL CHECK (length(validation_input_hash)=64),
    validator_results_json TEXT NOT NULL CHECK (json_valid(validator_results_json)),
    audit_status TEXT NOT NULL CHECK (audit_status IN ('pass', 'unsupported', 'fail')),
    findings_json TEXT NOT NULL CHECK (json_valid(findings_json)),
    audit_hash TEXT NOT NULL UNIQUE CHECK (length(audit_hash)=64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE question_mapping_approval_audits_v2 (
    id TEXT PRIMARY KEY,
    source_revision_id TEXT NOT NULL UNIQUE REFERENCES question_mapping_source_revisions(id),
    evidence_id TEXT NOT NULL UNIQUE REFERENCES question_mapping_approval_evidence_v2(id),
    auto_audit_id TEXT NOT NULL UNIQUE REFERENCES question_mapping_auto_audits_v2(id),
    approval_input_hash TEXT NOT NULL CHECK (length(approval_input_hash)=64),
    approval_snapshot_revision INTEGER NOT NULL CHECK (approval_snapshot_revision >= 0),
    audit_manifest_hash TEXT NOT NULL UNIQUE CHECK (length(audit_manifest_hash)=64),
    auditor_id TEXT NOT NULL,
    audit_method TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    findings_json TEXT NOT NULL CHECK (json_valid(findings_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER trg_v28_mapping_approval_evidence_immutable_update
BEFORE UPDATE ON question_mapping_approval_evidence_v2
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping approval evidence is append-only');
END;

CREATE TRIGGER trg_v28_mapping_approval_evidence_immutable_delete
BEFORE DELETE ON question_mapping_approval_evidence_v2
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping approval evidence is append-only');
END;

CREATE TRIGGER trg_v28_mapping_auto_audits_immutable_update
BEFORE UPDATE ON question_mapping_auto_audits_v2
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping automatic audits are append-only');
END;

CREATE TRIGGER trg_v28_mapping_auto_audits_immutable_delete
BEFORE DELETE ON question_mapping_auto_audits_v2
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping automatic audits are append-only');
END;

CREATE TRIGGER trg_v28_mapping_approval_audits_immutable_update
BEFORE UPDATE ON question_mapping_approval_audits_v2
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping approval audits are append-only');
END;

CREATE TRIGGER trg_v28_mapping_approval_audits_immutable_delete
BEFORE DELETE ON question_mapping_approval_audits_v2
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'mapping approval audits are append-only');
END;

CREATE TRIGGER trg_v28_controlled_content_sources_immutable_update
BEFORE UPDATE ON controlled_content_sources
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled content sources are append-only');
END;

CREATE TRIGGER trg_v28_controlled_content_sources_immutable_delete
BEFORE DELETE ON controlled_content_sources
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled content sources are append-only');
END;

CREATE TRIGGER trg_v28_controlled_content_segments_immutable_contract
BEFORE UPDATE OF source_id, segment_id, content_type, layer, textbook_id,
                 curriculum_node_id, allowed_node_ids_json, required_knowledge_json,
                 source_character_range_json, normalized_text_sha256, scope_status
ON controlled_content_segments
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled content segment contracts are immutable; create a new source revision');
END;

CREATE VIEW current_question_mapping_approval_evidence_v2 AS
SELECT e.*
FROM question_mapping_approval_evidence_v2 e
JOIN question_approval_input_snapshots_v2 s ON s.question_id=e.question_id
JOIN current_question_mapping_source_revisions r ON r.id=e.source_revision_id
JOIN questions q ON q.id=e.question_id
JOIN source_documents sd ON sd.id=q.source_document_id
JOIN textbooks t ON t.id=e.textbook_id
JOIN curriculum_nodes n ON n.id=e.curriculum_node_id
JOIN catalog_releases cr ON cr.textbook_id=t.id AND cr.catalog_version=t.catalog_version
WHERE e.status='candidate'
  AND s.input_hash=e.approval_input_hash
  AND s.revision=e.approval_snapshot_revision
  AND s.invalidated_at IS NULL
  AND r.question_id=e.question_id
  AND r.textbook_id=e.textbook_id
  AND r.curriculum_node_id=e.curriculum_node_id
  AND sd.trusted_source=1
  AND t.catalog_version=n.catalog_version
  AND n.textbook_id=t.id
  AND n.status='active'
  AND cr.status='approved'
  AND EXISTS (
      SELECT 1
      FROM catalog_audits ca
      JOIN controlled_import_runs cir ON cir.id=ca.import_run_id
      WHERE ca.catalog_release_id=cr.id
        AND ca.status='approved'
        AND ca.source_reference=cr.source_reference
        AND ca.source_hash=cr.source_hash
        AND cir.import_kind='catalog'
        AND cir.status='validated'
        AND cir.source_reference=cr.source_reference
        AND cir.source_hash=cr.source_hash
  );

CREATE VIEW current_question_mapping_auto_audits_v2 AS
SELECT a.*
FROM question_mapping_auto_audits_v2 a
JOIN current_question_mapping_approval_evidence_v2 e ON e.id=a.evidence_id
JOIN question_approval_input_snapshots_v2 s ON s.question_id=a.question_id
JOIN current_question_mapping_source_revisions r ON r.id=a.source_revision_id
WHERE a.audit_status='pass'
  AND a.question_id=e.question_id
  AND a.textbook_id=e.textbook_id
  AND a.curriculum_node_id=e.curriculum_node_id
  AND a.source_revision_id=e.source_revision_id
  AND a.approval_input_hash=e.approval_input_hash
  AND a.approval_snapshot_revision=e.approval_snapshot_revision
  AND s.input_hash=a.approval_input_hash
  AND s.revision=a.approval_snapshot_revision
  AND s.invalidated_at IS NULL
  AND r.id=a.source_revision_id;

CREATE TRIGGER trg_v28_mapping_approval_evidence_insert_guards
BEFORE INSERT ON question_mapping_approval_evidence_v2
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM question_approval_input_snapshots_v2 s
         JOIN current_question_mapping_source_revisions r ON r.id=NEW.source_revision_id
         JOIN questions q ON q.id=NEW.question_id
         JOIN source_documents sd ON sd.id=q.source_document_id
         WHERE s.question_id=NEW.question_id
           AND s.input_hash=NEW.approval_input_hash
           AND s.revision=NEW.approval_snapshot_revision
           AND s.invalidated_at IS NULL
           AND r.question_id=NEW.question_id
           AND r.textbook_id=NEW.textbook_id
           AND r.curriculum_node_id=NEW.curriculum_node_id
           AND sd.trusted_source=1
    ) THEN RAISE(ABORT, 'mapping approval evidence requires a current v2 approval snapshot and source revision') END;
END;

CREATE TRIGGER trg_v28_mapping_auto_audit_insert_guards
BEFORE INSERT ON question_mapping_auto_audits_v2
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM current_question_mapping_approval_evidence_v2 e
         WHERE e.id=NEW.evidence_id
           AND e.question_id=NEW.question_id
           AND e.textbook_id=NEW.textbook_id
           AND e.curriculum_node_id=NEW.curriculum_node_id
           AND e.source_revision_id=NEW.source_revision_id
           AND e.approval_input_hash=NEW.approval_input_hash
           AND e.approval_snapshot_revision=NEW.approval_snapshot_revision
    ) THEN RAISE(ABORT, 'mapping auto audit requires current v2 approval evidence') END;
    SELECT CASE WHEN NEW.audit_status='pass' AND json_valid(NEW.validator_results_json) <> 1
      THEN RAISE(ABORT, 'mapping auto audit pass requires a validator bundle') END;
    SELECT CASE WHEN NEW.audit_status='pass' AND (
        SELECT COUNT(*) FROM json_each(NEW.validator_results_json)
    ) <> 4 THEN RAISE(ABORT, 'mapping auto audit pass requires exactly four validators') END;
    SELECT CASE WHEN NEW.audit_status='pass' AND EXISTS (
        SELECT 1 FROM json_each(NEW.validator_results_json) item
         WHERE item.key NOT IN ('source_fidelity', 'structural_consistency', 'mathematical_independent', 'asset_semantics')
            OR json_type(item.value) <> 'object'
            OR json_extract(item.value, '$.status') <> 'pass'
            OR json_extract(item.value, '$.input_hash') <> NEW.validation_input_hash
    ) THEN RAISE(ABORT, 'mapping auto audit pass requires four matching validator passes') END;
END;

CREATE TRIGGER trg_v28_mapping_approval_audit_insert_guards
BEFORE INSERT ON question_mapping_approval_audits_v2
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM current_question_mapping_auto_audits_v2 a
         JOIN current_question_mapping_approval_evidence_v2 e ON e.id=a.evidence_id
         JOIN current_question_mapping_source_revisions r ON r.id=a.source_revision_id
         JOIN question_approval_input_snapshots_v2 s ON s.question_id=a.question_id
         JOIN questions q ON q.id=a.question_id
         WHERE a.id=NEW.auto_audit_id
           AND e.id=NEW.evidence_id
           AND r.id=NEW.source_revision_id
           AND s.input_hash=NEW.approval_input_hash
           AND s.revision=NEW.approval_snapshot_revision
           AND q.quality_status='blocked'
           AND q.review_status='pending'
    ) THEN RAISE(ABORT, 'mapping approval audit requires current v2 evidence, audit, snapshot, and blocked question') END;
END;

-- Existing v1 approval remains valid for mappings without a P1-3c revision.
-- Once a current revision exists, direct approval must use the v2 audit tied to it.
DROP TRIGGER IF EXISTS trg_question_textbook_requires_approved_mapping_audit;
CREATE TRIGGER trg_question_textbook_requires_approved_mapping_audit
BEFORE UPDATE OF fit_status ON question_textbooks
FOR EACH ROW WHEN NEW.fit_status='approved' AND OLD.fit_status <> 'approved'
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM current_question_mapping_source_revisions r
         WHERE r.question_id=NEW.question_id
           AND r.textbook_id=NEW.textbook_id
           AND r.curriculum_node_id=NEW.curriculum_node_id
    ) AND NOT EXISTS (
        SELECT 1 FROM question_mapping_approval_audits_v2 a
         JOIN current_question_mapping_source_revisions r ON r.id=a.source_revision_id
         WHERE a.status='approved'
           AND r.question_id=NEW.question_id
           AND r.textbook_id=NEW.textbook_id
           AND r.curriculum_node_id=NEW.curriculum_node_id
    ) THEN RAISE(ABORT, 'revised question textbook mapping requires approved v2 mapping audit') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM current_question_mapping_source_revisions r
         WHERE r.question_id=NEW.question_id
           AND r.textbook_id=NEW.textbook_id
           AND r.curriculum_node_id=NEW.curriculum_node_id
    ) AND NOT EXISTS (
        SELECT 1
        FROM question_textbook_imports qti
        JOIN controlled_import_runs cir ON cir.id=qti.import_run_id
        JOIN question_mapping_audits qma ON qma.import_run_id=qti.import_run_id
        WHERE qti.question_id=NEW.question_id
          AND qti.textbook_id=NEW.textbook_id
          AND qti.curriculum_node_id=NEW.curriculum_node_id
          AND qma.status='approved'
          AND qma.mapping_hash=qti.mapping_hash
          AND qma.source_reference=cir.source_reference
          AND qma.source_hash=cir.source_hash
          AND cir.import_kind='question_mapping'
          AND cir.status='approved'
    ) THEN RAISE(ABORT, 'question textbook mapping requires approved source-bound mapping audit') END;
END;

-- v2 invalidation covers every semantic dependency in the approval snapshot.
CREATE TRIGGER trg_v28_snapshot_invalidate_question_inputs
AFTER UPDATE OF stem, options_json, answer, analysis, question_type, difficulty,
                stage, grade_level, source_document_id, source_fragment_id,
                source_question_no, source_page, content_hash, extraction_status ON questions
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='question_input_changed'
     WHERE question_id=NEW.id;
END;

CREATE TRIGGER trg_v28_snapshot_invalidate_source_document
AFTER UPDATE ON source_documents
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='source_document_changed'
     WHERE question_id IN (SELECT id FROM questions WHERE source_document_id=NEW.id);
END;

CREATE TRIGGER trg_v28_snapshot_invalidate_source_fragment
AFTER UPDATE ON source_fragments
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='source_fragment_changed'
     WHERE question_id IN (
        SELECT question_id FROM question_source_fragments WHERE source_fragment_id=NEW.id
        UNION SELECT question_id FROM question_assets WHERE source_fragment_id=NEW.id
        UNION SELECT id FROM questions WHERE source_fragment_id=NEW.id
     );
END;

CREATE TRIGGER trg_v28_snapshot_invalidate_provenance_insert
AFTER INSERT ON question_source_fragments
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='source_provenance_changed'
     WHERE question_id=NEW.question_id;
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_provenance_update
AFTER UPDATE ON question_source_fragments
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='source_provenance_changed'
     WHERE question_id IN (OLD.question_id, NEW.question_id);
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_provenance_delete
AFTER DELETE ON question_source_fragments
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='source_provenance_changed'
     WHERE question_id=OLD.question_id;
END;

CREATE TRIGGER trg_v28_snapshot_invalidate_asset_insert
AFTER INSERT ON question_assets
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='asset_changed'
     WHERE question_id=NEW.question_id;
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_asset_update
AFTER UPDATE ON question_assets
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='asset_changed'
     WHERE question_id IN (OLD.question_id, NEW.question_id);
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_asset_delete
AFTER DELETE ON question_assets
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='asset_changed'
     WHERE question_id=OLD.question_id;
END;

CREATE TRIGGER trg_v28_snapshot_invalidate_textbook_mapping_insert
AFTER INSERT ON question_textbooks
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='textbook_mapping_identity_changed'
     WHERE question_id=NEW.question_id;
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_textbook_mapping_identity_update
AFTER UPDATE OF question_id, textbook_id, curriculum_node_id ON question_textbooks
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='textbook_mapping_identity_changed'
     WHERE question_id IN (OLD.question_id, NEW.question_id);
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_textbook_mapping_delete
AFTER DELETE ON question_textbooks
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='textbook_mapping_identity_changed'
     WHERE question_id=OLD.question_id;
END;

CREATE TRIGGER trg_v28_snapshot_invalidate_knowledge_mapping_insert
AFTER INSERT ON question_knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='knowledge_mapping_changed'
     WHERE question_id=NEW.question_id;
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_knowledge_mapping_update
AFTER UPDATE ON question_knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='knowledge_mapping_changed'
     WHERE question_id IN (OLD.question_id, NEW.question_id);
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_knowledge_mapping_delete
AFTER DELETE ON question_knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='knowledge_mapping_changed'
     WHERE question_id=OLD.question_id;
END;

CREATE TRIGGER trg_v28_snapshot_invalidate_textbook
AFTER UPDATE ON textbooks
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='textbook_changed'
     WHERE question_id IN (SELECT question_id FROM question_textbooks WHERE textbook_id=NEW.id);
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_curriculum_node
AFTER UPDATE ON curriculum_nodes
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='curriculum_node_changed'
     WHERE question_id IN (SELECT question_id FROM question_textbooks WHERE curriculum_node_id=NEW.id);
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_knowledge_point
AFTER UPDATE ON knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='knowledge_point_changed'
     WHERE question_id IN (SELECT question_id FROM question_knowledge_points WHERE knowledge_point_id=NEW.id);
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_curriculum_knowledge_insert
AFTER INSERT ON curriculum_knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='curriculum_knowledge_changed'
     WHERE question_id IN (SELECT question_id FROM question_textbooks WHERE curriculum_node_id=NEW.curriculum_node_id);
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_curriculum_knowledge_update
AFTER UPDATE ON curriculum_knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='curriculum_knowledge_changed'
     WHERE question_id IN (
        SELECT question_id FROM question_textbooks WHERE curriculum_node_id=OLD.curriculum_node_id
        UNION SELECT question_id FROM question_textbooks WHERE curriculum_node_id=NEW.curriculum_node_id
     );
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_curriculum_knowledge_delete
AFTER DELETE ON curriculum_knowledge_points
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='curriculum_knowledge_changed'
     WHERE question_id IN (SELECT question_id FROM question_textbooks WHERE curriculum_node_id=OLD.curriculum_node_id);
END;

CREATE TRIGGER trg_v28_snapshot_invalidate_mapping_revision_head_insert
AFTER INSERT ON question_mapping_source_revision_head_events
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='mapping_source_revision_changed'
     WHERE question_id=NEW.question_id;
END;

CREATE TRIGGER trg_v28_snapshot_invalidate_controlled_content_insert
AFTER INSERT ON controlled_content_segments
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='controlled_content_changed'
     WHERE question_id IN (
        SELECT question_id FROM question_textbooks
         WHERE textbook_id=NEW.textbook_id AND curriculum_node_id=NEW.curriculum_node_id
     );
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_controlled_content_update
AFTER UPDATE ON controlled_content_segments
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='controlled_content_changed'
     WHERE question_id IN (
        SELECT question_id FROM question_textbooks
         WHERE (textbook_id=OLD.textbook_id AND curriculum_node_id=OLD.curriculum_node_id)
            OR (textbook_id=NEW.textbook_id AND curriculum_node_id=NEW.curriculum_node_id)
     );
END;
CREATE TRIGGER trg_v28_snapshot_invalidate_controlled_content_delete
AFTER DELETE ON controlled_content_segments
FOR EACH ROW
BEGIN
    UPDATE question_approval_input_snapshots_v2
       SET input_hash=NULL, revision=revision+1, calculated_at=NULL,
           invalidated_at=CURRENT_TIMESTAMP, invalidation_reason='controlled_content_changed'
     WHERE question_id IN (
        SELECT question_id FROM question_textbooks
         WHERE textbook_id=OLD.textbook_id AND curriculum_node_id=OLD.curriculum_node_id
     );
END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.28-p13c-approval-boundary-and-mapping-revisions', datetime('now'));
