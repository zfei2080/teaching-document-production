PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS textbook_sources (
    id TEXT PRIMARY KEY,
    textbook_id TEXT NOT NULL REFERENCES textbooks(id) ON DELETE CASCADE,
    source_document_id TEXT REFERENCES source_documents(id) ON DELETE SET NULL,
    source_file_hash TEXT NOT NULL CHECK (length(trim(source_file_hash)) > 0),
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL CHECK (status IN ('active', 'replaced', 'invalidated')),
    invalidated_at TEXT,
    invalidation_reason TEXT,
    CHECK ((status = 'invalidated' AND invalidated_at IS NOT NULL)
        OR (status <> 'invalidated' AND invalidated_at IS NULL)),
    UNIQUE(textbook_id, source_file_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_textbook_sources_one_active
ON textbook_sources(textbook_id)
WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_textbook_sources_lookup
ON textbook_sources(textbook_id, status, imported_at DESC);

CREATE TABLE IF NOT EXISTS textbook_body_units (
    id TEXT PRIMARY KEY,
    textbook_source_id TEXT NOT NULL REFERENCES textbook_sources(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    locator TEXT NOT NULL CHECK (length(trim(locator)) > 0),
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    raw_hash TEXT NOT NULL CHECK (length(trim(raw_hash)) > 0),
    normalized_hash TEXT NOT NULL CHECK (length(trim(normalized_hash)) > 0),
    structural_hash TEXT NOT NULL CHECK (length(trim(structural_hash)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(textbook_source_id, ordinal),
    UNIQUE(textbook_source_id, locator)
);

CREATE INDEX IF NOT EXISTS idx_textbook_body_units_source
ON textbook_body_units(textbook_source_id, ordinal);

CREATE TABLE IF NOT EXISTS question_body_unit_mappings (
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    body_unit_id TEXT NOT NULL REFERENCES textbook_body_units(id) ON DELETE CASCADE,
    source_file_hash TEXT NOT NULL CHECK (length(trim(source_file_hash)) > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invalidated')),
    invalidated_at TEXT,
    invalidation_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (question_id, body_unit_id),
    CHECK ((status = 'invalidated' AND invalidated_at IS NOT NULL)
        OR (status = 'active' AND invalidated_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_question_body_unit_mappings_body
ON question_body_unit_mappings(body_unit_id, status);

CREATE TABLE IF NOT EXISTS knowledge_body_unit_mappings (
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    body_unit_id TEXT NOT NULL REFERENCES textbook_body_units(id) ON DELETE CASCADE,
    source_file_hash TEXT NOT NULL CHECK (length(trim(source_file_hash)) > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invalidated')),
    invalidated_at TEXT,
    invalidation_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (knowledge_point_id, body_unit_id),
    CHECK ((status = 'invalidated' AND invalidated_at IS NOT NULL)
        OR (status = 'active' AND invalidated_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_knowledge_body_unit_mappings_body
ON knowledge_body_unit_mappings(body_unit_id, status);

CREATE TABLE IF NOT EXISTS textbook_body_unit_snapshots (
    body_unit_id TEXT PRIMARY KEY REFERENCES textbook_body_units(id) ON DELETE CASCADE,
    source_file_hash TEXT,
    raw_hash TEXT,
    normalized_hash TEXT,
    structural_hash TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    captured_at TEXT,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    CHECK ((source_file_hash IS NULL AND raw_hash IS NULL AND normalized_hash IS NULL AND structural_hash IS NULL AND invalidated_at IS NOT NULL)
        OR (source_file_hash IS NOT NULL AND raw_hash IS NOT NULL AND normalized_hash IS NOT NULL AND structural_hash IS NOT NULL AND invalidated_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_textbook_body_unit_snapshots_active
ON textbook_body_unit_snapshots(body_unit_id, source_file_hash);

CREATE TRIGGER IF NOT EXISTS trg_textbook_sources_require_active_body_snapshot_source
BEFORE INSERT ON textbook_body_units
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM textbook_sources ts
        WHERE ts.id = NEW.textbook_source_id AND ts.status = 'active'
    ) THEN RAISE(ABORT, 'textbook body unit requires active textbook source') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_textbook_body_units_snapshot_insert
AFTER INSERT ON textbook_body_units
FOR EACH ROW
BEGIN
    INSERT INTO textbook_body_unit_snapshots (
        body_unit_id, source_file_hash, raw_hash, normalized_hash, structural_hash, revision, captured_at, invalidated_at, invalidation_reason
    )
    SELECT NEW.id, ts.source_file_hash, NEW.raw_hash, NEW.normalized_hash, NEW.structural_hash, 0, CURRENT_TIMESTAMP, NULL, NULL
    FROM textbook_sources ts
    WHERE ts.id = NEW.textbook_source_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_textbook_body_units_reject_manual_hash_update
BEFORE UPDATE OF raw_hash, normalized_hash, structural_hash ON textbook_body_units
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'textbook body unit hashes are immutable; replace the source instead');
END;

CREATE TRIGGER IF NOT EXISTS trg_textbook_body_units_invalidate_dependents
AFTER UPDATE OF text, locator, ordinal ON textbook_body_units
FOR EACH ROW
BEGIN
    UPDATE textbook_body_unit_snapshots
       SET source_file_hash = NULL,
           raw_hash = NULL,
           normalized_hash = NULL,
           structural_hash = NULL,
           revision = revision + 1,
           captured_at = NULL,
           invalidated_at = CURRENT_TIMESTAMP,
           invalidation_reason = 'textbook_body_unit_changed'
     WHERE body_unit_id = NEW.id;

    UPDATE question_body_unit_mappings
       SET status = 'invalidated',
           invalidated_at = CURRENT_TIMESTAMP,
           invalidation_reason = 'textbook_body_unit_changed'
     WHERE body_unit_id = NEW.id AND status <> 'invalidated';

    UPDATE knowledge_body_unit_mappings
       SET status = 'invalidated',
           invalidated_at = CURRENT_TIMESTAMP,
           invalidation_reason = 'textbook_body_unit_changed'
     WHERE body_unit_id = NEW.id AND status <> 'invalidated';
END;

CREATE TRIGGER IF NOT EXISTS trg_textbook_sources_invalidate_dependents
AFTER UPDATE OF status, source_file_hash ON textbook_sources
FOR EACH ROW
WHEN NEW.status <> 'active' OR NEW.source_file_hash <> OLD.source_file_hash
BEGIN
    UPDATE textbook_body_unit_snapshots
       SET source_file_hash = NULL,
           raw_hash = NULL,
           normalized_hash = NULL,
           structural_hash = NULL,
           revision = revision + 1,
           captured_at = NULL,
           invalidated_at = CURRENT_TIMESTAMP,
           invalidation_reason = CASE
               WHEN NEW.status <> 'active' THEN 'textbook_source_inactive'
               ELSE 'textbook_source_hash_changed'
           END
     WHERE body_unit_id IN (
         SELECT id FROM textbook_body_units WHERE textbook_source_id = NEW.id
     )
       AND invalidated_at IS NULL;

    UPDATE question_body_unit_mappings
       SET status = 'invalidated',
           invalidated_at = CURRENT_TIMESTAMP,
           invalidation_reason = CASE
               WHEN NEW.status <> 'active' THEN 'textbook_source_inactive'
               ELSE 'textbook_source_hash_changed'
           END
     WHERE body_unit_id IN (
         SELECT id FROM textbook_body_units WHERE textbook_source_id = NEW.id
     )
       AND status <> 'invalidated';

    UPDATE knowledge_body_unit_mappings
       SET status = 'invalidated',
           invalidated_at = CURRENT_TIMESTAMP,
           invalidation_reason = CASE
               WHEN NEW.status <> 'active' THEN 'textbook_source_inactive'
               ELSE 'textbook_source_hash_changed'
           END
     WHERE body_unit_id IN (
         SELECT id FROM textbook_body_units WHERE textbook_source_id = NEW.id
     )
       AND status <> 'invalidated';
END;

CREATE TRIGGER IF NOT EXISTS trg_question_body_unit_mapping_requires_active_snapshot
BEFORE INSERT ON question_body_unit_mappings
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
          FROM textbook_body_unit_snapshots s
          JOIN textbook_body_units bu ON bu.id = s.body_unit_id
          JOIN textbook_sources ts ON ts.id = bu.textbook_source_id
         WHERE s.body_unit_id = NEW.body_unit_id
           AND s.invalidated_at IS NULL
           AND ts.status = 'active'
           AND ts.source_file_hash = NEW.source_file_hash
    ) THEN RAISE(ABORT, 'question body mapping requires active textbook body snapshot') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_knowledge_body_unit_mapping_requires_active_snapshot
BEFORE INSERT ON knowledge_body_unit_mappings
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
          FROM textbook_body_unit_snapshots s
          JOIN textbook_body_units bu ON bu.id = s.body_unit_id
          JOIN textbook_sources ts ON ts.id = bu.textbook_source_id
         WHERE s.body_unit_id = NEW.body_unit_id
           AND s.invalidated_at IS NULL
           AND ts.status = 'active'
           AND ts.source_file_hash = NEW.source_file_hash
    ) THEN RAISE(ABORT, 'knowledge body mapping requires active textbook body snapshot') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_questions_block_approval_without_active_textbook_body_evidence
BEFORE UPDATE OF quality_status, review_status ON questions
FOR EACH ROW WHEN NEW.quality_status='approved' OR NEW.review_status='approved'
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM question_textbooks qt WHERE qt.question_id = NEW.id
    ) AND NOT EXISTS (
        SELECT 1
          FROM question_body_unit_mappings qbm
          JOIN textbook_body_unit_snapshots s ON s.body_unit_id = qbm.body_unit_id
          JOIN textbook_body_units bu ON bu.id = qbm.body_unit_id
          JOIN textbook_sources ts ON ts.id = bu.textbook_source_id
         WHERE qbm.question_id = NEW.id
           AND qbm.status = 'active'
           AND s.invalidated_at IS NULL
           AND ts.status = 'active'
           AND qbm.source_file_hash = ts.source_file_hash
    ) THEN RAISE(ABORT, 'approved textbook-mapped question requires active textbook body evidence') END;
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.18-textbook-body-snapshot-chain-2026-07-26');
