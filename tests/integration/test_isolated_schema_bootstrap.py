"""Isolated fresh-database bootstrap integration proof (ISOLATED-BOOTSTRAP-001,
hardened by ISOLATED-BOOTSTRAP-SAFETY-001).

This suite proves the runtime prerequisite identified by SCHEMA-AUTHORITY-001:
``tools/bootstrap_isolated_schema.py`` creates a brand-new SQLite database from
``schema_v2.sql`` plus ``schema_v2_1.sql`` ... ``schema_v2_39.sql`` in numeric
order with exactly 40 expected ledger versions, preserving the wrapper-sensitive
behavior of v2.17/v2.24/v2.25/v2.29/v2.37/v2.39 and ending with clean
``PRAGMA foreign_key_check``.

The SAFETY-001 hardening proves the approved-root policy: only fresh temporary
roots (lexically under ``tempfile.gettempdir()``) or explicitly marked
controlled roots under one explicitly configured parent are admitted;
repository roots, repository subdirectories, forbidden directory families
(``<local-scratch>/``, data, source, backup, output, collection, KPLC, browser), and
arbitrary roots are rejected with the stable
``ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP`` error before target creation;
protected targets remain pure comparison-only rejections; and every test
database is removed during cleanup.

The SAFETY-002 regression closes the invalid-UTF-8 marker gap: a marker that
cannot be decoded as UTF-8 is an unreadable marker, so the candidate
controlled root is rejected with the same stable error (no
``UnicodeDecodeError`` leak) and no target is created.

Scope discipline
----------------
- Every bootstrap target in this suite is constructed under the test's own
  ``tempfile.TemporaryDirectory()`` and removed with it. No tracked database,
  backup, snapshot, source artifact, or ``<local-scratch>/`` file is ever opened, read,
  hashed, copied, or passed to the runner.
- Marker files exist only inside the test's own temporary directories.
- Protected known database paths are exercised only through path comparison
  functions (``is_protected_target`` / ``validate_target``), which never touch
  the filesystem objects they reject.
- This is a narrow L2 ``integration``-marked proof of a fresh empty bootstrap
  and its root-admission policy only. It claims nothing about importer
  correctness, source fidelity, QSR, bridge identity, candidate qualification,
  production safety, or delivery.

Run with:
    python -m unittest tests.integration.test_isolated_schema_bootstrap
    python -m pytest -q tests/integration/test_isolated_schema_bootstrap.py
    python -m pytest -q -m integration tests/integration/test_isolated_schema_bootstrap.py
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError:
    pytest = None  # markers only; these tests are plain unittest

import tools.bootstrap_isolated_schema as bootstrap_mod

pytestmark = None if pytest is None else pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

_V217_TRIGGERS = (
    "trg_quality_report_insert_guard",
    "trg_quality_report_single_per_document",
    "trg_delivery_requires_complete_usage",
    "trg_document_pass_sets_request_validating_precondition",
)


class IsolatedBootstrapTests(unittest.TestCase):
    """Every test uses only its own TemporaryDirectory database."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="isolated-bootstrap-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def target(self, name: str = "fresh.db") -> Path:
        """A non-existing target under this test's own temp root."""
        target = self.root / name
        self.assertFalse(target.exists(), "bootstrap must start from a non-existing target")
        # Structural guarantee: the target is always inside the test temp root,
        # so a tracked database path can never be handed to the runner.
        common = os.path.commonpath(
            [os.path.normcase(self.tmp.name), os.path.normcase(str(target))]
        )
        self.assertEqual(common, os.path.normcase(self.tmp.name))
        return target

    def bootstrap(self) -> Path:
        """Bootstrap a fresh chain in this test's temp root."""
        target = self.target()
        result = bootstrap_mod.bootstrap(self.root, target)
        self.assertTrue(target.exists(), "bootstrap must create the target")
        self.assertEqual(result.ledger_count, 40)
        self.assertTrue(result.foreign_key_check_clean)
        return target

    # -- safety -------------------------------------------------------------

    def test_rejects_existing_target(self) -> None:
        target = self.target()
        target.write_bytes(b"pre-existing")
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.validate_target(self.root, target)
        self.assertIn("TARGET_ALREADY_EXISTS", str(ctx.exception))

    def test_rejects_outside_root_target(self) -> None:
        outside = Path(tempfile.gettempdir()) / "isolated-bootstrap-outside-proof.db"
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.validate_target(self.root, outside)
        self.assertIn("TARGET_OUTSIDE_ROOT", str(ctx.exception))
        self.assertFalse(outside.exists(), "validation must not create anything")

    def test_protected_paths_rejected_by_comparison_only(self) -> None:
        # Real protected database paths are flagged by pure path comparison;
        # neither function opens, stats, or hashes them.
        self.assertTrue(bootstrap_mod.is_protected_target(REPO_ROOT / "question_bank.db"))
        self.assertTrue(
            bootstrap_mod.is_protected_target(REPO_ROOT / "data" / "dev" / "teaching_docs_dev.db")
        )
        # A file that does NOT exist under the protected backups directory is
        # still rejected: the protection decision is comparison-only and
        # existence is irrelevant to it.
        synthetic = REPO_ROOT / "data" / "dev" / "backups" / "comparison-only-proof-nonexistent.db"
        self.assertFalse(synthetic.exists())
        # Protected targets are rejected through the real validate_target path
        # with an approved (temporary) root: root admission passes, then the
        # comparison-only protection fires before any outside-root/existence
        # consideration. Nothing is created or opened.
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.validate_target(self.root, synthetic)
        self.assertIn("TARGET_IS_PROTECTED_DATABASE", str(ctx.exception))
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.validate_target(self.root, REPO_ROOT / "question_bank.db")
        self.assertIn("TARGET_IS_PROTECTED_DATABASE", str(ctx.exception))
        # A non-protected sibling path is not flagged.
        self.assertFalse(bootstrap_mod.is_protected_target(REPO_ROOT / "unrelated-file.db"))

    def test_bootstrap_targets_never_are_tracked_database_paths(self) -> None:
        # All bootstrap calls in this suite go through self.target(), which
        # builds the path under the test's own TemporaryDirectory and asserts
        # containment. The protected-path set is additionally disjoint from
        # any test temp root.
        for protected in bootstrap_mod.protected_paths():
            self.assertNotIn(
                os.path.normcase(str(protected)),
                os.path.normcase(str(self.root)),
            )

    # -- approved-root policy (ISOLATED-BOOTSTRAP-SAFETY-001) ---------------

    def test_rejects_repository_root_and_subdirectories_before_target_creation(self) -> None:
        # The repository root is rejected by validation only; nothing is
        # created, opened, or stat'ed beyond the pure path comparison.
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.approve_root(REPO_ROOT)
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        # Repository subdirectories, including the forbidden directory
        # families (paths need not exist), are rejected the same way.
        for sub in (
            "<local-scratch>", "data", "source", "output", "backup", "backups",
            "collection", "kplc", "KPLC", "browser",
        ):
            with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
                bootstrap_mod.approve_root(REPO_ROOT / sub)
            self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        # validate_target refuses the repository root before any target
        # creation; the target is never created.
        never = REPO_ROOT / "isolated-bootstrap-never-created.db"
        self.assertFalse(never.exists())
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.validate_target(REPO_ROOT, never)
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        self.assertFalse(never.exists(), "root rejection must not create the target")

    def test_rejects_ordinary_root_without_configured_controlled_parent(self) -> None:
        # A root that is neither temporary nor under a configured controlled
        # parent is rejected; the path need not exist (pure comparison), so
        # nothing is created anywhere.
        ordinary = REPO_ROOT.parent / "isolated-bootstrap-ordinary-root-proof"
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.approve_root(ordinary)
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.validate_target(ordinary, ordinary / "fresh.db")
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        self.assertFalse((ordinary / "fresh.db").exists())

    def test_controlled_parent_without_marker_rejected(self) -> None:
        # Configured controlled parent without the marker file: rejected even
        # though the root lies under the system temporary directory.
        parent = Path(self.tmp.name)
        root = parent / "controlled-root"
        root.mkdir()
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.approve_root(root, controlled_root_parent=parent)
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.validate_target(
                root, root / "fresh.db", controlled_root_parent=parent
            )
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        self.assertFalse((root / "fresh.db").exists())

    def test_controlled_root_accepted_with_exact_marker_and_bootstraps(self) -> None:
        # Correctly marked controlled root under an explicitly configured
        # parent: admitted and a full fresh chain bootstraps inside it.
        parent = Path(self.tmp.name)
        root = parent / "controlled-root"
        root.mkdir()
        (root / bootstrap_mod.CONTROLLED_ROOT_MARKER_FILE).write_text(
            bootstrap_mod.CONTROLLED_ROOT_MARKER_VALUE + "\n", encoding="utf-8"
        )
        target = root / "controlled-fresh.db"
        result = bootstrap_mod.bootstrap(
            root, target, controlled_root_parent=parent
        )
        self.assertEqual(result.ledger_count, 40)
        self.assertTrue(result.foreign_key_check_clean)
        self.assertTrue(target.exists())
        common = os.path.commonpath(
            [os.path.normcase(str(root)), os.path.normcase(str(target))]
        )
        self.assertEqual(common, os.path.normcase(str(root)))

    def test_controlled_root_wrong_marker_content_rejected(self) -> None:
        # Marker with the wrong contract value, and an empty marker file, are
        # both rejected.
        parent = Path(self.tmp.name)
        root = parent / "controlled-root"
        root.mkdir()
        marker = root / bootstrap_mod.CONTROLLED_ROOT_MARKER_FILE
        marker.write_text("not-the-contract-value", encoding="utf-8")
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.approve_root(root, controlled_root_parent=parent)
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        marker.write_text("", encoding="utf-8")
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.approve_root(root, controlled_root_parent=parent)
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        # A missing controlled root (no marker readable) is rejected too.
        missing = parent / "controlled-root-missing"
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.approve_root(missing, controlled_root_parent=parent)
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))

    def test_controlled_root_invalid_utf8_marker_rejected(self) -> None:
        # A marker containing invalid UTF-8 bytes is an unreadable marker:
        # the stable ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP rejection fires
        # (no UnicodeDecodeError leak) and no target is ever created.
        # Regression for ISOLATED-BOOTSTRAP-SAFETY-002; fixture is this
        # test's own temporary directory only.
        parent = Path(self.tmp.name)
        root = parent / "controlled-root"
        root.mkdir()
        marker = root / bootstrap_mod.CONTROLLED_ROOT_MARKER_FILE
        marker.write_bytes(b"\xff\xfe\x80invalid-utf8-bytes")
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.approve_root(root, controlled_root_parent=parent)
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        target = root / "fresh.db"
        with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
            bootstrap_mod.validate_target(
                root, target, controlled_root_parent=parent
            )
        self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))
        self.assertFalse(
            target.exists(),
            "invalid-marker rejection must not create the target",
        )

    def test_controlled_parent_must_not_be_forbidden_directory(self) -> None:
        # The explicitly configured controlled parent must never be the
        # repository root or a project data/source/backup/output family
        # directory; the configuration itself is a policy violation.
        for forbidden in (
            REPO_ROOT,
            REPO_ROOT / "<local-scratch>",
            REPO_ROOT / "data",
            REPO_ROOT / "backups",
            REPO_ROOT / "output",
        ):
            with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
                bootstrap_mod.approve_root(
                    forbidden / "root", controlled_root_parent=forbidden
                )
            self.assertIn("ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP", str(ctx.exception))

    def test_controlled_root_parent_resolution_env_and_cli(self) -> None:
        # The controlled parent is explicit configuration only: CLI value wins
        # over the environment variable, and unset/whitespace means "not
        # configured" (only temporary roots usable).
        saved = os.environ.get(bootstrap_mod.CONTROLLED_ROOT_PARENT_ENV)
        try:
            os.environ.pop(bootstrap_mod.CONTROLLED_ROOT_PARENT_ENV, None)
            self.assertIsNone(bootstrap_mod.resolve_controlled_root_parent(None))
            os.environ[bootstrap_mod.CONTROLLED_ROOT_PARENT_ENV] = str(self.root)
            self.assertEqual(
                bootstrap_mod.resolve_controlled_root_parent(None), self.root
            )
            cli = self.root / "cli-parent"
            self.assertEqual(
                bootstrap_mod.resolve_controlled_root_parent(cli), cli
            )
            os.environ[bootstrap_mod.CONTROLLED_ROOT_PARENT_ENV] = "   "
            self.assertIsNone(bootstrap_mod.resolve_controlled_root_parent(None))
        finally:
            if saved is None:
                os.environ.pop(bootstrap_mod.CONTROLLED_ROOT_PARENT_ENV, None)
            else:
                os.environ[bootstrap_mod.CONTROLLED_ROOT_PARENT_ENV] = saved

    def test_controlled_root_target_cleaned_after(self) -> None:
        # The controlled-root database is removed with its temporary fixture.
        with tempfile.TemporaryDirectory(prefix="isolated-bootstrap-test-") as td:
            parent = Path(td)
            root = parent / "controlled-root"
            root.mkdir()
            (root / bootstrap_mod.CONTROLLED_ROOT_MARKER_FILE).write_text(
                bootstrap_mod.CONTROLLED_ROOT_MARKER_VALUE, encoding="utf-8"
            )
            target = root / "controlled-fresh.db"
            bootstrap_mod.bootstrap(root, target, controlled_root_parent=parent)
            self.assertTrue(target.exists())
        self.assertFalse(target.exists())
        self.assertFalse(root.exists())

    # -- fresh chain --------------------------------------------------------

    def test_fresh_chain_reaches_exactly_40_ledger_versions_in_order(self) -> None:
        target = self.bootstrap()
        conn = sqlite3.connect(target)
        try:
            rows = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY rowid"
            ).fetchall()
            ledger = [row[0] for row in rows]
        finally:
            conn.close()
        self.assertEqual(len(ledger), 40)
        self.assertEqual(ledger, list(bootstrap_mod.EXPECTED_VERSIONS))
        self.assertEqual(ledger[0], "v2-initial-2026-07-25")
        self.assertEqual(ledger[-1], bootstrap_mod.V2_39)

    def test_foreign_key_integrity_clean(self) -> None:
        target = self.bootstrap()
        conn = sqlite3.connect(target)
        try:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    def test_target_absent_before_and_cleaned_after(self) -> None:
        with tempfile.TemporaryDirectory(prefix="isolated-bootstrap-test-") as td:
            root = Path(td)
            target = root / "fresh.db"
            self.assertFalse(target.exists())
            bootstrap_mod.bootstrap(root, target)
            self.assertTrue(target.exists())
        # The TemporaryDirectory context exit removed the database again.
        self.assertFalse(target.exists())

    # -- v2.17 quality_reports rebuild --------------------------------------

    def test_v217_quality_reports_rebuild_conditions(self) -> None:
        target = self.bootstrap()
        conn = sqlite3.connect(target)
        try:
            qr_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='quality_reports'"
            ).fetchone()[0]
            self.assertIn("unsupported", qr_sql)
            self.assertIn("missing", qr_sql)
            trigger_names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            for name in _V217_TRIGGERS:
                self.assertIn(name, trigger_names)
            # The insert guard recreated by the v2.17 rebuild is live: a
            # quality report without a pending-review document is rejected at
            # runtime with the v2.17 guard message.
            with self.assertRaises(sqlite3.IntegrityError) as ctx:
                conn.execute(
                    "INSERT INTO quality_reports(id, document_id, data_gate, rule_gate, "
                    "fidelity_gate, artifact_gate, findings_json, blockers_json, status) "
                    "VALUES('qr1','missing-doc','pass','pass','pass','pass','[]','[]','pass')"
                )
            self.assertIn(
                "quality report requires pending review document", str(ctx.exception)
            )
            conn.rollback()
        finally:
            conn.close()

    # -- v2.24/v2.25 view-and-trigger replacement ---------------------------

    def test_v224_v225_view_and_trigger_replacement_in_file_order(self) -> None:
        target = self.bootstrap()
        conn = sqlite3.connect(target)
        try:
            view_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='view' "
                "AND name='current_question_auto_mapping_audits'"
            ).fetchone()[0]
            # The v2.25 hardened view replaced the v2.24 view: JSON-validity
            # and the exact four-validator bundle keys are present.
            self.assertIn("json_valid(a.validator_results_json)", view_sql)
            for key in (
                "source_fidelity",
                "structural_consistency",
                "mathematical_independent",
                "asset_semantics",
            ):
                self.assertIn(key, view_sql)
            trigger_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_question_auto_mapping_audit_logs_insert_guards'"
            ).fetchone()[0]
            self.assertIn("exactly four validator bundles", trigger_sql)
            # File order is evidenced by the ledger order.
            ledger = [
                row[0]
                for row in conn.execute(
                    "SELECT version FROM schema_migrations ORDER BY rowid"
                )
            ]
            self.assertLess(ledger.index(bootstrap_mod.V2_24), ledger.index(bootstrap_mod.V2_25))
        finally:
            conn.close()

    # -- v2.29 wrapper-inserted ledger row -----------------------------------

    def test_v229_wrapper_ledger_row_and_append_only_machinery(self) -> None:
        target = self.bootstrap()
        # Static fact: the v2.29 SQL file does not insert its own ledger row;
        # the wrapper path (reproduced by the runner) must insert it.
        v29_sql = (bootstrap_mod.SCHEMA_DIR / "schema_v2_29.sql").read_text(encoding="utf-8")
        self.assertNotIn("schema_migrations", v29_sql)
        conn = sqlite3.connect(target)
        try:
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (bootstrap_mod.V2_29,)
            ).fetchone()
            self.assertIsNotNone(row, "v2.29 ledger row must exist (wrapper-inserted)")
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for name in (
                "controlled_content_source_revisions",
                "controlled_content_source_revision_heads",
                "controlled_content_source_revision_head_events",
            ):
                self.assertIn(name, tables)
            views = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='view'"
                )
            }
            self.assertIn("current_controlled_content_source_revisions", views)
            self.assertIn("current_controlled_content_segments", views)
            triggers = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            self.assertIn("trg_v29_content_revision_head_insert_guard", triggers)
            self.assertIn("trg_v29_content_revision_head_event_apply", triggers)
        finally:
            conn.close()

    def test_v229_refuses_non_empty_seed_precondition(self) -> None:
        # The runner must fail closed if the v2.29 seed query ever finds rows
        # (legacy-current-revision seeding belongs to the wrapper, never to a
        # fresh bootstrap). Fixture uses only this test's own temp database.
        with tempfile.TemporaryDirectory(prefix="isolated-bootstrap-test-") as td:
            conn = sqlite3.connect(Path(td) / "pre29.db")
            try:
                h64 = "a" * 64
                conn.executescript(
                    f"""
                    CREATE TABLE controlled_content_import_runs (
                        id TEXT PRIMARY KEY,
                        manifest_sha256 TEXT NOT NULL UNIQUE,
                        p1_2b_audit_sha256 TEXT NOT NULL,
                        importer_id TEXT NOT NULL,
                        importer_version TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('validated')),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE controlled_content_sources (
                        id TEXT PRIMARY KEY,
                        import_run_id TEXT NOT NULL REFERENCES controlled_content_import_runs(id),
                        original_path TEXT NOT NULL,
                        original_sha256 TEXT NOT NULL CHECK (length(original_sha256)=64),
                        archive_path TEXT NOT NULL,
                        archive_sha256 TEXT NOT NULL CHECK (length(archive_sha256)=64),
                        converted_path TEXT NOT NULL,
                        converted_sha256 TEXT NOT NULL CHECK (length(converted_sha256)=64),
                        converter_fingerprint TEXT NOT NULL CHECK (length(converter_fingerprint)=64),
                        fidelity_status TEXT NOT NULL CHECK (fidelity_status='passed'),
                        source_profile_sha256 TEXT NOT NULL CHECK (length(source_profile_sha256)=64),
                        UNIQUE(original_sha256, converter_fingerprint)
                    );
                    CREATE TABLE controlled_content_segments (
                        id TEXT PRIMARY KEY,
                        source_id TEXT NOT NULL REFERENCES controlled_content_sources(id),
                        segment_id TEXT NOT NULL,
                        content_type TEXT NOT NULL CHECK (content_type IN ('knowledge_explanation','consolidation_practice')),
                        layer TEXT NOT NULL CHECK (layer IN ('public_core','basic_reinforcement','standard_extension','challenge_extension')),
                        textbook_id TEXT NOT NULL,
                        curriculum_node_id TEXT NOT NULL,
                        allowed_node_ids_json TEXT NOT NULL CHECK (json_valid(allowed_node_ids_json)),
                        required_knowledge_json TEXT NOT NULL CHECK (json_valid(required_knowledge_json)),
                        source_character_range_json TEXT NOT NULL CHECK (json_valid(source_character_range_json)),
                        normalized_text_sha256 TEXT NOT NULL CHECK (length(normalized_text_sha256)=64),
                        scope_status TEXT NOT NULL CHECK (scope_status='candidate_only'),
                        invalidated_at TEXT,
                        invalidation_reason TEXT,
                        UNIQUE(source_id, segment_id)
                    );
                    INSERT INTO controlled_content_import_runs(id, manifest_sha256, p1_2b_audit_sha256, importer_id, importer_version, status)
                    VALUES('run1','{h64}','{h64}','imp','1','validated');
                    INSERT INTO controlled_content_sources(id, import_run_id, original_path, original_sha256, archive_path, archive_sha256, converted_path, converted_sha256, converter_fingerprint, fidelity_status, source_profile_sha256)
                    VALUES('src1','run1','p','{h64}','p','{h64}','p','{h64}','{h64}','passed','{h64}');
                    INSERT INTO controlled_content_segments(id, source_id, segment_id, content_type, layer, textbook_id, curriculum_node_id, allowed_node_ids_json, required_knowledge_json, source_character_range_json, normalized_text_sha256, scope_status)
                    VALUES('seg1','src1','seg','knowledge_explanation','public_core','tb1','cn1','[]','[]','[0,1]','{h64}','candidate_only');
                    """
                )
                self.assertEqual(len(bootstrap_mod.v29_seed_rows(conn)), 1)
                with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
                    bootstrap_mod._check_v229_seed_precondition(conn)
                self.assertIn("V229_NON_EMPTY_PRECONDITION", str(ctx.exception))
            finally:
                conn.close()

    # -- v2.37 questions rebuild ---------------------------------------------

    def test_v237_questions_rebuild_preserves_objects_and_nullability(self) -> None:
        target = self.bootstrap()
        conn = sqlite3.connect(target)
        try:
            cols = {
                row[1]: row
                for row in conn.execute("PRAGMA table_info(questions)")
            }
            self.assertEqual(cols["stage"][3], 0, "stage must be nullable after v2.37")
            self.assertEqual(cols["difficulty"][3], 0, "difficulty must be nullable after v2.37")
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            self.assertIn("idx_questions_quality", indexes)
            triggers = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            # A v2.15/v2.17 delivery trigger must survive the v2.37 dance.
            self.assertIn("trg_delivery_requires_complete_usage", triggers)
            views = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='view'"
                )
            }
            # A v2.25 view must survive the v2.37 dance.
            self.assertIn("current_question_auto_mapping_audits", views)
            count = conn.execute(
                "SELECT COUNT(*) FROM content_change_ledger "
                "WHERE event_type='schema_migration' "
                "AND entity_id='v2.37-question-stage-optional-for-content-library'"
            ).fetchone()[0]
            self.assertEqual(count, 1, "v2.37 must record its schema migration in the change ledger")
        finally:
            conn.close()

    # -- v2.39 repair and empty-table condition ------------------------------

    def test_v239_rebuild_and_empty_table_condition_on_fresh_chain(self) -> None:
        target = self.bootstrap()
        conn = sqlite3.connect(target)
        try:
            d_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='content_item_difficulty_evidence'"
            ).fetchone()[0]
            for value in ("基础", "中等", "中等偏难", "高", "拓展"):
                self.assertIn(value, d_sql)
            self.assertNotIn("??", d_sql)
            # The empty-table condition held on the fresh chain: the table is
            # empty and the rebuild (which refuses any row) completed.
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM content_item_difficulty_evidence").fetchone()[0],
                0,
            )
            views = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='view'"
                )
            }
            # The v2.38 eligibility view referencing the rebuilt table survived.
            self.assertIn("content_item_question_eligibility", views)
            count = conn.execute(
                "SELECT COUNT(*) FROM content_change_ledger "
                "WHERE event_type='schema_migration' "
                "AND entity_id='v2.39-repair-content-item-difficulty-enum'"
            ).fetchone()[0]
            self.assertEqual(count, 1, "v2.39 must record its schema migration in the change ledger")
        finally:
            conn.close()

    def test_v239_refuses_non_empty_legacy_difficulty_evidence(self) -> None:
        # The runner must refuse v2.39 when the legacy table holds rows
        # (v2_39_refuses_ambiguous_legacy_difficulty_values). Fixture uses the
        # exact malformed v2.34 CHECK and only this test's own temp database.
        with tempfile.TemporaryDirectory(prefix="isolated-bootstrap-test-") as td:
            conn = sqlite3.connect(Path(td) / "pre39.db")
            try:
                h64 = "a" * 64
                conn.executescript(
                    f"""
                    CREATE TABLE content_items (id TEXT PRIMARY KEY);
                    CREATE TABLE content_change_ledger (id TEXT PRIMARY KEY);
                    CREATE TABLE content_item_difficulty_evidence (
                        id TEXT PRIMARY KEY,
                        content_item_id TEXT NOT NULL REFERENCES content_items(id),
                        difficulty TEXT NOT NULL CHECK(difficulty IN ('??','??','????','?','??')),
                        evidence_status TEXT NOT NULL CHECK(evidence_status IN ('candidate','validated','blocked','rejected','superseded')),
                        method TEXT NOT NULL,
                        evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
                        evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
                        created_change_event_id TEXT NOT NULL REFERENCES content_change_ledger(id),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(content_item_id, difficulty, evidence_sha256)
                    );
                    INSERT INTO content_items(id) VALUES('ci1');
                    INSERT INTO content_change_ledger(id) VALUES('ev1');
                    INSERT INTO content_item_difficulty_evidence(id, content_item_id, difficulty, evidence_status, method, evidence_json, evidence_sha256, created_change_event_id)
                    VALUES('r1','ci1','?','candidate','m','{{}}','{h64}','ev1');
                    """
                )
                with self.assertRaises(bootstrap_mod.BootstrapError) as ctx:
                    bootstrap_mod._check_v239_empty_precondition(conn)
                self.assertIn("V239_REFUSES_AMBIGUOUS_LEGACY_DIFFICULTY_VALUES", str(ctx.exception))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
