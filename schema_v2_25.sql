PRAGMA foreign_keys = ON;

DROP VIEW IF EXISTS current_question_auto_mapping_audits;
DROP TRIGGER IF EXISTS trg_question_auto_mapping_audit_logs_insert_guards;

CREATE VIEW current_question_auto_mapping_audits AS
SELECT a.*
FROM question_auto_mapping_audit_logs a
JOIN question_curriculum_mapping_evidence e ON e.id = a.evidence_id
JOIN question_input_snapshots s ON s.question_id = a.question_id
JOIN questions q ON q.id = a.question_id
JOIN source_documents sd ON sd.id = q.source_document_id
JOIN textbooks t ON t.id = a.textbook_id
JOIN curriculum_nodes n ON n.id = a.curriculum_node_id
JOIN catalog_releases cr
  ON cr.textbook_id = t.id AND cr.catalog_version = t.catalog_version
WHERE a.audit_status = 'pass'
  AND a.decision_basis = 'current_candidate'
  AND json_valid(a.validator_results_json) = 1
  AND json_type(a.validator_results_json) = 'object'
  AND (
      SELECT COUNT(*)
      FROM json_each(a.validator_results_json)
  ) = 4
  AND (
      SELECT COUNT(*)
      FROM json_each(a.validator_results_json) AS item
      WHERE item.key IN ('source_fidelity', 'structural_consistency', 'mathematical_independent', 'asset_semantics')
        AND json_type(item.value) = 'object'
        AND TRIM(COALESCE(json_extract(item.value, '$.validator_id'), '')) <> ''
        AND TRIM(COALESCE(json_extract(item.value, '$.validator_version'), '')) <> ''
        AND json_extract(item.value, '$.status') = 'pass'
        AND json_extract(item.value, '$.input_hash') = a.question_input_hash
  ) = 4
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
  AND e.evidence_hash = a.evidence_hash
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
        AND other.invalidated_at IS NULL
        AND other.status = 'candidate'
  );

CREATE TRIGGER trg_question_auto_mapping_audit_logs_insert_guards
BEFORE INSERT ON question_auto_mapping_audit_logs
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
          AND e.question_id = NEW.question_id
          AND e.textbook_id = NEW.textbook_id
          AND e.curriculum_node_id = NEW.curriculum_node_id
          AND e.question_input_hash = NEW.evidence_input_hash
          AND e.question_snapshot_revision = NEW.evidence_question_snapshot_revision
          AND e.evidence_hash = NEW.evidence_hash
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
    ) THEN RAISE(ABORT, 'auto mapping audit requires current evidence binding') END;

    SELECT CASE WHEN NEW.audit_status = 'pass' AND NEW.decision_basis <> 'current_candidate'
      THEN RAISE(ABORT, 'auto mapping audit pass requires current candidate evidence') END;

    SELECT CASE WHEN NEW.audit_status = 'pass' AND NOT EXISTS (
        SELECT 1
        FROM question_curriculum_mapping_evidence e
        WHERE e.id = NEW.evidence_id
          AND e.status = 'candidate'
          AND e.invalidated_at IS NULL
    ) THEN RAISE(ABORT, 'auto mapping audit pass requires current candidate evidence') END;

    SELECT CASE WHEN NEW.audit_status = 'pass' AND EXISTS (
        SELECT 1
        FROM question_curriculum_mapping_evidence other
        WHERE other.question_id = NEW.question_id
          AND other.question_input_hash = NEW.question_input_hash
          AND other.invalidated_at IS NULL
          AND other.id <> NEW.evidence_id
          AND other.status = 'candidate'
    ) THEN RAISE(ABORT, 'auto mapping audit pass requires a single current candidate mapping') END;

    SELECT CASE WHEN NEW.audit_status = 'pass' AND json_valid(NEW.validator_results_json) <> 1
      THEN RAISE(ABORT, 'auto mapping audit pass requires validator bundle object') END;

    SELECT CASE WHEN NEW.audit_status = 'pass' AND json_type(NEW.validator_results_json) <> 'object'
      THEN RAISE(ABORT, 'auto mapping audit pass requires validator bundle object') END;

    SELECT CASE WHEN NEW.audit_status = 'pass' AND (
        SELECT COUNT(*)
        FROM json_each(NEW.validator_results_json)
    ) <> 4 THEN RAISE(ABORT, 'auto mapping audit pass requires exactly four validator bundles') END;

    SELECT CASE WHEN NEW.audit_status = 'pass' AND EXISTS (
        SELECT 1
        FROM json_each(NEW.validator_results_json) AS item
        WHERE item.key NOT IN ('source_fidelity', 'structural_consistency', 'mathematical_independent', 'asset_semantics')
    ) THEN RAISE(ABORT, 'auto mapping audit pass requires only required validator bundles') END;

    SELECT CASE WHEN NEW.audit_status = 'pass' AND EXISTS (
        SELECT 1
        FROM json_each(NEW.validator_results_json) AS item
        WHERE json_type(item.value) <> 'object'
           OR TRIM(COALESCE(json_extract(item.value, '$.validator_id'), '')) = ''
           OR TRIM(COALESCE(json_extract(item.value, '$.validator_version'), '')) = ''
           OR json_extract(item.value, '$.status') <> 'pass'
           OR json_extract(item.value, '$.input_hash') <> NEW.question_input_hash
    ) THEN RAISE(ABORT, 'auto mapping audit pass requires all current validator passes') END;

    SELECT CASE WHEN NEW.audit_status = 'pass' AND (
        SELECT COUNT(*)
        FROM json_each(NEW.validator_results_json) AS item
        WHERE item.key IN ('source_fidelity', 'structural_consistency', 'mathematical_independent', 'asset_semantics')
    ) <> 4 THEN RAISE(ABORT, 'auto mapping audit pass requires all required validator bundles') END;
END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.25-question-auto-mapping-audit-bundle-hardening', datetime('now'));
