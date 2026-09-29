# Isolated Schema Authority

- **Work order:** `SCHEMA-AUTHORITY-001`
- **Date:** 2026-08-14
- **Decision:** **Conditionally selected** (see section 2)
- **Scope:** applies only to a **future isolated candidate-intake database**. This document never declares any existing database — development, production, backup, historical, or test snapshot — authoritative.

## 1. Purpose

This document names the single authoritative schema/migration path for future isolated candidate-intake databases so that a later implementation session (for example `SUPPLY-BRIDGE-001`) does not choose between `schema.sql`, `schema_v2.sql`, numbered `schema_v2_*` files, and ad hoc application scripts by guesswork. It records the baseline, the ordered migration chain, the version ledger, the idempotence/transaction expectations, the bootstrap contract, the legacy compatibility boundary, the capabilities the later bridge can rely on, and the gaps that require an explicit future migration.

Evidence and uncertainty are detailed in `docs/governance/SCHEMA_MIGRATION_INVENTORY.md`; the durable decision is recorded in `docs/adr/ADR-0002-isolated-schema-authority.md`.

## 2. Authority Decision (Conditionally Selected)

**Baseline:** `schema_v2.sql` (self-declared unified development baseline; records `v2-initial-2026-07-25` in `schema_migrations`).

**Ordered incremental migrations:** `schema_v2_1.sql` → `schema_v2_2.sql` → … → `schema_v2_39.sql`, strictly in numeric order, with the wrapper-declared prerequisites (`apply_schema_v2_28.py` … `apply_schema_v2_39.py`; see inventory section 6).

**Version marker and ledger:** `schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`, created by the baseline. A fresh database built from the chain is expected to end with 40 ledger rows (`v2-initial-2026-07-25` + `v2.1`…`v2.39`; the `v2.29` row is inserted by `apply_schema_v2_29.py`, not by its SQL file).

**Transaction/idempotence expectations:** migrations 31–39 declare their own `BEGIN…COMMIT` (31–33 `BEGIN IMMEDIATE`); 29 declares `BEGIN IMMEDIATE` with foreign-key toggling and commits via its wrapper; 27 toggles foreign keys around a rebuild; 1–28 and 30 rely on caller/`executescript` transaction semantics. Idempotence is "check ledger, then apply": wrappers 16–39 early-return `MIGRATION_ALREADY_APPLIED` when the version is present; SQL files 1–8 use `INSERT OR IGNORE` ledger rows; `ALTER TABLE` statements (v2.3, v2.20, v2.21, v2.28, v2.35) are not re-runnable at SQL level and are protected only by the wrapper guards.

**Why conditionally selected:** the complete, path-generic, single bootstrap entrypoint **does not exist** in the approved static set. Apply wrappers 5–15 hard-code the development database path (`data/dev/teaching_docs_dev.db`); no wrappers exist for the baseline or migrations 1–4; the intake-chain tests bootstrap from a pre-built binary snapshot rather than from the SQL files. The static artifacts therefore define *what* the authority is but not yet a *reproducible bootstrap* for an arbitrary isolated path.

**Bootstrap prerequisite — PROVEN by `ISOLATED-BOOTSTRAP-001` on 2026-08-14; root admission HARDENED by `ISOLATED-BOOTSTRAP-SAFETY-001` on 2026-08-14:**

1. The path-generic bootstrap entrypoint exists: `tools/bootstrap_isolated_schema.py` (standard library only), which creates a fresh database from `schema_v2.sql` plus `schema_v2_1.sql`…`schema_v2_39.sql` in numeric order under an explicit root/target pair, rejecting existing, outside-root, and known protected database paths (`question_bank.db`, `data/dev/teaching_docs_dev.db`, `data/dev/backups/`) by path comparison only.
2. The fresh-database proof passed on a `tempfile.TemporaryDirectory()` database: the chain completes with exactly 40 ledger versions in numeric order (the v2.29 row inserted by the wrapper path); the v2.37 `questions` rebuild (trigger/view drop-recreate, `PRAGMA foreign_key_check`) completes; the v2.39 rebuild runs with empty `content_item_difficulty_evidence` and its wrapper refusal was proven to fail closed on a non-empty fixture; the v2.24/v2.25 view-and-trigger replacements and the v2.17 rebuild execute in file order; the final `PRAGMA foreign_key_check` is clean.
3. **Approved-root policy (`ISOLATED-BOOTSTRAP-SAFETY-001`):** the runner admits only roots lexically contained by the system temporary directory, or controlled roots lexically contained by one explicitly configured parent (`--controlled-root-parent` or `ISOLATED_BOOTSTRAP_CONTROLLED_ROOT_PARENT`; no default) that carry the marker file `.isolated-bootstrap-controlled-root` with the fixed non-secret value `isolated-bootstrap-controlled-root-v1`. The repository root, repository subdirectories, `<local-scratch>/`, data, source, backup, output, collection, KPLC, browser, and arbitrary roots are rejected with the stable `ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP` error before target creation. The policy uses only path comparison and the marker text; the 22-test extended integration suite and CLI end-to-end runs proved all admission/rejection branches on temporary fixtures only.

