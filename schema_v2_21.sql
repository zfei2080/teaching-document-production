PRAGMA foreign_keys = ON;

-- Controlled source intake records the read-only original and the archive
-- copy separately. source_documents.relative_path/file_hash always identify
-- the archive used by extractors; the original hash binds that archive to the
-- user-designated trusted source at intake time.
ALTER TABLE source_documents ADD COLUMN original_relative_path TEXT;
ALTER TABLE source_documents ADD COLUMN original_file_hash TEXT;
ALTER TABLE source_documents ADD COLUMN trusted_source INTEGER NOT NULL DEFAULT 0
    CHECK (trusted_source IN (0, 1));
ALTER TABLE source_documents ADD COLUMN intake_manifest_json TEXT;

CREATE TRIGGER IF NOT EXISTS trg_source_document_trusted_requires_original_binding
BEFORE INSERT ON source_documents
FOR EACH ROW WHEN NEW.trusted_source=1
BEGIN
    SELECT CASE WHEN NEW.original_relative_path IS NULL
                      OR NEW.original_file_hash IS NULL
                      OR NEW.original_file_hash <> NEW.file_hash
      THEN RAISE(ABORT, 'trusted source requires matching original/archive hash binding') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_source_document_trusted_update_requires_original_binding
BEFORE UPDATE OF file_hash, original_relative_path, original_file_hash, trusted_source ON source_documents
FOR EACH ROW WHEN NEW.trusted_source=1
BEGIN
    SELECT CASE WHEN NEW.original_relative_path IS NULL
                      OR NEW.original_file_hash IS NULL
                      OR NEW.original_file_hash <> NEW.file_hash
      THEN RAISE(ABORT, 'trusted source requires matching original/archive hash binding') END;
END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.21-controlled-source-intake', datetime('now'));
