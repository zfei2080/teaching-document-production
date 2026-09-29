PRAGMA foreign_keys = ON;

-- Knowledge-point content is a separate controlled artifact from a catalog
-- hierarchy.  A catalog may be approved while its knowledge annotations remain
-- unavailable; this keeps the automatic scope gate fail-closed.
CREATE TABLE IF NOT EXISTS controlled_knowledge_import_runs (
    id TEXT PRIMARY KEY,
    textbook_id TEXT NOT NULL REFERENCES textbooks(id),
    catalog_release_id TEXT NOT NULL REFERENCES catalog_releases(id),
    source_reference TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    importer_id TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('validated', 'approved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(textbook_id, catalog_release_id, source_reference, source_hash, manifest_hash, importer_id, importer_version)
);

CREATE TABLE IF NOT EXISTS knowledge_point_imports (
    knowledge_point_id TEXT PRIMARY KEY REFERENCES knowledge_points(id) ON DELETE CASCADE,
    import_run_id TEXT NOT NULL REFERENCES controlled_knowledge_import_runs(id) ON DELETE CASCADE,
    knowledge_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curriculum_knowledge_point_imports (
    curriculum_node_id TEXT NOT NULL,
    knowledge_point_id TEXT NOT NULL,
    import_run_id TEXT NOT NULL REFERENCES controlled_knowledge_import_runs(id) ON DELETE CASCADE,
    relation_hash TEXT NOT NULL,
    PRIMARY KEY (curriculum_node_id, knowledge_point_id),
    FOREIGN KEY (curriculum_node_id, knowledge_point_id)
      REFERENCES curriculum_knowledge_points(curriculum_node_id, knowledge_point_id)
      ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_controlled_knowledge_import_runs_release_status
ON controlled_knowledge_import_runs(catalog_release_id, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_point_imports_run
ON knowledge_point_imports(import_run_id);
CREATE INDEX IF NOT EXISTS idx_curriculum_knowledge_point_imports_run
ON curriculum_knowledge_point_imports(import_run_id);

CREATE TABLE IF NOT EXISTS knowledge_mapping_audits (
    id TEXT PRIMARY KEY,
    import_run_id TEXT NOT NULL UNIQUE REFERENCES controlled_knowledge_import_runs(id) ON DELETE CASCADE,
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

CREATE INDEX IF NOT EXISTS idx_knowledge_mapping_audits_import_status
ON knowledge_mapping_audits(import_run_id, status);

-- A direct SQL status flip cannot make a knowledge point acceptable to the
-- automatic textbook-scope validator.  Every approved point must have an exact
-- source-bound approved knowledge audit and matching approved import batch.
CREATE TRIGGER IF NOT EXISTS trg_knowledge_point_requires_approved_knowledge_audit
BEFORE UPDATE OF review_status ON knowledge_points
FOR EACH ROW WHEN NEW.review_status = 'approved' AND OLD.review_status <> 'approved'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM knowledge_point_imports kpi
        JOIN controlled_knowledge_import_runs kir ON kir.id = kpi.import_run_id
        JOIN knowledge_mapping_audits kma ON kma.import_run_id = kir.id
        WHERE kpi.knowledge_point_id = NEW.id
          AND kir.status = 'approved'
          AND kma.status = 'approved'
          AND kma.source_reference = kir.source_reference
          AND kma.source_hash = kir.source_hash
    ) THEN RAISE(ABORT, 'knowledge point requires approved source-bound knowledge audit') END;
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.11-controlled-knowledge-mapping-audit-2026-07-26');
