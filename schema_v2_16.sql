PRAGMA foreign_keys = ON;

-- P1-1: fail-closed class progress and per-question reuse authorization state.
-- This migration adds only new tables/triggers and tightens delivery-time checks.
-- It never infers prerequisite relationships from curriculum trees.

CREATE TABLE IF NOT EXISTS class_progress_controls (
    id TEXT PRIMARY KEY,
    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    textbook_id TEXT NOT NULL REFERENCES textbooks(id),
    current_curriculum_node_id TEXT NOT NULL REFERENCES curriculum_nodes(id),
    allowed_nodes_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'replaced', 'invalidated')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    replaced_at TEXT,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    CHECK (TRIM(id) <> ''),
    CHECK (TRIM(class_id) <> ''),
    CHECK (TRIM(textbook_id) <> ''),
    CHECK (TRIM(current_curriculum_node_id) <> ''),
    CHECK (TRIM(allowed_nodes_json) <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_class_progress_one_active
ON class_progress_controls(class_id)
WHERE status = 'active';

CREATE TABLE IF NOT EXISTS class_progress_allowed_nodes (
    progress_id TEXT NOT NULL REFERENCES class_progress_controls(id) ON DELETE CASCADE,
    curriculum_node_id TEXT NOT NULL REFERENCES curriculum_nodes(id),
    PRIMARY KEY (progress_id, curriculum_node_id)
);

CREATE INDEX IF NOT EXISTS idx_class_progress_allowed_nodes_node
ON class_progress_allowed_nodes(curriculum_node_id, progress_id);

CREATE TABLE IF NOT EXISTS question_reuse_authorizations (
    id TEXT PRIMARY KEY,
    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL REFERENCES production_requests(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    progress_id TEXT NOT NULL REFERENCES class_progress_controls(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invalidated', 'used', 'revoked')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    used_at TEXT,
    used_usage_id TEXT,
    CHECK (TRIM(id) <> ''),
    CHECK (TRIM(class_id) <> ''),
    CHECK (TRIM(request_id) <> ''),
    CHECK (TRIM(question_id) <> ''),
    CHECK (TRIM(progress_id) <> ''),
    CHECK (TRIM(reason) <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reuse_authorization_one_active_per_request_question
ON question_reuse_authorizations(class_id, request_id, question_id)
WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_reuse_authorization_lookup
ON question_reuse_authorizations(class_id, question_id, progress_id, status);

DROP TRIGGER IF EXISTS trg_class_progress_insert_guard;
CREATE TRIGGER trg_class_progress_insert_guard
BEFORE INSERT ON class_progress_controls
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NEW.status <> 'active'
        THEN RAISE(ABORT, 'class progress must be inserted as active') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM classes c WHERE c.id = NEW.class_id AND c.status = 'active'
    ) THEN RAISE(ABORT, 'class progress requires active class') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM curriculum_nodes n WHERE n.id = NEW.current_curriculum_node_id AND n.status = 'active'
    ) THEN RAISE(ABORT, 'class progress requires active current curriculum node') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM classes c
        WHERE c.id = NEW.class_id AND c.textbook_id = NEW.textbook_id
    ) THEN RAISE(ABORT, 'class progress textbook must match class textbook') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM curriculum_nodes n
        WHERE n.id = NEW.current_curriculum_node_id AND n.textbook_id = NEW.textbook_id
    ) THEN RAISE(ABORT, 'class progress current node must belong to class textbook') END;
    SELECT CASE WHEN json_valid(NEW.allowed_nodes_json) <> 1
        THEN RAISE(ABORT, 'class progress allowed_nodes_json must be valid json') END;
    SELECT CASE WHEN json_type(NEW.allowed_nodes_json) <> 'array'
        THEN RAISE(ABORT, 'class progress allowed_nodes_json must be a json array') END;
    SELECT CASE WHEN json_array_length(NEW.allowed_nodes_json) < 1
        THEN RAISE(ABORT, 'class progress allowed node set cannot be empty') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM json_each(NEW.allowed_nodes_json) j
        WHERE TRIM(CAST(j.value AS TEXT)) = NEW.current_curriculum_node_id
    ) THEN RAISE(ABORT, 'allowed curriculum node set must include current curriculum node') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM json_each(NEW.allowed_nodes_json) j
        LEFT JOIN curriculum_nodes n ON n.id = TRIM(CAST(j.value AS TEXT))
        WHERE n.id IS NULL
           OR n.status <> 'active'
           OR n.textbook_id <> NEW.textbook_id
    ) THEN RAISE(ABORT, 'allowed curriculum nodes must all be active and match class textbook') END;
