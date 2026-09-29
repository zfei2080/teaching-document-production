# Schema and Migration Static Inventory

- **Work order:** `SCHEMA-AUTHORITY-001`
- **Date of observation:** 2026-08-14
- **Method:** read-only static inspection of the approved file set. No database was opened, created, migrated, hashed, queried, attached, copied, or inspected. No importer, migration, test, or application script was executed. All statements below are static observations of file content, Git tracking state, and declared contracts, not runtime results.

## 1. Purpose and Boundary

This inventory records every schema/migration candidate reachable from the approved static inspection set of `SCHEMA-AUTHORITY-001`, with its Git-tracked status, declared version or order signal, baseline-versus-incremental role, statically visible transaction/idempotence behavior, ledger usage, relevant tables, invoking scripts, and known incompatibilities or uncertainties. It is the evidence base for the authority decision in `docs/architecture/SCHEMA_AUTHORITY.md` and `docs/adr/ADR-0002-isolated-schema-authority.md`.

SQL object names and structural metadata are recorded. No source-question content and no database rows are recorded.

## 2. Approved Static Inspection Set Used

- `schema.sql`, `schema_v2.sql`, `schema_v2_1.sql` … `schema_v2_39.sql`, `schema_p1_2c_temporary.sql`
- `apply_schema_v2_5.py` … `apply_schema_v2_39.py` (35 files)
- `database.py`, `source_library_intake.py`, `content_library_extraction.py`, `content_question_candidate_import.py`, `docx_asset_materializer.py`
- `collection/import_bridge.py` from the `extsrc-browser-collection-004` worktree (read only for its declared schema/bootstrap expectations)
- Tests `test_schema_v2.py`, `test_schema_v2_17.py`, `test_source_library_intake.py`, `test_content_question_candidate_import.py` (only to verify statically declared bootstrap contracts)
- `docs/architecture/SUPPLY_DELIVERY_CONTRACT.md`, `docs/governance/ENGINEERING_STANDARD.md`, `docs/governance/LEGACY_MODULE_MANIFEST.json`, `docs/adr/ADR-0001-project-control-plane.md`

`schema_manager.py` was listed in the work order's approved set but does not exist: it is absent from the root worktree, absent from all registered worktrees, and untracked anywhere (verified by file search and `git ls-files`). This is recorded as a static fact.

## 3. Discovery Commands Actually Run

`rg` was not available in this environment; the equivalent Git-native commands were used and are recorded here as the commands actually executed:

