PRAGMA foreign_keys = ON;

-- Source-bound, fail-closed import path for DOCX media-instance adjacency
-- evidence.  A manifest is evidence only while validated: it does not create a
-- question_assets row.  Only a source-bound approved audit can materialize a
-- verified question asset from one exact assigned media instance.
CREATE TABLE IF NOT EXISTS asset_adjacency_imports (
    id TEXT PRIMARY KEY,
    source_document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    source_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL UNIQUE,
    generator_id TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('validated', 'approved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_document_id, source_hash, manifest_hash)
);

CREATE TABLE IF NOT EXISTS asset_adjacency_instances (
    import_run_id TEXT NOT NULL REFERENCES asset_adjacency_imports(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    assignment_status TEXT NOT NULL CHECK (assignment_status IN ('assigned', 'unassigned', 'ambiguous')),
    question_id TEXT REFERENCES questions(id) ON DELETE RESTRICT,
    source_fragment_id TEXT REFERENCES source_fragments(id) ON DELETE RESTRICT,
    assignment_reason TEXT NOT NULL,
    package_part TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    relationship_status TEXT NOT NULL,
    relationship_target TEXT,
    media_path TEXT,
    filename TEXT,
    media_sha256 TEXT,
    host_kind TEXT NOT NULL,
    host_location TEXT NOT NULL,
    paragraph_index INTEGER,
    drawing_layout_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (import_run_id, instance_id),
    CHECK ((assignment_status = 'assigned' AND question_id IS NOT NULL AND source_fragment_id IS NOT NULL)
        OR (assignment_status IN ('unassigned', 'ambiguous') AND question_id IS NULL AND source_fragment_id IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_adjacency_instance_source_identity
ON asset_adjacency_instances(instance_id, import_run_id);
CREATE INDEX IF NOT EXISTS idx_asset_adjacency_instances_question
ON asset_adjacency_instances(question_id, assignment_status);

-- An assigned instance can only claim a stem paragraph of the exact question
-- and source document represented by its import batch.  This prevents a
-- valid-looking instance row from being rebound to a neighboring question.
CREATE TRIGGER IF NOT EXISTS trg_asset_adjacency_instance_requires_exact_stem
BEFORE INSERT ON asset_adjacency_instances
FOR EACH ROW WHEN NEW.assignment_status = 'assigned'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM asset_adjacency_imports aii
        JOIN questions q ON q.id=NEW.question_id
        JOIN source_fragments sf ON sf.id=NEW.source_fragment_id
        JOIN question_source_fragments qsf
          ON qsf.question_id=q.id AND qsf.source_fragment_id=sf.id
        WHERE aii.id=NEW.import_run_id
          AND aii.status='validated'
          AND q.source_document_id=aii.source_document_id
          AND sf.source_document_id=aii.source_document_id
          AND sf.paragraph_index=NEW.paragraph_index
          AND qsf.field_name='stem'
          AND qsf.source_hash=sf.raw_hash
    ) THEN RAISE(ABORT, 'assigned adjacency instance requires exact question stem source fragment') END;
END;

CREATE TABLE IF NOT EXISTS asset_adjacency_audits (
    id TEXT PRIMARY KEY,
    import_run_id TEXT NOT NULL UNIQUE REFERENCES asset_adjacency_imports(id) ON DELETE CASCADE,
    manifest_hash TEXT NOT NULL,
    audit_manifest_hash TEXT NOT NULL,
    auditor_id TEXT NOT NULL,
    audit_method TEXT NOT NULL,
    source_document_id TEXT NOT NULL REFERENCES source_documents(id),
    source_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    findings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(import_run_id, audit_manifest_hash)
);

CREATE TABLE IF NOT EXISTS question_asset_adjacency_links (
    question_asset_id TEXT PRIMARY KEY REFERENCES question_assets(id) ON DELETE CASCADE,
    import_run_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    FOREIGN KEY (import_run_id, instance_id)
      REFERENCES asset_adjacency_instances(import_run_id, instance_id) ON DELETE RESTRICT,
    UNIQUE(import_run_id, instance_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_adjacency_audits_import_status
ON asset_adjacency_audits(import_run_id, status);

-- A verified question asset sourced through adjacency evidence must have an
-- approved audit for its exact manifest/source, must reference an assigned
-- resolved internal instance, and must keep the evidence checksum unchanged.
CREATE TRIGGER IF NOT EXISTS trg_question_asset_verified_requires_adjacency_audit
BEFORE UPDATE OF status ON question_assets
FOR EACH ROW WHEN NEW.status = 'verified' AND OLD.status <> 'verified'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM question_asset_adjacency_links qaal
        JOIN asset_adjacency_instances aai
          ON aai.import_run_id=qaal.import_run_id AND aai.instance_id=qaal.instance_id
        JOIN asset_adjacency_imports aii ON aii.id=aai.import_run_id
        JOIN asset_adjacency_audits aaa ON aaa.import_run_id=aii.id
        JOIN source_documents sd ON sd.id=aii.source_document_id
        WHERE qaal.question_asset_id=NEW.id
          AND aai.question_id=NEW.question_id
          AND aai.assignment_status='assigned'
          AND aai.relationship_status='resolved_internal'
          AND aii.status='approved'
          AND aaa.status='approved'
          AND aaa.manifest_hash=aii.manifest_hash
          AND aaa.source_document_id=aii.source_document_id
          AND aaa.source_hash=aii.source_hash
          AND sd.file_hash=aii.source_hash
          AND (NEW.checksum IS aai.media_sha256)
    ) THEN RAISE(ABORT, 'verified question asset requires approved source-bound adjacency audit') END;
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.12-controlled-asset-adjacency-audit-2026-07-26');
