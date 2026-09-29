PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;

CREATE TABLE controlled_auxiliary_content_import_runs (
    id TEXT PRIMARY KEY,
    manifest_hash TEXT NOT NULL UNIQUE CHECK(length(manifest_hash)=64),
    status TEXT NOT NULL CHECK(status='validated_evidence'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE controlled_auxiliary_content_evidence (
    id TEXT PRIMARY KEY,
    import_run_id TEXT NOT NULL REFERENCES controlled_auxiliary_content_import_runs(id) ON DELETE RESTRICT,
    controlled_source_id TEXT NOT NULL REFERENCES controlled_content_sources(id) ON DELETE RESTRICT,
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL CHECK(evidence_type IN ('worked_example','summary','self_assessment')),
    source_key TEXT NOT NULL CHECK(TRIM(source_key)<>''),
    original_sha256 TEXT NOT NULL CHECK(length(original_sha256)=64),
    archive_sha256 TEXT NOT NULL CHECK(length(archive_sha256)=64),
    converted_sha256 TEXT NOT NULL CHECK(length(converted_sha256)=64),
    paragraph_index INTEGER NOT NULL CHECK(paragraph_index>=0),
    paragraph_sha256 TEXT NOT NULL CHECK(length(paragraph_sha256)=64),
    marker TEXT NOT NULL CHECK(TRIM(marker)<>''),
    fragment_sha256 TEXT NOT NULL CHECK(length(fragment_sha256)=64),
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    evidence_hash TEXT NOT NULL UNIQUE CHECK(length(evidence_hash)=64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIEW current_controlled_auxiliary_content_evidence AS
SELECT e.*
FROM controlled_auxiliary_content_evidence e
JOIN controlled_auxiliary_content_import_runs r ON r.id=e.import_run_id AND r.status='validated_evidence'
JOIN controlled_content_sources s ON s.id=e.controlled_source_id
 AND s.original_sha256=e.original_sha256
 AND s.archive_sha256=e.archive_sha256
 AND s.converted_sha256=e.converted_sha256;

CREATE TRIGGER trg_v33_auxiliary_evidence_insert_guard
BEFORE INSERT ON controlled_auxiliary_content_evidence
FOR EACH ROW
BEGIN
  SELECT CASE WHEN NOT EXISTS(
    SELECT 1 FROM controlled_auxiliary_content_import_runs r
    WHERE r.id=NEW.import_run_id AND r.status='validated_evidence'
  ) THEN RAISE(ABORT,'auxiliary evidence requires a validated import run') END;
  SELECT CASE WHEN NOT EXISTS(
    SELECT 1 FROM controlled_content_sources s
    WHERE s.id=NEW.controlled_source_id
      AND s.original_sha256=NEW.original_sha256
      AND s.archive_sha256=NEW.archive_sha256
      AND s.converted_sha256=NEW.converted_sha256
  ) THEN RAISE(ABORT,'auxiliary evidence requires current controlled source hashes') END;
  SELECT CASE WHEN NEW.evidence_type='worked_example' AND NEW.marker!='例题'
    THEN RAISE(ABORT,'worked example requires explicit example marker') END;
  SELECT CASE WHEN NEW.evidence_type='summary' AND NEW.marker NOT IN ('总结','小结')
    THEN RAISE(ABORT,'summary requires explicit summary marker') END;
  SELECT CASE WHEN NEW.evidence_type='self_assessment' AND NEW.marker NOT IN ('自评','自我评价')
    THEN RAISE(ABORT,'self assessment requires explicit self-assessment marker') END;
END;

CREATE TRIGGER trg_v33_auxiliary_evidence_immutable_update
BEFORE UPDATE ON controlled_auxiliary_content_evidence
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'auxiliary content evidence is append-only'); END;
CREATE TRIGGER trg_v33_auxiliary_evidence_immutable_delete
BEFORE DELETE ON controlled_auxiliary_content_evidence
FOR EACH ROW BEGIN SELECT RAISE(ABORT,'auxiliary content evidence is append-only'); END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.33-p14b-controlled-auxiliary-content-evidence', CURRENT_TIMESTAMP);
COMMIT;