Evidence: [`docs/governance/ISOLATED_BOOTSTRAP_CONTRACT.md`](../governance/ISOLATED_BOOTSTRAP_CONTRACT.md) and the completion reports [`docs/completion_reports/ISOLATED-BOOTSTRAP-001.md`](../completion_reports/ISOLATED-BOOTSTRAP-001.md) and [`docs/completion_reports/ISOLATED-BOOTSTRAP-SAFETY-001.md`](../completion_reports/ISOLATED-BOOTSTRAP-SAFETY-001.md). The controlled-baseline-snapshot alternative was not used and remains unauthorized. The proof covers only a fresh empty bootstrap; a later bridge work order still requires its own approval (section 6) and must provision its controlled root under the hardened policy.

## 3. No-Formal-Database Boundary

- This authority is for a future **isolated candidate-intake database** only. It does not authorize opening, creating, migrating, or inspecting any existing database, and it does not make any development, production, backup, historical, or test database authoritative.
- The binary snapshot fixture under `data/dev/backups/p1-2f/` used by approved tests is a test fixture, not an authority, and must not be copied into the isolated path without the section 2 prerequisite and user approval.
- `schema.sql`/`database.py`/`question_bank.db` is a legacy family and is not the isolated path (section 5).

## 4. Isolated Bootstrap Contract (for a later `SUPPLY-BRIDGE-001`)

A later bridge work order may rely on the following contract once the section 2 prerequisite is proven (it was proven on a fresh temporary database by `ISOLATED-BOOTSTRAP-001` on 2026-08-14 and its root admission hardened by `ISOLATED-BOOTSTRAP-SAFETY-001` on 2026-08-14; see [`docs/governance/ISOLATED_BOOTSTRAP_CONTRACT.md`](../governance/ISOLATED_BOOTSTRAP_CONTRACT.md)):

- The isolated database is built from `schema_v2.sql` + `schema_v2_1.sql`…`schema_v2_39.sql` in numeric order, with ledger rows as the version truth; any database whose ledger does not match the expected 40 rows is not in the authoritative state and must be rebuilt or reconciled under an approved work order.
- The bootstrap root must be admitted under the hardened approved-root policy (section 2 item 3): a fresh temporary root, or a controlled root provisioned under an explicitly configured non-repository parent with the exact marker. The bridge work order must provision its controlled root outside the repository and outside `<local-scratch>/`, data, source, backup, output, collection, KPLC, browser, and the system temporary directory; no implicit project-root fallback exists.
- The intake chain (`source_library_intake.register_sources` → `content_library_extraction.import_current_word_source` → `content_question_candidate_import.import_question_candidates` → `docx_asset_materializer.materialize_question_assets`, the chain declared by `collection/import_bridge.py`) assumes an already-initialized project-schema database and writes only into existing tables; the bridge provides no bootstrap and must receive the bootstrapped database.
- Candidate-only semantics are enforced by the schema: `questions.extraction_status IN ('candidate','structured','rejected')` defaults to `candidate`; approval requires the full current-snapshot + five-validator evidence chain (v2.13/v2.14, replaced admission triggers v2.30); controlled content segments are `candidate_only` by constraint.
- All evidence tables added by v2.22–v2.39 are append-only by trigger; delivery-side state machines (v2.15, v2.16, v2.17, v2.19) are database-enforced and fail closed.

## 5. Legacy Compatibility Boundary

For the selected isolated path, the following are **legacy / reference / unsupported** and must not be altered, deleted, or used as the bootstrap:

- `schema.sql`, `database.py`, and the `question_bank.db` family (legacy question-bank schema; no ledger; ad hoc column migrations).
- `schema_p1_2c_temporary.sql` (temporary unversioned schema, superseded inside the v2 chain by v2.26/v2.27/v2.29).
- Dev-bound wrappers `apply_schema_v2_5.py`…`apply_schema_v2_15.py` (hard-coded development DB path; usable only for that database, never for an arbitrary isolated path).
- Historic application scripts and root modules (legacy per `docs/governance/LEGACY_MODULE_MANIFEST.json`).
- The collection worktree copies of the schema files (other branches; not authority evidence).
- `schema_manager.py` does not exist anywhere; no session should look for it.

## 6. Supply-Bridge Contract Readiness

Against the `SUPPLY_DELIVERY_CONTRACT` requirements, the v2 chain statically provides or lacks the following for an isolated candidate-intake database:

