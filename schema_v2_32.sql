PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

-- P1-4b: a pedagogical role is usable only when it is explicitly anchored in
-- a current source fragment and the question remains approved for the exact
-- current curriculum mapping. Roles are never inferred from filenames,
-- difficulty, or an otherwise approved question.
CREATE TABLE controlled_pedagogical_role_import_runs (
    id TEXT PRIMARY KEY,
    manifest_sha256 TEXT NOT NULL UNIQUE CHECK (length(manifest_sha256)=64),
    importer_id TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status='validated_evidence'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE question_pedagogical_role_evidence (
    id TEXT PRIMARY KEY,
    import_run_id TEXT NOT NULL REFERENCES controlled_pedagogical_role_import_runs(id) ON DELETE RESTRICT,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN (
        'activation_diagnostic', 'public_core', 'basic_reinforcement',
        'standard_extension', 'challenge_extension', 'transfer'
    )),
    source_document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
    source_document_hash TEXT NOT NULL CHECK (length(source_document_hash)=64),
    source_fragment_id TEXT NOT NULL REFERENCES source_fragments(id) ON DELETE RESTRICT,
    source_hash TEXT NOT NULL CHECK (length(source_hash)=64),
    role_marker TEXT NOT NULL CHECK (TRIM(role_marker)<>''),
    expected_minutes INTEGER NOT NULL CHECK (expected_minutes BETWEEN 1 AND 120),
    input_hash TEXT NOT NULL CHECK (length(input_hash)=64),
    status TEXT NOT NULL CHECK (status='approved'),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    evidence_hash TEXT NOT NULL UNIQUE CHECK (length(evidence_hash)=64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (question_id, textbook_id, curriculum_node_id)
      REFERENCES question_textbooks(question_id, textbook_id, curriculum_node_id)
      ON DELETE RESTRICT
);

CREATE INDEX idx_question_pedagogical_role_evidence_current
ON question_pedagogical_role_evidence(question_id, textbook_id, curriculum_node_id, role);

CREATE VIEW current_question_pedagogical_role_evidence AS
SELECT e.*
FROM question_pedagogical_role_evidence e
JOIN controlled_pedagogical_role_import_runs run
  ON run.id=e.import_run_id AND run.status='validated_evidence'
JOIN questions q
  ON q.id=e.question_id
 AND q.quality_status='approved'
 AND q.review_status='approved'
JOIN question_input_snapshots snapshot
  ON snapshot.question_id=e.question_id
 AND snapshot.input_hash=e.input_hash
 AND snapshot.invalidated_at IS NULL
JOIN question_textbooks mapping
  ON mapping.question_id=e.question_id
 AND mapping.textbook_id=e.textbook_id
 AND mapping.curriculum_node_id=e.curriculum_node_id
 AND mapping.fit_status='approved'
JOIN source_documents document
  ON document.id=e.source_document_id
 AND document.file_hash=e.source_document_hash
 AND document.parse_status='parsed'
JOIN source_fragments fragment
  ON fragment.id=e.source_fragment_id
 AND fragment.source_document_id=e.source_document_id
 AND fragment.raw_hash=e.source_hash
WHERE instr(fragment.raw_text, e.role_marker)>0;

CREATE TRIGGER trg_v32_role_evidence_insert_guard
BEFORE INSERT ON question_pedagogical_role_evidence
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM controlled_pedagogical_role_import_runs run
         WHERE run.id=NEW.import_run_id AND run.status='validated_evidence'
    ) THEN RAISE(ABORT, 'role evidence requires a validated evidence import run') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM questions q
         JOIN question_input_snapshots snapshot ON snapshot.question_id=q.id
         JOIN question_textbooks mapping
           ON mapping.question_id=q.id
          AND mapping.textbook_id=NEW.textbook_id
          AND mapping.curriculum_node_id=NEW.curriculum_node_id
         WHERE q.id=NEW.question_id
           AND q.quality_status='approved'
           AND q.review_status='approved'
           AND snapshot.input_hash=NEW.input_hash
           AND snapshot.invalidated_at IS NULL
           AND mapping.fit_status='approved'
    ) THEN RAISE(ABORT, 'role evidence requires a current approved question snapshot and mapping') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM source_documents document
         JOIN source_fragments fragment ON fragment.source_document_id=document.id
         WHERE document.id=NEW.source_document_id
           AND document.file_hash=NEW.source_document_hash
           AND document.parse_status='parsed'
           AND fragment.id=NEW.source_fragment_id
           AND fragment.raw_hash=NEW.source_hash
           AND instr(fragment.raw_text, NEW.role_marker)>0
    ) THEN RAISE(ABORT, 'role evidence requires a current explicit source marker') END;
END;

CREATE TRIGGER trg_v32_role_evidence_immutable_update
BEFORE UPDATE ON question_pedagogical_role_evidence
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'question pedagogical role evidence is append-only');
END;

CREATE TRIGGER trg_v32_role_evidence_immutable_delete
BEFORE DELETE ON question_pedagogical_role_evidence
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'question pedagogical role evidence is append-only');
END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.32-p14b-current-pedagogical-role-evidence', datetime('now'));

COMMIT;
