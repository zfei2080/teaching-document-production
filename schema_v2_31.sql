PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

-- P1-4b keeps verifier-only answer evidence outside student-visible controlled
-- content.  A derived DOCX source can be used for a question only when its
-- prompt is contained by a current student-safe segment and its answer and
-- analysis have separate non-overlapping internal evidence records.
CREATE TABLE controlled_question_source_import_runs (
    id TEXT PRIMARY KEY,
    manifest_sha256 TEXT NOT NULL UNIQUE CHECK (length(manifest_sha256)=64),
    importer_id TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status='validated'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE controlled_question_answer_evidence (
    id TEXT PRIMARY KEY,
    import_run_id TEXT NOT NULL REFERENCES controlled_question_source_import_runs(id) ON DELETE RESTRICT,
    controlled_source_id TEXT NOT NULL REFERENCES controlled_content_sources(id) ON DELETE RESTRICT,
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    source_question_no TEXT NOT NULL CHECK (TRIM(source_question_no)<>''),
    field_name TEXT NOT NULL CHECK (field_name IN ('answer','analysis')),
    source_character_range_json TEXT NOT NULL CHECK (json_valid(source_character_range_json)),
    normalized_text_sha256 TEXT NOT NULL CHECK (length(normalized_text_sha256)=64),
    internal_only INTEGER NOT NULL CHECK (internal_only=1),
    evidence_hash TEXT NOT NULL UNIQUE CHECK (length(evidence_hash)=64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(controlled_source_id, textbook_id, curriculum_node_id, source_question_no, field_name, evidence_hash)
);

CREATE TABLE controlled_question_source_derivations (
    source_document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
    source_question_no TEXT NOT NULL CHECK (TRIM(source_question_no)<>''),
    import_run_id TEXT NOT NULL REFERENCES controlled_question_source_import_runs(id) ON DELETE RESTRICT,
    controlled_source_id TEXT NOT NULL REFERENCES controlled_content_sources(id) ON DELETE RESTRICT,
    student_segment_row_id TEXT NOT NULL REFERENCES controlled_content_segments(id) ON DELETE RESTRICT,
    textbook_id TEXT NOT NULL,
    curriculum_node_id TEXT NOT NULL,
    student_prompt_range_json TEXT NOT NULL CHECK (json_valid(student_prompt_range_json)),
    student_prompt_sha256 TEXT NOT NULL CHECK (length(student_prompt_sha256)=64),
    derivation_hash TEXT NOT NULL UNIQUE CHECK (length(derivation_hash)=64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_document_id, source_question_no)
);

CREATE INDEX idx_controlled_question_answer_evidence_source
ON controlled_question_answer_evidence(controlled_source_id, textbook_id, curriculum_node_id, source_question_no);

CREATE VIEW current_controlled_question_answer_evidence AS
SELECT e.*
FROM controlled_question_answer_evidence e
JOIN controlled_question_source_import_runs run ON run.id=e.import_run_id AND run.status='validated'
JOIN current_controlled_content_segments segment
  ON segment.source_id=e.controlled_source_id
 AND segment.textbook_id=e.textbook_id
 AND segment.curriculum_node_id=e.curriculum_node_id
WHERE NOT EXISTS (
    SELECT 1 FROM current_controlled_content_segments student_segment
     WHERE student_segment.source_id=e.controlled_source_id
       AND student_segment.textbook_id=e.textbook_id
       AND student_segment.curriculum_node_id=e.curriculum_node_id
       AND json_extract(e.source_character_range_json, '$[0]') < json_extract(student_segment.source_character_range_json, '$[1]')
       AND json_extract(e.source_character_range_json, '$[1]') > json_extract(student_segment.source_character_range_json, '$[0]')
);

CREATE VIEW current_controlled_question_source_derivations AS
SELECT d.*
FROM controlled_question_source_derivations d
JOIN controlled_question_source_import_runs run ON run.id=d.import_run_id AND run.status='validated'
JOIN source_documents document ON document.id=d.source_document_id
JOIN controlled_content_sources source ON source.id=d.controlled_source_id
JOIN current_controlled_content_segments segment
  ON segment.id=d.student_segment_row_id
 AND segment.source_id=d.controlled_source_id
 AND segment.textbook_id=d.textbook_id
 AND segment.curriculum_node_id=d.curriculum_node_id
WHERE document.file_type='docx'
  AND document.parse_status='parsed'
  AND document.file_hash=source.converted_sha256
  AND EXISTS (
      SELECT 1 FROM current_controlled_question_answer_evidence answer_evidence
       WHERE answer_evidence.import_run_id=d.import_run_id
         AND answer_evidence.controlled_source_id=d.controlled_source_id
         AND answer_evidence.textbook_id=d.textbook_id
         AND answer_evidence.curriculum_node_id=d.curriculum_node_id
         AND answer_evidence.source_question_no=d.source_question_no
         AND answer_evidence.field_name='answer'
  )
  AND EXISTS (
      SELECT 1 FROM current_controlled_question_answer_evidence analysis_evidence
       WHERE analysis_evidence.import_run_id=d.import_run_id
         AND analysis_evidence.controlled_source_id=d.controlled_source_id
         AND analysis_evidence.textbook_id=d.textbook_id
         AND analysis_evidence.curriculum_node_id=d.curriculum_node_id
         AND analysis_evidence.source_question_no=d.source_question_no
         AND analysis_evidence.field_name='analysis'
  );

CREATE TRIGGER trg_v31_question_answer_evidence_insert_guard
BEFORE INSERT ON controlled_question_answer_evidence
FOR EACH ROW
BEGIN
    SELECT CASE WHEN json_type(NEW.source_character_range_json) <> 'array'
                      OR json_array_length(NEW.source_character_range_json) <> 2
                      OR json_type(NEW.source_character_range_json, '$[0]') <> 'integer'
                      OR json_type(NEW.source_character_range_json, '$[1]') <> 'integer'
                      OR json_extract(NEW.source_character_range_json, '$[0]') < 0
                      OR json_extract(NEW.source_character_range_json, '$[1]') <= json_extract(NEW.source_character_range_json, '$[0]')
      THEN RAISE(ABORT, 'answer evidence range must be a positive integer pair') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM controlled_question_source_import_runs run
         WHERE run.id=NEW.import_run_id AND run.status='validated'
    ) THEN RAISE(ABORT, 'answer evidence requires a validated controlled import run') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM current_controlled_content_segments segment
         WHERE segment.source_id=NEW.controlled_source_id
           AND segment.textbook_id=NEW.textbook_id
           AND segment.curriculum_node_id=NEW.curriculum_node_id
    ) THEN RAISE(ABORT, 'answer evidence requires a current controlled source') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM current_controlled_content_segments segment
         WHERE segment.source_id=NEW.controlled_source_id
           AND segment.textbook_id=NEW.textbook_id
           AND segment.curriculum_node_id=NEW.curriculum_node_id
           AND json_extract(NEW.source_character_range_json, '$[0]') < json_extract(segment.source_character_range_json, '$[1]')
           AND json_extract(NEW.source_character_range_json, '$[1]') > json_extract(segment.source_character_range_json, '$[0]')
    ) THEN RAISE(ABORT, 'internal answer evidence must not overlap student-safe content') END;