| Contract need | Static status in the v2 chain |
| --- | --- |
| Source artifact/version provenance | **Provided:** `source_documents` (+ `original_*`/`trusted_source` via v2.21), `content_source_versions` + `content_source_version_heads` + `content_source_lifecycle_events` (v2.34), `content_source_intake_records` + lifecycle (v2.35), `textbook_sources`/`textbook_body_unit_snapshots` (v2.18). |
| Source locator and QSR-like identity linkage | **Partial:** `content_items` + `content_item_question_links` (v2.34), `question_source_asset_evidence` (v2.36), `controlled_question_source_derivations` with `source_question_no` identity (v2.31). **Missing:** any canonical QSR table/identity and any Bridge Manifest record table; QSR remains a contract concept, not a schema object. |
| Parser/import evidence and candidate-only status | **Provided:** `content_import_runs`/`content_import_run_events` (v2.34), `content_extraction_runs` (v2.34), `questions.extraction_status` (baseline), `controlled_content_segments.scope_status='candidate_only'` (v2.26+). |
| Answer/analysis/asset evidence | **Provided:** `question_internal_evidence` (answer/analysis/scoring/solution, v2.34), `content_item_evidence` with student/internal visibility (v2.34), `content_item_math_validation_evidence` (v2.38), `question_source_asset_evidence` (v2.36), asset-adjacency chain (v2.12), controlled answer evidence with no-overlap-with-student-content constraint (v2.31). |
| Semantic duplicate signals without identity collapse | **Partial:** `questions.content_hash UNIQUE`, evidence/mapping `*_hash UNIQUE` columns exist, and the collection side records `semantic_hash` (collection ledger, outside this schema). **Missing:** a schema object that stores cross-question semantic-duplicate review signals for the isolated database; per the contract, such signals may support duplicate review but must never assert QSR identity. |
| Later eligibility, selection, document delivery, same-class usage history | **Provided (baseline + v2.15/16/17/19/38):** `question_usage` (immutable, delivered-only), `selection_plans`/`selection_plan_questions`, `teaching_documents`/`document_questions`, `quality_reports`, `class_progress_controls`, `question_reuse_authorizations`, `artifact_validation_evidence`, `content_item_question_eligibility` view. |

**Explicit `SUPPLY-BRIDGE-001` (or later) requirements — designed here only as requirements, not SQL:**

0. Provision the controlled bootstrap root under the hardened approved-root policy (`ISOLATED-BOOTSTRAP-SAFETY-001`, section 2 item 3): a dedicated parent outside the repository, `<local-scratch>/`, data, source, backup, output, collection, KPLC, browser, and the system temporary directory, with the exact marker file; no implicit fallback exists.
1. Canonical QSR identity table (or equivalent revision identity object) that the bridge manifest can bind exactly to source + question revision.
2. Bridge Manifest records (or an equivalent reconciliation table) with exact source-to-QSR binding or declared failure, closing gate G2 of the supply contract.
3. Cross-question semantic duplicate-review signal storage that cannot collapse identity.
4. Any KPLC/collection linkage fields required by later reconciliation work (to be defined by that work order).

The schema currently lacks these; a future work order must add them as explicit migrations in the chain — never as silent schema mutation.

## 7. Forbidden Shortcuts

- Declaring any existing database authoritative, or copying a test/baseline snapshot into the isolated path without the section 2 prerequisite and user approval.
- Choosing `schema.sql`/`database.py` or `schema_p1_2c_temporary.sql` as the isolated baseline.
- Running dev-bound wrappers (5–15) against an arbitrary path, or applying migrations out of numeric order or without their declared prerequisites.
- Skipping or guessing the `schema_migrations` ledger, or treating ledger absence as authority.
- Applying the v2.37 or v2.39 rebuilds on a database that is not empty where the wrappers require emptiness, or silently "fixing" the v2.34 difficulty-enum defect outside the v2.39 repair.
- Silently mutating any schema object to satisfy the missing bridge capabilities; each gap requires an explicit future migration in the chain.

## 8. Fact and Uncertainty Boundary

This document records static evidence only. It does not claim that any migration applies successfully, that any test passes, or that any database is unchanged — none of those actions was performed by `SCHEMA-AUTHORITY-001`. Runtime behavior of the v2.17/v2.24/v2.25/v2.37/v2.39 rebuilds on a fresh database was unmeasured at the time of this decision and has since been proven on a fresh temporary database by `ISOLATED-BOOTSTRAP-001` (2026-08-14); root admission was subsequently hardened and proven by `ISOLATED-BOOTSTRAP-SAFETY-001` (2026-08-14); see [`docs/governance/ISOLATED_BOOTSTRAP_CONTRACT.md`](../governance/ISOLATED_BOOTSTRAP_CONTRACT.md). That proof covers only a fresh empty bootstrap under the approved-root policy, not importer correctness, source fidelity, QSR, bridge identity, candidate qualification, production safety, or delivery. `MATH-VALIDATION-005` results were not used as evidence.
