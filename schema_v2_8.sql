PRAGMA foreign_keys = ON;

-- A catalog can be approved only through a reproducible audit attestation.
-- This is a controlled setup action, not a per-question manual review path.
CREATE TABLE IF NOT EXISTS catalog_audits (
    id TEXT PRIMARY KEY,
    catalog_release_id TEXT NOT NULL REFERENCES catalog_releases(id) ON DELETE CASCADE,
    import_run_id TEXT NOT NULL REFERENCES controlled_import_runs(id),
    audit_manifest_hash TEXT NOT NULL,
    auditor_id TEXT NOT NULL,
    audit_method TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    findings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(catalog_release_id, audit_manifest_hash)
);

CREATE INDEX IF NOT EXISTS idx_catalog_audits_release_status
ON catalog_audits(catalog_release_id, status);

-- Prevent a release from becoming approved unless an approved audit binds the
-- same release, catalog import, source reference and source hash.
CREATE TRIGGER IF NOT EXISTS trg_catalog_release_requires_approved_audit
BEFORE UPDATE OF status ON catalog_releases
FOR EACH ROW WHEN NEW.status = 'approved' AND OLD.status <> 'approved'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM catalog_audits ca
        JOIN controlled_import_runs cir ON cir.id = ca.import_run_id
        WHERE ca.catalog_release_id = NEW.id
          AND ca.status = 'approved'
          AND ca.source_reference = NEW.source_reference
          AND ca.source_hash = NEW.source_hash
          AND cir.import_kind = 'catalog'
          AND cir.status = 'validated'
          AND cir.source_reference = NEW.source_reference
          AND cir.source_hash = NEW.source_hash
    ) THEN RAISE(ABORT, 'catalog release requires approved source-bound catalog audit') END;
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.8-catalog-audit-attestation-2026-07-26');