| Command | Purpose | Result |
| --- | --- | --- |
| `git status -sb` | Scoped initial state | Branch `main` in sync with `origin`; pre-existing approval edits in `PROJECT_CONTROL.md`/`task_plan.md`; untracked `<local-scratch>/` and untracked `docs/work_orders/SCHEMA-AUTHORITY-001.md`. |
| `git diff --check` | Whitespace check | Exit 0. |
| `git ls-files` filtered to `^schema.*\.sql$` and `^apply_schema_v2.*\.py$` at root (substitute for `rg --files -g "schema*.sql" -g "apply_schema_v2*.py"`) | File inventory | 77 tracked root files: 42 SQL files (`schema.sql`, `schema_v2.sql`, `schema_v2_1.sql`…`schema_v2_39.sql`, `schema_p1_2c_temporary.sql`) and 35 apply wrappers (`apply_schema_v2_5.py`…`apply_schema_v2_39.py`). No `apply_schema_v2_1.py`…`apply_schema_v2_4.py` exists. |
| `git grep -n -E "schema_migrations|BEGIN|COMMIT|CREATE TABLE|source_versions|questions|question_usage" -- <approved files>` (substitute for the work order's `rg -n` line) | Structural markers | 658 matching lines across the approved set; per-file counts recorded below where relevant. |
| Line-anchored scan for transaction statements (`^BEGIN( IMMEDIATE| TRANSACTION)?;$`, `^COMMIT;$`) over all `schema_v2_*.sql` | Transaction-wrapper detection | Only the files listed in section 5 declare semicolon-terminated transaction statements; all other `BEGIN` hits are trigger-body `BEGIN`. |
| `python -m compileall -q` | Not run | No documentation helper was created, so the work order's conditional compile check has no subject. |

## 4. Baseline Candidates

### 4.1 `schema.sql` — legacy family baseline (with `database.py`)

- **Tracked:** yes (root, 4,188 bytes).
- **Declared role:** standalone create-script for the legacy question bank. Header: "用法: `sqlite3 questions.db < schema.sql`" (usage note, not an executed contract).
- **Version marker:** none. No `schema_migrations` table, no version string.
- **Transaction/idempotence:** none declared in the file. `database.py` (`QuestionBankDB.init_db`) executes it with `conn.executescript(sql)` inside `init_db`, after ad-hoc column migrations `_migrate_add_stage_column` / `_migrate_add_options_column`; an inline fallback `_create_tables_inline` exists for when the file is unavailable. `database.py` default DB path is `question_bank.db` (`DEFAULT_DB_PATH`).
- **Tables:** `questions` (legacy shape: `subject`, `answer`, `knowledge_point`, `sub_knowledge`, `difficulty`, `error_prone`, `grade_level`, `stage`, `question_type`, `options`, `has_image`, `source_file`, `source_page`, `created_at`, `used_count`), `images`, `knowledge_tags`, `lecture_sessions`, `session_questions`; six indexes.
- **Invoked by:** `database.py` only (within the approved set).
- **Relevance to the isolated candidate-intake path:** none of the v2 family, the importer modules, or `collection/import_bridge.py` references this family's tables. Classified legacy/reference in section 9.

### 4.2 `schema_v2.sql` — unified development baseline

- **Tracked:** yes (root, 11,628 bytes).
- **Declared role:** baseline for a new unified development database. File comment: "This schema creates a new development database only. It does not modify legacy databases."
- **Version marker:** creates `schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)` and inserts ledger version `v2-initial-2026-07-25` (`INSERT OR IGNORE`).
- **Transaction/idempotence:** no transaction wrapper in the file; idempotent `CREATE TABLE IF NOT EXISTS` style; ledger insert is `INSERT OR IGNORE`.
- **Tables (24 + indexes):** `textbooks`, `curriculum_nodes`, `knowledge_points`, `curriculum_knowledge_points`, `source_documents` (with `file_hash`, `file_type`, `copyright_status`, `parse_status`), `source_fragments` (with `raw_hash`, locators, `question_number`), `questions` (v2 shape: `stem`, `options_json`, `answer`, `analysis`, `question_type`, `difficulty`, `stage`, `grade_level`, `source_document_id`, `source_fragment_id`, `source_question_no`, `source_page`, `content_hash UNIQUE`, `extraction_status`, `quality_status`, `review_status`), `question_textbooks`, `question_knowledge_points`, `question_assets`, `classes`, `rule_sets`, `production_requests`, `selection_plans`, `selection_plan_questions`, `teaching_documents`, `document_questions`, `question_usage`, `quality_reports`.
- **Invoked by:** no tracked application or wrapper script in the approved set. Statically declared bootstrap consumers are the approved tests (section 7), which execute the file text directly against fresh temporary databases.
- **Relevance:** the sole coherent baseline for the numbered migration chain.

### 4.3 `schema_p1_2c_temporary.sql` — temporary standalone schema

- **Tracked:** yes (root, 2,087 bytes).
- **Declared role:** temporary controlled-content schema for the P1-2c pipeline: `controlled_content_import_runs`, `controlled_content_sources`, `controlled_content_segments` (with `scope_status='candidate_only'` constraint), no ledger, no version, no transaction wrapper.
- **Known incompatibility:** its table shapes are superseded inside the v2 chain by `schema_v2_26.sql` and rebuilt by `schema_v2_27.sql` / `schema_v2_29.sql`. The file is unversioned and is not referenced by any apply wrapper. Classified legacy/temporary reference in section 9.

## 5. Incremental Migrations `schema_v2_1.sql` … `schema_v2_39.sql`

All 39 files are tracked at root. All are incremental migrations of the `schema_v2.sql` baseline; none creates a database. Every file declares its ledger version in `schema_migrations`. Style facts:

- **Ledger insert inside the SQL file:** v2.1–v2.28 and v2.30–v2.39 (v2.1–v2.5, v2.8 use `INSERT OR IGNORE`; v2.20–v2.28 and v2.30–v2.39 use plain `INSERT INTO schema_migrations(version, applied_at) VALUES(..., datetime('now')|CURRENT_TIMESTAMP)`).
- **Ledger insert by the wrapper, not the SQL file:** v2.29 (`apply_schema_v2_29.py` inserts the row after seeding, inside an explicit transaction with rollback on failure).
- **Declared transaction wrapper (semicolon-terminated `BEGIN`/`COMMIT` in the SQL file):** v2.29 (`BEGIN IMMEDIATE;` + `PRAGMA foreign_keys=OFF` … `=ON`, no in-file `COMMIT` — the wrapper commits), v2.31, v2.32, v2.33 (`BEGIN IMMEDIATE;` … `COMMIT;`), v2.34–v2.39 (`BEGIN;` … `COMMIT;`). v2.27 toggles `PRAGMA foreign_keys=OFF`…`=ON` around a table rebuild without an in-file transaction statement. All other `BEGIN` occurrences in the files are trigger-body `BEGIN`.
- **Idempotence model:** SQL-level `CREATE TABLE IF NOT EXISTS` / `CREATE TRIGGER IF NOT EXISTS` / `DROP TRIGGER IF EXISTS` where applicable, plus `INSERT OR IGNORE` ledger rows in v2.1–v2.8; wrapper-level "check ledger then apply" (`MIGRATION_ALREADY_APPLIED` early return) in `apply_schema_v2_16`…`apply_schema_v2_39`; dev-bound wrappers v2.5–v2.15 rely on the SQL-level guards. `ALTER TABLE` statements (v2.3, v2.20, v2.21, v2.28, v2.35) are not re-runnable at SQL level; wrapper guards are the only protection.

Per-migration static facts (file, declared ledger version, transaction wrapper, key objects, structural notes):

| File | Declared version | Txn | Key static objects / notes |
| --- | --- | --- | --- |
| `schema_v2_1.sql` | `v2.1-field-provenance-2026-07-25` | no | `question_source_fragments` (field-level source provenance: `field_name IN ('stem','options','answer','knowledge_point','analysis','asset')`, `source_hash`); index. |
| `schema_v2_2.sql` | `v2.2-question-review-audit-2026-07-25` | no | `question_reviews` (immutable review decisions; `review_type` incl. `copyright_source`; `decision IN ('approved','rejected','needs_revision')`). |
| `schema_v2_3.sql` | `v2.3-teacher-final-review-2026-07-25` | no | `ALTER TABLE question_reviews ADD COLUMN reviewer_role ... DEFAULT 'assistant_precheck'`. Not re-runnable at SQL level. |
| `schema_v2_4.sql` | `v2.4-automatic-verification-2026-07-25` | no | `question_verifications` (`verification_type` incl. `source_fidelity`, `mathematical_independent`; `status IN ('pass','fail','unsupported')`; `input_hash`, `evidence_json`). |
| `schema_v2_5.sql` | `v2.5-controlled-catalog-releases-2026-07-25` | no | `catalog_releases` (versioned curriculum scope; `status IN ('draft','approved','archived')`). |
| `schema_v2_6.sql` | `v2.6-controlled-import-provenance-2026-07-26` | no | `controlled_import_runs` (`import_kind IN ('catalog','question_mapping')`; source/manifest/importer identity), `catalog_release_imports`, `question_textbook_imports` (FK to `question_textbooks`). |
| `schema_v2_7.sql` | `v2.7-provenance-link-integrity-2026-07-26` | no | Four integrity triggers binding import kind and source identity (`catalog_release_imports`, `question_textbook_imports`). |
| `schema_v2_8.sql` | `v2.8-catalog-audit-attestation-2026-07-26` | no | `catalog_audits`; trigger requiring approved source-bound audit before `catalog_releases.status='approved'`. |
| `schema_v2_9.sql` | `v2.9-question-knowledge-point-import-provenance-2026-07-26` | no | `question_knowledge_point_imports`; kind-guard triggers. |
| `schema_v2_10.sql` | `v2.10-question-mapping-audit-attestation-2026-07-26` | no | `question_mapping_audits`; trigger binding `question_textbooks.fit_status='approved'` to approved source-bound mapping audit. |
| `schema_v2_11.sql` | `v2.11-controlled-knowledge-mapping-audit-2026-07-26` | no | `controlled_knowledge_import_runs`, `knowledge_point_imports`, `curriculum_knowledge_point_imports`, `knowledge_mapping_audits`; approval-guard trigger. |
| `schema_v2_12.sql` | `v2.12-controlled-asset-adjacency-audit-2026-07-26` | no | `asset_adjacency_imports`, `asset_adjacency_instances`, `asset_adjacency_audits`, `question_asset_adjacency_links`; exact-stem and verified-asset triggers. |
| `schema_v2_13.sql` | `v2.13-question-approval-state-machine-2026-07-26` | no | Five approval/verification guards on `questions`; "latest pass" admission predicate; approved-question input/evidence locks. |
| `schema_v2_14.sql` | `v2.14-current-input-snapshot-evidence-2026-07-26` | no | `question_input_snapshots` (fail-closed invalidation); drops v2.13 admission triggers and replaces them with current-snapshot predicates; extensive invalidation triggers. |
| `schema_v2_15.sql` | `v2.15-controlled-delivery-state-machine-2026-07-26` | no | Delivery state-machine triggers over `production_requests`, `selection_plans`/`selection_plan_questions`, `teaching_documents`, `document_questions`, `quality_reports`, `question_usage` (immutable usage rows; delivery requires complete usage). |
| `schema_v2_16.sql` | `v2.16-class-progress-reuse-authorization-2026-07-26` | no | `class_progress_controls`, `class_progress_allowed_nodes`, `question_reuse_authorizations`; progress/reuse guards; progress change invalidates prior state. |
| `schema_v2_17.sql` | `v2.17-quality-gate-unsupported-missing-2026-07-26` | no | Rebuilds `quality_reports` (copy-to-new-table, `DROP TABLE`, `RENAME`) to add `'unsupported'`/`'missing'` gate values; drops and recreates four v2.15 triggers. Requires v2.15 trigger set to exist (drop order). |
| `schema_v2_18.sql` | `v2.18-textbook-body-snapshot-chain-2026-07-26` | no | `textbook_sources`, `textbook_body_units`, `question_body_unit_mappings`, `knowledge_body_unit_mappings`, `textbook_body_unit_snapshots`; snapshot/invalidation triggers; approval-block trigger for textbook-mapped questions. |
| `schema_v2_19.sql` | `v2.19-delivery-quality-gate-binding-2026-07-26` | no | `artifact_validation_evidence`; replaces `trg_delivery_requires_complete_usage` binding delivery to current content/question-set/snapshot hashes and artifact role. |
| `schema_v2_20.sql` | `v2.20` | no | `classifier_runs`; `ALTER TABLE` adds `classifier_run_id`/`confidence`/`classification_method` to `question_textbooks` and `question_knowledge_points`; closes INSERT-path bypass of v2.10/v2.11 approval guards. Plain ledger insert. |
| `schema_v2_21.sql` | `v2.21-controlled-source-intake` | no | `ALTER TABLE source_documents` adds `original_relative_path`, `original_file_hash`, `trusted_source`, `intake_manifest_json`; trusted-source hash-binding triggers. |
| `schema_v2_22.sql` | `v2.22-draft-question-curriculum-mapping-evidence` | no | `question_curriculum_mapping_evidence` (draft-only; statuses `candidate`/`unsupported`/`rejected`); `current_question_curriculum_mapping_evidence` view; immutable-binding and invalidation triggers. |
| `schema_v2_23.sql` | `v2.23-question-auto-mapping-audits` | no | `question_auto_mapping_audits`; `current_question_auto_mapping_audits` view; insert guards and append-only triggers. |
| `schema_v2_24.sql` | `v2.24-question-auto-mapping-audit-logs` | no | `question_auto_mapping_audit_logs`; **replaces** the v2.23 view `current_question_auto_mapping_audits` (CREATE VIEW on new table) and drops/recreates the insert-guard trigger. |
| `schema_v2_25.sql` | `v2.25-question-auto-mapping-audit-bundle-hardening` | no | Drops v2.24 view and trigger; recreates both with four-validator-bundle hardening (exact keys `source_fidelity`, `structural_consistency`, `mathematical_independent`, `asset_semantics`). |
| `schema_v2_26.sql` | `v2.26-controlled-content-evidence` | no | `controlled_content_import_runs`, `controlled_content_sources`, `controlled_content_segments` (same shapes as `schema_p1_2c_temporary.sql` plus `current_controlled_content_segments` view). |
| `schema_v2_27.sql` | `v2.27-controlled-content-segment-revisions` | no (FK OFF→ON) | Rebuilds `controlled_content_segments` (new unique key incl. textbook/node/allowed-nodes) via copy/drop/rename with `PRAGMA foreign_keys=OFF` … `=ON`; recreates view. |
| `schema_v2_28.sql` | `v2.28-p13c-approval-boundary-and-mapping-revisions` | no | `question_mapping_source_revisions`, `question_mapping_source_revision_heads`, `question_mapping_source_revision_knowledge_points`, `question_mapping_source_revision_head_events` (append-only head events), `question_approval_input_snapshots_v2`, `question_mapping_approval_evidence_v2`, `question_mapping_auto_audits_v2`, `question_mapping_approval_audits_v2`; current-* views; v2 approval chain triggers; replaces the v2.10 mapping-approval trigger with a revision-aware version; v2 approval-snapshot invalidation triggers; makes v2.26/27 `controlled_content_sources`/`controlled_content_segments` contract-immutable. |
| `schema_v2_29.sql` | `v2.29-p12f-append-only-controlled-content-source-revisions` (row inserted by wrapper) | `BEGIN IMMEDIATE;` (FK OFF→ON) | Rebuilds `controlled_content_sources` (drops uniqueness that prevented recovery) and `controlled_content_segments`; adds `controlled_content_source_revisions`, `controlled_content_source_revision_heads`, `controlled_content_source_revision_head_events`; current-* views; append-only head-event machinery; snapshot-invalidation trigger on head events. |
| `schema_v2_30.sql` | `v2.30-p14-current-p13c-question-admission-path` | no | Drops v2.18 approval-block trigger and recreates it to accept either active textbook-body evidence **or** current P1-3c mapping approval evidence (v2.28 chain). |
| `schema_v2_31.sql` | `v2.31-p14b-controlled-question-source-derivations` | `BEGIN IMMEDIATE;`…`COMMIT;` | `controlled_question_source_import_runs` (status `'validated'`), `controlled_question_answer_evidence` (fields `answer`/`analysis`, `internal_only=1`, no overlap with student-safe segments), `controlled_question_source_derivations` (binds `source_documents` docx to current converted hash, prompt inside a current student segment, requires both answer and analysis evidence); current-* views; append-only triggers. |
| `schema_v2_32.sql` | `v2.32-p14b-current-pedagogical-role-evidence` | `BEGIN IMMEDIATE;`…`COMMIT;` | `controlled_pedagogical_role_import_runs` (status `'validated_evidence'`), `question_pedagogical_role_evidence` (role anchored to an exact current source fragment and marker; requires current approved question snapshot and mapping); current view; append-only triggers. |
| `schema_v2_33.sql` | `v2.33-p14b-controlled-auxiliary-content-evidence` | `BEGIN IMMEDIATE;`…`COMMIT;` | `controlled_auxiliary_content_import_runs` (status `'validated_evidence'`), `controlled_auxiliary_content_evidence` (`worked_example`/`summary`/`self_assessment` with explicit Chinese marker constraints); current view; append-only triggers. |
| `schema_v2_34.sql` | `v2.34-database-enrichment-change-ledger` | `BEGIN;`…`COMMIT;` | Append-only enrichment family: `content_change_ledger`, `content_import_runs`, `content_import_run_events`, `content_source_versions`, `content_source_version_heads`, `content_source_lifecycle_events`, `content_extraction_runs`, `source_content_blocks`, `source_content_assets`, `content_items`, `content_item_evidence`, `content_item_question_links`, `question_internal_evidence`, `content_item_curriculum_mappings`, `content_item_knowledge_mappings`, `content_dependency_edges`, `content_item_difficulty_evidence`, `content_item_role_suitability`; append-only triggers; ledger row for the migration itself. Note: `content_item_difficulty_evidence.difficulty` CHECK contains a malformed character sequence (`'??','??','????','?','??'`), repaired by v2.39. |
| `schema_v2_35.sql` | `v2.35-database-enrichment-complete-source-inventory` | `BEGIN;`…`COMMIT;` | `content_source_intake_records`, `content_source_intake_lifecycle_events`; `ALTER TABLE content_source_versions ADD COLUMN intake_record_id`; append-only triggers; ledger rows. |
| `schema_v2_36.sql` | `v2.36-question-source-asset-evidence` | `BEGIN;`…`COMMIT;` | `question_source_asset_evidence` (question-to-source-asset binding for image-only options); append-only triggers; ledger + change-ledger rows. |
| `schema_v2_37.sql` | `v2.37-question-stage-optional-for-content-library` | `BEGIN;`…`COMMIT;` | Rebuilds `questions` making `stage` and `difficulty` nullable (drop CHECK) while preserving the v2 shape; copy/drop/rename inside one transaction. The wrapper (`apply_schema_v2_37.py`) additionally drops and recreates all triggers/views and runs `PRAGMA foreign_key_check`. |
| `schema_v2_38.sql` | `v2.38-content-item-mathematical-validation-contract` | `BEGIN;`…`COMMIT;` | `content_item_math_validation_evidence`; `content_item_question_eligibility` view (fail-closed: eligibility requires passing math validation + validated primary knowledge mapping + validated difficulty evidence); append-only triggers; ledger rows. |
| `schema_v2_39.sql` | `v2.39-repair-content-item-difficulty-enum` | `BEGIN;`…`COMMIT;` | Rebuilds `content_item_difficulty_evidence` with the correct Chinese difficulty enum; the wrapper refuses to run when the old table contains any row (`v2_39_refuses_ambiguous_legacy_difficulty_values`) and drops/recreates dependent triggers and views. |

## 6. Apply Wrappers `apply_schema_v2_5.py` … `apply_schema_v2_39.py`

All 35 wrappers are tracked at root. Static facts:

- **No wrappers exist** for the baseline `schema_v2.sql` or for `schema_v2_1.sql` … `schema_v2_4.sql`.
- **Dev-bound wrappers (5–15):** `apply_schema_v2_5.py` … `apply_schema_v2_15.py` hard-code `DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"`, refuse to run when that file is missing, and print `APPLIED=<version>` after verifying the ledger row. They cannot target an arbitrary isolated path.
- **Generic wrappers (16–19):** `apply_schema_v2_16.py` … `apply_schema_v2_19.py` accept one explicit database path, execute the SQL file, and require the ledger row afterwards.
- **Generic minimal wrappers (20–27):** accept a path; check ledger first (`MIGRATION_ALREADY_APPLIED` early return); execute and commit. No declared prerequisite checks.
- **Prerequisite-aware wrappers (28–39):** accept a path; check ledger first; declare explicit prerequisite migration versions that must already be present before applying:

| Wrapper | Declared prerequisites |
| --- | --- |
| `apply_schema_v2_28.py` | none declared (only self ledger check) |
| `apply_schema_v2_29.py` | `v2.28-p13c-approval-boundary-and-mapping-revisions`; also performs stable-hash seeding of legacy current revisions in one transaction with rollback |
| `apply_schema_v2_30.py` | `v2.28`, `v2.29` |
| `apply_schema_v2_31.py` | `v2.29` |
| `apply_schema_v2_32.py` | `v2.30`, `v2.31` |
| `apply_schema_v2_33.py` | none declared |
| `apply_schema_v2_34.py` | `v2.30` |
| `apply_schema_v2_35.py` | `v2.34` |
| `apply_schema_v2_36.py` | `v2.35` |
| `apply_schema_v2_37.py` | `v2.36` |
| `apply_schema_v2_38.py` | `v2.37` |
| `apply_schema_v2_39.py` | `v2.38` |

- **Rebuild-sensitive wrappers:** `apply_schema_v2_37.py` drops all triggers and views, applies the rebuild, then recreates them from `sqlite_master` and runs `PRAGMA foreign_key_check`; `apply_schema_v2_39.py` drops only dependent triggers/views of the rebuilt table, refuses ambiguous legacy rows, and runs `PRAGMA foreign_key_check`. These constraints are visible statically; they were not executed.

## 7. Declared Bootstrap Patterns in Approved Tests

- `test_schema_v2.py`: creates a fresh temporary database and `executescript`s `schema_v2.sql`, `schema_v2_1.sql` … `schema_v2_6.sql`; asserts `schema_migrations` contains `v2-initial-2026-07-25` plus `v2.1`…`v2.6` and that expected tables exist.
- `test_schema_v2_17.py`: creates a fresh temporary database and `executescript`s `schema_v2.sql` + `schema_v2_1.sql` … `schema_v2_15.sql`, then calls `apply_schema_v2_16`.
- `test_source_library_intake.py` and `test_content_question_candidate_import.py`: copy a pre-built binary baseline snapshot (`data/dev/backups/p1-2f/teaching_docs_dev.before-p1-2f.<timestamp>.<hash>.db`) and then apply v2.30, v2.34–v2.35 (intake) or v2.30, v2.34–v2.39 (candidate import). The snapshot file is a binary test fixture, not a static bootstrap artifact.

**Static consequence:** a fresh-from-SQL bootstrap of the complete chain (baseline + 1…39) is demonstrated in approved test code only through v2.16; the intake/import-chain tests depend on a pre-built binary copy. No tracked, path-generic, single entrypoint creates a fresh isolated candidate-intake database from the SQL files alone.

## 8. Importer Modules' Declared Schema Expectations

All four modules assume an already-existing project-schema database; none creates tables:

- `source_library_intake.py` (`SCHEMA_VERSION = "content-source-registration-v1"`, importer 1.1.0): writes `content_change_ledger` (via `_insert_event`), `content_import_runs`, `content_import_run_events`, `source_documents`, `content_source_intake_records`, `content_source_intake_lifecycle_events`, `content_source_versions`, `content_source_version_heads`. Requires the v2.34/v2.35 enrichment tables plus the v2 baseline `source_documents`.
- `content_library_extraction.py` (`SCHEMA = "content-library-word-extraction-import-v1"`): requires a current `content_source_versions` row; writes `content_extraction_runs`, `source_content_blocks`, `source_content_assets`, `content_source_lifecycle_events`, `content_import_run_events`, `content_change_ledger`.
- `content_question_candidate_import.py` (`SCHEMA = "content-library-question-candidate-import-v2"`): writes `source_fragments`, `questions` (v2 shape), `content_items`, `content_item_question_links`, `content_item_evidence`, `question_internal_evidence`, `content_item_knowledge_mappings`, `content_item_difficulty_evidence`, `content_item_math_validation_evidence`, `question_source_asset_evidence`, `content_import_runs`/events, `content_change_ledger`. Requires the v2 baseline plus v2.34–v2.39 tables.
- `docx_asset_materializer.py` (`TOOL_ID = "docx-asset-materializer"`, `PROTECTED_DATABASE = data/dev/teaching_docs_dev.db`): writes verified asset evidence into an existing database.
- `collection/import_bridge.py` (collection-004 worktree): declares the production chain `register_sources → import_current_word_source → import_question_candidates → materialize_question_assets` against "an already-initialized isolated project-schema database", queries `content_source_versions` and `questions`, and counts approved questions. It provides no bootstrap entrypoint itself.

## 9. Legacy Compatibility Boundary

Per `docs/governance/LEGACY_MODULE_MANIFEST.json` (ENGINEERING-BASELINE-001), all root Python modules including `database.py`, `apply_schema_v2_*.py`, `source_library_intake.py`, `content_library_extraction.py`, `content_question_candidate_import.py`, and `docx_asset_materializer.py` are classified `legacy` ("no movement without a dedicated adapter work order and contract evidence"). `AGENTS.md` and `MATURITY_AND_WORKTREE_REGISTER.md` treat root code as legacy or transitional until promoted. For the isolated candidate-intake authority:

- **Legacy/reference family:** `schema.sql` + `database.py` + `question_bank.db` (legacy question-bank family); `schema_p1_2c_temporary.sql` and its table shapes (temporary, superseded by v2.26/27/29); dev-bound wrappers `apply_schema_v2_5.py`…`apply_schema_v2_15.py` (dev-DB-specific bootstrap family, not usable for an arbitrary isolated path); the binary snapshot fixture under `data/dev/backups/p1-2f/` (test fixture; a database file, not a static authority); the collection-004 worktree's `collection/import_bridge.py` (declares expectations only).
- **Worktree copies:** `schema*.sql` and `apply_schema_v2*.py` files also exist inside the registered collection worktrees (`extsrc-browser-collection-001`, `extsrc-browser-collection-004`) as copies on other branches; they are not part of the root branch's tracked set and were not used as authority evidence.
- Nothing in this section was deleted or altered.

## 10. File and Ordering Matrix

| Role | Files (root, tracked) | Order signal |
| --- | --- | --- |
| Baseline | `schema_v2.sql` | self-declared baseline; ledger `v2-initial-2026-07-25` |
| Incremental migrations | `schema_v2_1.sql` … `schema_v2_39.sql` (39) | numeric file order 1…39; ledger version strings; wrapper prerequisites for 28/29/30/31/32/34/35/36/37/38/39 |
| Apply wrappers | `apply_schema_v2_5.py` … `apply_schema_v2_39.py` (35) | one wrapper per migration 5…39; none for baseline or 1…4 |
| Legacy baseline | `schema.sql` (+ `database.py`, `question_bank.db`) | outside the v2 chain |
| Temporary reference | `schema_p1_2c_temporary.sql` | unversioned; superseded inside v2 chain |
| Missing candidate | `schema_manager.py` | does not exist (recorded per work order) |

A fresh isolated database built strictly from the v2 chain would be expected to end with 40 ledger versions (`v2-initial-2026-07-25` + `v2.1`…`v2.39`, with `v2.29`'s row inserted by its wrapper). This expectation is derived statically; it was not executed.

## 11. Fact and Uncertainty Boundary

**Facts (statically observed):** file presence/tracking; declared ledger versions; per-file transaction wrappers; wrapper DB-path binding; wrapper prerequisite declarations; table/column/trigger inventories; importer modules' INSERT targets; test bootstrap patterns; absence of `schema_manager.py`; absence of apply wrappers for baseline and v2.1–v2.4; absence of any path-generic complete bootstrap entrypoint; legacy classification of root modules.

**Not measured / not claimed:** that any migration applies successfully on any database; that any test passes; that any database is unchanged; the contents of any database or snapshot file; the behavior of any importer at runtime; the completeness of the chain beyond static dependency declarations (for example, whether v2.17's trigger drop order, v2.25/v2.24 view replacements, or v2.37's trigger/view recreation succeed in an actual SQLite session). `MATH-VALIDATION-005` results were not used as evidence.

**Uncertainties relevant to a future bootstrap:** v2.37 rebuilds `questions` while other tables' triggers reference it (the wrapper's drop/recreate strategy is visible statically but unexecuted); v2.39 refuses to run when `content_item_difficulty_evidence` has rows; v2.20–v2.28 plain `INSERT` ledger rows depend on wrapper-level early returns for re-run safety; the v2.24/v2.25 view-and-trigger replacements must execute in file order. All of these must be proven on a fresh isolated database by a future work order before the bootstrap path is used.