END;

CREATE TRIGGER trg_v31_question_answer_evidence_immutable_update
BEFORE UPDATE ON controlled_question_answer_evidence
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled question answer evidence is append-only');
END;

CREATE TRIGGER trg_v31_question_answer_evidence_immutable_delete
BEFORE DELETE ON controlled_question_answer_evidence
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled question answer evidence is append-only');
END;

CREATE TRIGGER trg_v31_question_source_derivation_insert_guard
BEFORE INSERT ON controlled_question_source_derivations
FOR EACH ROW
BEGIN
    SELECT CASE WHEN json_type(NEW.student_prompt_range_json) <> 'array'
                      OR json_array_length(NEW.student_prompt_range_json) <> 2
                      OR json_type(NEW.student_prompt_range_json, '$[0]') <> 'integer'
                      OR json_type(NEW.student_prompt_range_json, '$[1]') <> 'integer'
                      OR json_extract(NEW.student_prompt_range_json, '$[0]') < 0
                      OR json_extract(NEW.student_prompt_range_json, '$[1]') <= json_extract(NEW.student_prompt_range_json, '$[0]')
      THEN RAISE(ABORT, 'derived prompt range must be a positive integer pair') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM controlled_question_source_import_runs run
         WHERE run.id=NEW.import_run_id AND run.status='validated'
    ) THEN RAISE(ABORT, 'derived question source requires a validated import run') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
          FROM source_documents document
          JOIN controlled_content_sources source ON source.id=NEW.controlled_source_id
         WHERE document.id=NEW.source_document_id
           AND document.file_type='docx'
           AND document.parse_status='parsed'
           AND document.file_hash=source.converted_sha256
    ) THEN RAISE(ABORT, 'derived question source must bind the current converted DOCX hash') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM current_controlled_content_segments segment
         WHERE segment.id=NEW.student_segment_row_id
           AND segment.source_id=NEW.controlled_source_id
           AND segment.textbook_id=NEW.textbook_id
           AND segment.curriculum_node_id=NEW.curriculum_node_id
           AND json_extract(NEW.student_prompt_range_json, '$[0]') >= json_extract(segment.source_character_range_json, '$[0]')
           AND json_extract(NEW.student_prompt_range_json, '$[1]') <= json_extract(segment.source_character_range_json, '$[1]')
    ) THEN RAISE(ABORT, 'derived question prompt must stay inside a current student-safe segment') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM current_controlled_question_answer_evidence evidence
         WHERE evidence.import_run_id=NEW.import_run_id
           AND evidence.controlled_source_id=NEW.controlled_source_id
           AND evidence.textbook_id=NEW.textbook_id
           AND evidence.curriculum_node_id=NEW.curriculum_node_id
           AND evidence.source_question_no=NEW.source_question_no
           AND evidence.field_name='answer'
    ) THEN RAISE(ABORT, 'derived question source requires internal answer evidence') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM current_controlled_question_answer_evidence evidence
         WHERE evidence.import_run_id=NEW.import_run_id
           AND evidence.controlled_source_id=NEW.controlled_source_id
           AND evidence.textbook_id=NEW.textbook_id
           AND evidence.curriculum_node_id=NEW.curriculum_node_id
           AND evidence.source_question_no=NEW.source_question_no
           AND evidence.field_name='analysis'
    ) THEN RAISE(ABORT, 'derived question source requires internal analysis evidence') END;
END;

CREATE TRIGGER trg_v31_question_source_derivation_immutable_update
BEFORE UPDATE ON controlled_question_source_derivations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled question source derivations are append-only');
END;

CREATE TRIGGER trg_v31_question_source_derivation_immutable_delete
BEFORE DELETE ON controlled_question_source_derivations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'controlled question source derivations are append-only');
END;

INSERT INTO schema_migrations(version, applied_at)
VALUES('v2.31-p14b-controlled-question-source-derivations', datetime('now'));

COMMIT;
