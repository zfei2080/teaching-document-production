import sqlite3
import unittest

from progress_control import (
    ProgressControlError,
    ProgressControlStorageError,
    authorize_question_reuse,
    ensure_plan_matches_active_progress,
    get_active_progress,
    get_question_reuse_authorization,
    set_active_progress,
)


class ProgressControlTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_minimal_contract()
        self._seed_base()

    def tearDown(self):
        self.conn.close()

    def _create_minimal_contract(self):
        self.conn.executescript(
            """
            CREATE TABLE textbooks (
                id TEXT PRIMARY KEY,
                name TEXT,
                catalog_version TEXT
            );
            CREATE TABLE curriculum_nodes (
                id TEXT PRIMARY KEY,
                textbook_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE classes (
                id TEXT PRIMARY KEY,
                textbook_id TEXT,
                status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE production_requests (
                id TEXT PRIMARY KEY,
                class_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE selection_plans (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                warnings_json TEXT
            );
            CREATE TABLE selection_plan_questions (
                selection_plan_id TEXT NOT NULL,
                question_id TEXT NOT NULL
            );
            CREATE TABLE question_textbooks (
                question_id TEXT NOT NULL,
                textbook_id TEXT NOT NULL,
                curriculum_node_id TEXT,
                fit_status TEXT NOT NULL
            );
            CREATE TABLE question_usage (
                id TEXT PRIMARY KEY,
                question_id TEXT NOT NULL,
                class_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                delivered INTEGER NOT NULL,
                reuse_allowed INTEGER NOT NULL DEFAULT 0,
                reuse_reason TEXT
            );
            CREATE TABLE class_progress_controls (
                id TEXT PRIMARY KEY,
                class_id TEXT NOT NULL,
                textbook_id TEXT NOT NULL,
                current_curriculum_node_id TEXT NOT NULL,
                allowed_nodes_json TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE class_progress_allowed_nodes (
                progress_id TEXT NOT NULL,
                curriculum_node_id TEXT NOT NULL
            );
            CREATE TABLE question_reuse_authorizations (
                id TEXT PRIMARY KEY,
                class_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                question_id TEXT NOT NULL,
                progress_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                invalidated_at TEXT,
                invalidation_reason TEXT,
                used_at TEXT,
                used_usage_id TEXT,
                UNIQUE (class_id, request_id, question_id)
            );

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
            END;

            CREATE TRIGGER trg_reuse_authorization_update_guard
            BEFORE UPDATE ON question_reuse_authorizations
            FOR EACH ROW
            BEGIN
                SELECT CASE WHEN NEW.class_id <> OLD.class_id OR NEW.request_id <> OLD.request_id OR NEW.question_id <> OLD.question_id OR NEW.progress_id <> OLD.progress_id
                    THEN RAISE(ABORT, 'reuse authorization identity is immutable') END;
            END;

            CREATE TRIGGER trg_reuse_authorization_delete_guard
            BEFORE DELETE ON question_reuse_authorizations
            FOR EACH ROW
            BEGIN
                SELECT RAISE(ABORT, 'reuse authorization cannot be deleted');
            END;

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
            END;
            """
        )

    def _seed_base(self):
        self.conn.execute("INSERT INTO textbooks VALUES ('book-a', '教材A', 'v1')")
        self.conn.execute("INSERT INTO textbooks VALUES ('book-b', '教材B', 'v1')")
        self.conn.execute("INSERT INTO classes VALUES ('class-a', 'book-a', 'active')")
        self.conn.execute("INSERT INTO classes VALUES ('class-b', 'book-b', 'active')")
        self.conn.execute("INSERT INTO curriculum_nodes VALUES ('node-current', 'book-a', 'active')")
        self.conn.execute("INSERT INTO curriculum_nodes VALUES ('node-prereq', 'book-a', 'active')")
        self.conn.execute("INSERT INTO curriculum_nodes VALUES ('node-future', 'book-a', 'active')")
        self.conn.execute("INSERT INTO curriculum_nodes VALUES ('node-cross', 'book-b', 'active')")
        self.conn.execute("INSERT INTO production_requests VALUES ('req-a', 'class-a', 'selected')")
        self.conn.execute("INSERT INTO production_requests VALUES ('req-a-2', 'class-a', 'passed')")
        self.conn.execute("INSERT INTO production_requests VALUES ('req-b', 'class-b', 'selected')")
        self.conn.execute("INSERT INTO production_requests VALUES ('req-closed', 'class-a', 'cancelled')")
        self.conn.execute("INSERT INTO selection_plans VALUES ('plan-a', 'req-a', 'draft', NULL)")
        self.conn.commit()

    def _activate_progress(self):
        return set_active_progress(
            self.conn,
            progress_id='progress-1',
            class_id='class-a',
            current_curriculum_node_id='node-current',
            allowed_curriculum_node_ids=['node-current', 'node-prereq'],
        )

    def test_storage_contract_is_required(self):
        broken = sqlite3.connect(":memory:")
        broken.execute("CREATE TABLE classes (id TEXT PRIMARY KEY, textbook_id TEXT)")
        with self.assertRaises(ProgressControlStorageError):
            get_active_progress(broken, 'class-a')
        broken.close()

    def test_set_and_replace_active_progress(self):
        active = self._activate_progress()
        self.assertEqual(active.allowed_curriculum_node_ids, ('node-current', 'node-prereq'))
        fetched = get_active_progress(self.conn, 'class-a')
        self.assertEqual(fetched.progress_id, 'progress-1')
        self.assertEqual(
            self.conn.execute("SELECT status FROM class_progress_controls WHERE id='progress-1'").fetchone()[0],
            'active',
        )

        replaced = set_active_progress(
            self.conn,
            progress_id='progress-2',
            class_id='class-a',
            current_curriculum_node_id='node-prereq',
            allowed_curriculum_node_ids=['node-prereq'],
        )
        self.assertEqual(replaced.progress_id, 'progress-2')
        self.assertEqual(
            self.conn.execute("SELECT status FROM class_progress_controls WHERE id='progress-1'").fetchone()[0],
            'replaced',
        )
        self.assertEqual(get_active_progress(self.conn, 'class-a').current_curriculum_node_id, 'node-prereq')

    def test_progress_requires_current_node_inside_allowed_set(self):
        with self.assertRaises(ProgressControlError):
            set_active_progress(
                self.conn,
                progress_id='progress-bad',
                class_id='class-a',
                current_curriculum_node_id='node-current',
                allowed_curriculum_node_ids=['node-prereq'],
            )

    def test_progress_blocks_cross_textbook_nodes(self):
        with self.assertRaises(ProgressControlError):
            set_active_progress(
                self.conn,
                progress_id='progress-cross',
                class_id='class-a',
                current_curriculum_node_id='node-cross',
                allowed_curriculum_node_ids=['node-cross'],
            )
        with self.assertRaises(ProgressControlError):
            set_active_progress(
                self.conn,
                progress_id='progress-cross-2',
                class_id='class-a',
                current_curriculum_node_id='node-current',
                allowed_curriculum_node_ids=['node-current', 'node-cross'],
            )

    def test_get_active_progress_fails_closed_when_missing(self):
        with self.assertRaises(ProgressControlError):
            get_active_progress(self.conn, 'class-a')

    def test_plan_must_match_active_progress(self):
        self._activate_progress()
        self.conn.execute("INSERT INTO selection_plan_questions VALUES ('plan-a', 'q1')")
        self.conn.execute("INSERT INTO selection_plan_questions VALUES ('plan-a', 'q2')")
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'book-a', 'node-current', 'approved')")
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q2', 'book-a', 'node-prereq', 'approved')")
        active = ensure_plan_matches_active_progress(self.conn, 'plan-a')
        self.assertEqual(active.progress_id, 'progress-1')

    def test_plan_blocks_future_or_cross_textbook_nodes(self):
        self._activate_progress()
        self.conn.execute("INSERT INTO selection_plan_questions VALUES ('plan-a', 'q1')")
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'book-a', 'node-future', 'approved')")
        with self.assertRaises(ProgressControlError):
            ensure_plan_matches_active_progress(self.conn, 'plan-a')
        self.conn.execute("DELETE FROM selection_plan_questions")
        self.conn.execute("DELETE FROM question_textbooks")
        self.conn.execute("INSERT INTO selection_plan_questions VALUES ('plan-a', 'q2')")
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q2', 'book-b', 'node-cross', 'approved')")
        with self.assertRaises(ProgressControlError):
            ensure_plan_matches_active_progress(self.conn, 'plan-a')

    def test_authorize_reuse_requires_active_progress_and_reason(self):
        self.conn.execute(
            "INSERT INTO question_usage VALUES ('usage-1', 'q1', 'class-a', 'doc-1', 1, 0, NULL)"
        )
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'book-a', 'node-current', 'approved')")
        with self.assertRaises(ProgressControlError):
            authorize_question_reuse(
                self.conn,
                authorization_id='auth-1',
                class_id='class-a',
                request_id='req-a',
                question_id='q1',
                reason='  ',
            )
        with self.assertRaises(ProgressControlError):
            authorize_question_reuse(
                self.conn,
                authorization_id='auth-2',
                class_id='class-a',
                request_id='req-a',
                question_id='q1',
                reason='错题回练',
            )

    def test_authorize_and_read_reuse_authorization(self):
        self._activate_progress()
        self.conn.execute(
            "INSERT INTO question_usage VALUES ('usage-1', 'q1', 'class-a', 'doc-1', 1, 0, NULL)"
        )
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'book-a', 'node-current', 'approved')")
        auth = authorize_question_reuse(
            self.conn,
            authorization_id='auth-1',
            class_id='class-a',
            request_id='req-a',
            question_id='q1',
            reason='错题回练',
        )
        self.assertEqual(auth.progress_id, 'progress-1')
        fetched = get_question_reuse_authorization(self.conn, class_id='class-a', request_id='req-a', question_id='q1')
        self.assertEqual(fetched.authorization_id, 'auth-1')
        self.assertEqual(fetched.reason, '错题回练')

    def test_authorize_reuse_blocks_duplicate_cross_textbook_and_out_of_progress(self):
        self._activate_progress()
        self.conn.execute(
            "INSERT INTO question_usage VALUES ('usage-1', 'q1', 'class-a', 'doc-1', 1, 0, NULL)"
        )
        self.conn.execute(
            "INSERT INTO question_usage VALUES ('usage-2', 'q2', 'class-b', 'doc-2', 1, 0, NULL)"
        )
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'book-a', 'node-current', 'approved')")
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q2', 'book-b', 'node-cross', 'approved')")
        authorize_question_reuse(
            self.conn,
            authorization_id='auth-1',
            class_id='class-a',
            request_id='req-a',
            question_id='q1',
            reason='错题回练',
        )
        with self.assertRaises(ProgressControlError):
            authorize_question_reuse(
                self.conn,
                authorization_id='auth-dup',
                class_id='class-a',
                request_id='req-a',
                question_id='q1',
                reason='再次重复',
            )
        with self.assertRaises(ProgressControlError):
            authorize_question_reuse(
                self.conn,
                authorization_id='auth-cross',
                class_id='class-a',
                request_id='req-a',
                question_id='q2',
                reason='跨教材',
            )
        self.conn.execute("INSERT INTO question_usage VALUES ('usage-3', 'q3', 'class-a', 'doc-3', 1, 0, NULL)")
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q3', 'book-a', 'node-future', 'approved')")
        with self.assertRaises(ProgressControlError):
            authorize_question_reuse(
                self.conn,
                authorization_id='auth-future',
                class_id='class-a',
                request_id='req-a',
                question_id='q3',
                reason='超前节点',
            )

    def test_get_reuse_authorization_blocks_mismatched_progress(self):
        self._activate_progress()
        self.conn.execute(
            "INSERT INTO question_usage VALUES ('usage-1', 'q1', 'class-a', 'doc-1', 1, 0, NULL)"
        )
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'book-a', 'node-current', 'approved')")
        authorize_question_reuse(
            self.conn,
            authorization_id='auth-1',
            class_id='class-a',
            request_id='req-a',
            question_id='q1',
            reason='错题回练',
        )
        set_active_progress(
            self.conn,
            progress_id='progress-2',
            class_id='class-a',
            current_curriculum_node_id='node-prereq',
            allowed_curriculum_node_ids=['node-prereq'],
        )
        with self.assertRaises(ProgressControlError):
            get_question_reuse_authorization(self.conn, class_id='class-a', request_id='req-a', question_id='q1')

    def test_authorize_reuse_allows_different_requests_but_same_class_when_explicit(self):
        self._activate_progress()
        self.conn.execute(
            "INSERT INTO question_usage VALUES ('usage-1', 'q1', 'class-a', 'doc-1', 1, 0, NULL)"
        )
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'book-a', 'node-current', 'approved')")
        first = authorize_question_reuse(
            self.conn,
            authorization_id='auth-1',
            class_id='class-a',
            request_id='req-a',
            question_id='q1',
            reason='错题回练',
        )
        second = authorize_question_reuse(
            self.conn,
            authorization_id='auth-2',
            class_id='class-a',
            request_id='req-a-2',
            question_id='q1',
            reason='阶段复盘',
        )
        self.assertEqual(first.request_id, 'req-a')
        self.assertEqual(second.request_id, 'req-a-2')

    def test_authorize_reuse_blocks_request_class_or_status_mismatch(self):
        self._activate_progress()
        self.conn.execute(
            "INSERT INTO question_usage VALUES ('usage-1', 'q1', 'class-a', 'doc-1', 1, 0, NULL)"
        )
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'book-a', 'node-current', 'approved')")
        with self.assertRaises(ProgressControlError):
            authorize_question_reuse(
                self.conn,
                authorization_id='auth-bad-class',
                class_id='class-a',
                request_id='req-b',
                question_id='q1',
                reason='跨班',
            )
        with self.assertRaises(ProgressControlError):
            authorize_question_reuse(
                self.conn,
                authorization_id='auth-bad-status',
                class_id='class-a',
                request_id='req-closed',
                question_id='q1',
                reason='非活跃请求',
            )

    def test_trigger_blocks_request_class_mismatch_and_progress_change_invalidates_old_authorization(self):
        self._activate_progress()
        self.conn.execute(
            "INSERT INTO question_usage VALUES ('usage-1', 'q1', 'class-a', 'doc-1', 1, 0, NULL)"
        )
        self.conn.execute("INSERT INTO question_textbooks VALUES ('q1', 'book-a', 'node-current', 'approved')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO question_reuse_authorizations
                (id, class_id, request_id, question_id, progress_id, reason, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
                """,
                ('auth-trigger-bad', 'class-a', 'req-b', 'q1', 'progress-1', 'bad'),
            )
        authorize_question_reuse(
            self.conn,
            authorization_id='auth-1',
            class_id='class-a',
            request_id='req-a',
            question_id='q1',
            reason='错题回练',
        )
        set_active_progress(
            self.conn,
            progress_id='progress-2',
            class_id='class-a',
            current_curriculum_node_id='node-prereq',
            allowed_curriculum_node_ids=['node-prereq'],
        )
        row = self.conn.execute(
            "SELECT status, invalidation_reason FROM question_reuse_authorizations WHERE id='auth-1'"
        ).fetchone()
        self.assertEqual(row, ('invalidated', 'progress_changed'))


if __name__ == '__main__':
    unittest.main()