END;

DROP TRIGGER IF EXISTS trg_class_progress_update_guard;
CREATE TRIGGER trg_class_progress_update_guard
BEFORE UPDATE ON class_progress_controls
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NEW.class_id <> OLD.class_id
        THEN RAISE(ABORT, 'class progress class_id is immutable') END;
    SELECT CASE WHEN NEW.textbook_id <> OLD.textbook_id
        THEN RAISE(ABORT, 'class progress textbook_id is immutable') END;
    SELECT CASE WHEN NEW.current_curriculum_node_id <> OLD.current_curriculum_node_id AND NEW.status = OLD.status
        THEN RAISE(ABORT, 'replace class progress instead of mutating current node') END;
    SELECT CASE WHEN NEW.allowed_nodes_json <> OLD.allowed_nodes_json AND NEW.status = OLD.status
        THEN RAISE(ABORT, 'replace class progress instead of mutating allowed nodes') END;
    SELECT CASE WHEN OLD.status = 'active' AND NEW.status NOT IN ('active', 'replaced', 'invalidated')
        THEN RAISE(ABORT, 'invalid class progress status transition') END;
    SELECT CASE WHEN OLD.status = 'replaced' AND NEW.status <> 'replaced'
        THEN RAISE(ABORT, 'replaced class progress is immutable') END;
    SELECT CASE WHEN OLD.status = 'invalidated' AND NEW.status <> 'invalidated'
        THEN RAISE(ABORT, 'invalidated class progress is immutable') END;
    SELECT CASE WHEN NEW.status = 'active' AND OLD.status <> 'active'
        THEN RAISE(ABORT, 'inactive class progress cannot be reactivated') END;
END;

DROP TRIGGER IF EXISTS trg_class_progress_allowed_node_insert_guard;
CREATE TRIGGER trg_class_progress_allowed_node_insert_guard
BEFORE INSERT ON class_progress_allowed_nodes
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM class_progress_controls cpc
        WHERE cpc.id = NEW.progress_id AND cpc.status = 'active'
    ) THEN RAISE(ABORT, 'allowed curriculum node requires active class progress') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM class_progress_controls cpc
        JOIN curriculum_nodes n ON n.id = NEW.curriculum_node_id
        WHERE cpc.id = NEW.progress_id
          AND n.status = 'active'
          AND n.textbook_id = cpc.textbook_id
    ) THEN RAISE(ABORT, 'allowed curriculum node must be active and match progress textbook') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM class_progress_controls cpc, json_each(cpc.allowed_nodes_json) j
        WHERE cpc.id = NEW.progress_id
          AND TRIM(CAST(j.value AS TEXT)) = NEW.curriculum_node_id
    ) THEN RAISE(ABORT, 'allowed curriculum node row must be present in allowed_nodes_json') END;
END;

DROP TRIGGER IF EXISTS trg_class_progress_allowed_node_update_guard;
CREATE TRIGGER trg_class_progress_allowed_node_update_guard
BEFORE UPDATE ON class_progress_allowed_nodes
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'class progress allowed nodes are immutable');
END;

DROP TRIGGER IF EXISTS trg_class_progress_allowed_node_delete_guard;
CREATE TRIGGER trg_class_progress_allowed_node_delete_guard
BEFORE DELETE ON class_progress_allowed_nodes
FOR EACH ROW
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM class_progress_controls cpc
        WHERE cpc.id = OLD.progress_id AND cpc.status = 'active'
    ) THEN RAISE(ABORT, 'active class progress allowed nodes cannot be deleted directly') END;
END;

