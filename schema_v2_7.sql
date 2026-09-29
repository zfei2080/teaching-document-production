PRAGMA foreign_keys = ON;

-- Provenance relationships must be semantically correct, not merely present.
-- These triggers protect the development database from linking a catalog release
-- to a question-mapping run (or vice versa), and from approving a release whose
-- source identity differs from the recorded controlled import.
CREATE TRIGGER IF NOT EXISTS trg_catalog_release_import_kind_and_source_insert
BEFORE INSERT ON catalog_release_imports
FOR EACH ROW
BEGIN
    SELECT CASE WHEN (SELECT import_kind FROM controlled_import_runs WHERE id = NEW.import_run_id) <> 'catalog'
      THEN RAISE(ABORT, 'catalog release requires catalog import run') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM catalog_releases cr
        JOIN controlled_import_runs cir ON cir.id = NEW.import_run_id
        WHERE cr.id = NEW.catalog_release_id
          AND cr.source_reference = cir.source_reference
          AND cr.source_hash = cir.source_hash
    ) THEN RAISE(ABORT, 'catalog release source must match controlled import') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_catalog_release_import_kind_and_source_update
BEFORE UPDATE OF catalog_release_id, import_run_id ON catalog_release_imports
FOR EACH ROW
BEGIN
    SELECT CASE WHEN (SELECT import_kind FROM controlled_import_runs WHERE id = NEW.import_run_id) <> 'catalog'
      THEN RAISE(ABORT, 'catalog release requires catalog import run') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM catalog_releases cr
        JOIN controlled_import_runs cir ON cir.id = NEW.import_run_id
        WHERE cr.id = NEW.catalog_release_id
          AND cr.source_reference = cir.source_reference
          AND cr.source_hash = cir.source_hash
    ) THEN RAISE(ABORT, 'catalog release source must match controlled import') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_question_textbook_import_kind_insert
BEFORE INSERT ON question_textbook_imports
FOR EACH ROW
BEGIN
    SELECT CASE WHEN (SELECT import_kind FROM controlled_import_runs WHERE id = NEW.import_run_id) <> 'question_mapping'
      THEN RAISE(ABORT, 'question textbook mapping requires question_mapping import run') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_question_textbook_import_kind_update
BEFORE UPDATE OF import_run_id ON question_textbook_imports
FOR EACH ROW
BEGIN
    SELECT CASE WHEN (SELECT import_kind FROM controlled_import_runs WHERE id = NEW.import_run_id) <> 'question_mapping'
      THEN RAISE(ABORT, 'question textbook mapping requires question_mapping import run') END;
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.7-provenance-link-integrity-2026-07-26');
