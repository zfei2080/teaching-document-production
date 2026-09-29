PRAGMA foreign_keys = ON;

-- A catalog release is the controlled, versioned source of curriculum scope.
-- Question mappings may only support automatic admission when they reference
-- an approved release for the same textbook and catalog version.
CREATE TABLE IF NOT EXISTS catalog_releases (
    id TEXT PRIMARY KEY,
    textbook_id TEXT NOT NULL REFERENCES textbooks(id),
    catalog_version TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'archived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(textbook_id, catalog_version)
);

CREATE INDEX IF NOT EXISTS idx_catalog_releases_textbook_version
ON catalog_releases(textbook_id, catalog_version, status);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('v2.5-controlled-catalog-releases-2026-07-25');