DROP TRIGGER IF EXISTS trg_reuse_authorization_insert_guard;
CREATE TRIGGER trg_reuse_authorization_insert_guard
BEFORE INSERT ON question_reuse_authorizations
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NEW.status <> 'active'
        THEN RAISE(ABORT, 'reuse authorization must be inserted as active') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM class_progress_controls cpc
        WHERE cpc.id = NEW.progress_id AND cpc.class_id = NEW.class_id AND cpc.status = 'active'
    ) THEN RAISE(ABORT, 'reuse authorization requires current active class progress') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM production_requests pr
        WHERE pr.id = NEW.request_id AND pr.class_id = NEW.class_id AND pr.status IN ('selected', 'generating', 'validating', 'passed')
    ) THEN RAISE(ABORT, 'reuse authorization requires same-class live production request') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM question_usage qu
        WHERE qu.class_id = NEW.class_id AND qu.question_id = NEW.question_id AND qu.delivered = 1
    ) THEN RAISE(ABORT, 'reuse authorization requires prior delivered usage in same class') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM class_progress_controls cpc
        JOIN question_textbooks qt ON qt.question_id = NEW.question_id
        WHERE cpc.id = NEW.progress_id
          AND qt.fit_status = 'approved'
          AND qt.textbook_id = cpc.textbook_id
          AND qt.curriculum_node_id = cpc.current_curriculum_node_id
    ) AND NOT EXISTS (
        SELECT 1
        FROM class_progress_controls cpc
        JOIN class_progress_allowed_nodes cpn ON cpn.progress_id = cpc.id
        JOIN question_textbooks qt ON qt.question_id = NEW.question_id AND qt.curriculum_node_id = cpn.curriculum_node_id
        WHERE cpc.id = NEW.progress_id
          AND qt.fit_status = 'approved'
          AND qt.textbook_id = cpc.textbook_id
    ) THEN RAISE(ABORT, 'reuse authorization question must match the active progress textbook and allowed node set') END;
END;

DROP TRIGGER IF EXISTS trg_reuse_authorization_update_guard;
CREATE TRIGGER trg_reuse_authorization_update_guard
BEFORE UPDATE ON question_reuse_authorizations
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NEW.class_id <> OLD.class_id OR NEW.request_id <> OLD.request_id OR NEW.question_id <> OLD.question_id OR NEW.progress_id <> OLD.progress_id
        THEN RAISE(ABORT, 'reuse authorization identity is immutable') END;
    SELECT CASE WHEN OLD.status = 'active' AND NEW.status NOT IN ('active', 'invalidated', 'used', 'revoked')
        THEN RAISE(ABORT, 'invalid reuse authorization status transition') END;
    SELECT CASE WHEN OLD.status IN ('invalidated', 'used', 'revoked') AND NEW.status <> OLD.status
        THEN RAISE(ABORT, 'inactive reuse authorization is immutable') END;
    SELECT CASE WHEN NEW.status = 'used' AND (NEW.used_usage_id IS NULL OR TRIM(NEW.used_usage_id) = '')
        THEN RAISE(ABORT, 'used reuse authorization requires used_usage_id') END;
    SELECT CASE WHEN NEW.status = 'invalidated' AND (NEW.invalidation_reason IS NULL OR TRIM(NEW.invalidation_reason) = '')
        THEN RAISE(ABORT, 'invalidated reuse authorization requires reason') END;
END;

DROP TRIGGER IF EXISTS trg_reuse_authorization_delete_guard;
CREATE TRIGGER trg_reuse_authorization_delete_guard
BEFORE DELETE ON question_reuse_authorizations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'reuse authorization cannot be deleted');
END;

DROP TRIGGER IF EXISTS trg_selection_plan_question_progress_guard;
CREATE TRIGGER trg_selection_plan_question_progress_guard
BEFORE INSERT ON selection_plan_questions
FOR EACH ROW
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM selection_plans sp
        JOIN production_requests pr ON pr.id = sp.request_id
        JOIN class_progress_controls cpc ON cpc.class_id = pr.class_id AND cpc.status = 'active'
        JOIN question_textbooks qt ON qt.question_id = NEW.question_id AND qt.textbook_id = cpc.textbook_id AND qt.fit_status = 'approved'
        LEFT JOIN class_progress_allowed_nodes cpn ON cpn.progress_id = cpc.id AND cpn.curriculum_node_id = qt.curriculum_node_id
        WHERE sp.id = NEW.selection_plan_id
          AND (qt.curriculum_node_id = cpc.current_curriculum_node_id OR cpn.curriculum_node_id IS NOT NULL)
    ) THEN RAISE(ABORT, 'selection plan question must match active class progress') END;
