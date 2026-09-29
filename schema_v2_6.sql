PRAGMA foreign_keys = ON;

-- Controlled import provenance closes the gap between a row marked "approved"
-- and an auditable, reproducible curriculum/mapping import.  The automatic
-- textbook-scope validator must require this evidence; an ad-hoc SQL status
-- update alone is never sufficient for delivery admission.
CREATE TABLE IF NOT EXISTS controlled_import_runs (
    id TEXT PRIMARY KEY,
    import_kind TEXT NOT NULL CHECK (import_kind IN ('catalog', 'question_mapping')),
    source_reference TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    importer_id TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('validated', 'approved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(import_kind, source_reference, source_hash, manifest_hash, importer_id, importer_version)
);

CREATE TABLE IF NOT EXISTS catalog_release_imports (
    catalog_release_id TEXT PRIMARY KEY REFERENCES catalog_releases(id) ON DELETE CASCADE,
    import_run_id TEXT NOT NULL REFERENCES controlled_import_runs(id),
    UNIQUE(import_run_id, catalog_release_id)
);

-- curriculum_node_id is deliberately non-null: an automatic textbook-scope
-- pass is defined only at a concrete controlled curriculum node.
CREATE TABLE IF NOT EXISTS question_textbook_imports (
    question_id TEXT NOT NULL,
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    import_run_id TEXT NOT NULL REFERENCES controlled_import_runs(id),
    mapping_hash TEXT NOT NULL,
    PRIMARY KEY (question_id, textbook_id, curriculum_node_id),
    FOREIGN KEY (question_id, textbook_id, curriculum_node_id)
      REFERENCES question_textbooks(question_id, textbook_id, curriculum_node_id)
      ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_controlled_import_runs_kind_status
ON controlled_import_runs(import_kind, status);
CREATE INDEX IF NOT EXISTS idx_question_textbook_imports_run
ON question_textbook_imports(import_run_id);

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.6-controlled-import-provenance-2026-07-26');