END;

DROP TRIGGER IF EXISTS trg_question_usage_reuse_authorization_guard;
CREATE TRIGGER trg_question_usage_reuse_authorization_guard
BEFORE INSERT ON question_usage
FOR EACH ROW
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM question_usage prior
        WHERE prior.question_id = NEW.question_id
          AND prior.class_id = NEW.class_id
          AND prior.delivered = 1
    ) AND COALESCE(NEW.reuse_allowed, 0) = 0
        THEN RAISE(ABORT, 'duplicate delivered question requires matching reuse authorization') END;

    SELECT CASE WHEN COALESCE(NEW.reuse_allowed, 0) = 1 AND NOT EXISTS (
        SELECT 1
        FROM teaching_documents td
        JOIN production_requests pr ON pr.id = td.request_id
        JOIN class_progress_controls cpc ON cpc.class_id = td.class_id AND cpc.status = 'active'
        JOIN question_reuse_authorizations qra
          ON qra.class_id = td.class_id
         AND qra.request_id = pr.id
         AND qra.question_id = NEW.question_id
         AND qra.progress_id = cpc.id
         AND qra.status = 'active'
        WHERE td.id = NEW.document_id
          AND td.class_id = NEW.class_id
    ) THEN RAISE(ABORT, 'reuse authorization must match class/request/question/current progress') END;

    SELECT CASE WHEN COALESCE(NEW.reuse_allowed, 0) = 1 AND NOT EXISTS (
        SELECT 1
        FROM question_reuse_authorizations qra
        WHERE qra.class_id = NEW.class_id
          AND qra.question_id = NEW.question_id
          AND qra.status = 'active'
          AND TRIM(qra.reason) = TRIM(COALESCE(NEW.reuse_reason, ''))
    ) THEN RAISE(ABORT, 'reuse_reason must match active reuse authorization') END;
END;

DROP TRIGGER IF EXISTS trg_question_usage_mark_authorization_used;
CREATE TRIGGER trg_question_usage_mark_authorization_used
AFTER INSERT ON question_usage
FOR EACH ROW
WHEN COALESCE(NEW.reuse_allowed, 0) = 1
BEGIN
    UPDATE question_reuse_authorizations
       SET status = 'used',
           used_at = CURRENT_TIMESTAMP,
           used_usage_id = NEW.id
     WHERE id = (
        SELECT qra.id
        FROM teaching_documents td
        JOIN production_requests pr ON pr.id = td.request_id
        JOIN class_progress_controls cpc ON cpc.class_id = td.class_id AND cpc.status = 'active'
        JOIN question_reuse_authorizations qra
          ON qra.class_id = td.class_id
         AND qra.request_id = pr.id
         AND qra.question_id = NEW.question_id
         AND qra.progress_id = cpc.id
         AND qra.status = 'active'
        WHERE td.id = NEW.document_id
          AND td.class_id = NEW.class_id
        LIMIT 1
     );
END;

DROP TRIGGER IF EXISTS trg_progress_change_invalidates_prior_state;
CREATE TRIGGER trg_progress_change_invalidates_prior_state
AFTER INSERT ON class_progress_controls
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM class_progress_controls prior
    WHERE prior.class_id = NEW.class_id
      AND prior.id <> NEW.id
      AND prior.status IN ('active', 'replaced')
)
BEGIN
    UPDATE question_reuse_authorizations
       SET status = 'invalidated',
           invalidated_at = CURRENT_TIMESTAMP,
           invalidation_reason = 'progress_changed'
     WHERE class_id = NEW.class_id
       AND status = 'active'
       AND progress_id <> NEW.id;

    UPDATE selection_plans
       SET status = 'voided',
           warnings_json = json_insert(COALESCE(NULLIF(warnings_json, ''), '[]'), '$[#]', 'invalidated:class_progress_changed')
     WHERE id IN (
        SELECT sp.id
        FROM selection_plans sp
        JOIN production_requests pr ON pr.id = sp.request_id
        WHERE pr.class_id = NEW.class_id
          AND sp.status IN ('draft', 'ready')
    );
END;

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('v2.16-class-progress-reuse-authorization-2026-07-26');
