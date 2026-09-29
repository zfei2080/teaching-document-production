# Isolated Bootstrap Contract

- **Work orders:** `ISOLATED-BOOTSTRAP-001` (2026-08-14), hardened by `ISOLATED-BOOTSTRAP-SAFETY-001` (2026-08-14)
- **Date of proof:** 2026-08-14
- **Runtime evidence:** `tools/bootstrap_isolated_schema.py` executed on a fresh, empty, temporary SQLite database under an explicit temporary root (ISOLATED-BOOTSTRAP-001) and, after the root-admission hardening, under explicitly marked controlled roots (ISOLATED-BOOTSTRAP-SAFETY-001); see [`../completion_reports/ISOLATED-BOOTSTRAP-001.md`](../completion_reports/ISOLATED-BOOTSTRAP-001.md) and [`../completion_reports/ISOLATED-BOOTSTRAP-SAFETY-001.md`](../completion_reports/ISOLATED-BOOTSTRAP-SAFETY-001.md) for the recorded commands and results, and [`../architecture/SCHEMA_AUTHORITY.md`](../architecture/SCHEMA_AUTHORITY.md) for the authority this contract serves.

## 1. Purpose

This document states the runtime contract proven by the isolated-bootstrap work: how a **fresh, empty, temporary** SQLite candidate-intake database is created from the v2 schema chain, what safety properties the bootstrap enforces, which wrapper behaviors it reproduces, and the narrow scope of the proof. It is the handoff contract for a later `SUPPLY-BRIDGE-001` (or equivalent) that may rely on the bootstrapped database.

## 2. Order and Ledger

- Baseline `schema_v2.sql` first, then `schema_v2_1.sql` … `schema_v2_39.sql` strictly in numeric order, all from the repository root (read-only inputs).
- The `schema_migrations(version TEXT PRIMARY KEY, applied_at ...)` ledger is the version truth. A fresh chain completes with **exactly 40 ledger rows** in insertion order: `v2-initial-2026-07-25` followed by `v2.1` … `v2.39`.
- The v2.29 ledger row is inserted by the bootstrap (reproducing `apply_schema_v2_29.py`), not by `schema_v2_29.sql`; every other row is inserted by its own SQL file.
- After every step the runner verifies the step's version is present and the ledger count matches the step index; at completion it verifies the exact 40-version sequence. Any deviation fails the bootstrap (fail closed).

## 3. Approved-Root Policy and Target Safety

Root admission (hardened by `ISOLATED-BOOTSTRAP-SAFETY-001`) runs **before any
target consideration** and is dependency-free and deterministic. Only two root
classes are admitted:

1. **Temporary root:** a root lexically contained by `tempfile.gettempdir()`
   (path comparison only; commonpath semantics, so the temporary directory
   itself is included).
2. **Controlled root:** a root lexically contained by **one explicitly
   configured parent path** AND containing the marker file
   `.isolated-bootstrap-controlled-root` whose UTF-8 text (byte-order mark and
   surrounding whitespace ignored) equals the fixed, non-secret contract value
   `isolated-bootstrap-controlled-root-v1`.

The controlled parent has **no default**. It must be passed via
`--controlled-root-parent` or the `ISOLATED_BOOTSTRAP_CONTROLLED_ROOT_PARENT`
environment variable (CLI value wins; unset or whitespace-only means "not
configured"). It must never be the repository root or a project data, source,
backup, output, collection, KPLC, or browser directory. If no parent is
configured, **only temporary roots are usable**.

Everything else — the repository root, repository subdirectories, `<local-scratch>/`,
data, source, backup, output, collection, KPLC, browser, and arbitrary roots —
is rejected with the stable path-only error
`ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP` before target creation. The policy
uses only lexical path comparison and, for a candidate controlled root, the
marker file text; it never opens a database, snapshot, source artifact, or
protected file. When a controlled parent is configured, any root lexically
under it must carry the exact marker — even a root that also lies under the
temporary directory.

After root admission the fail-closed target checks execute, all by path
comparison only:

- The resolved target must not be a known protected database path
  (`question_bank.db`, `data/dev/teaching_docs_dev.db`, and anything under
  `data/dev/backups/`); rejection never opens or stats the protected file.
- The resolved target must lie under the resolved root.
- The target must not already exist; its parent directory must exist.

The runner creates only the new target file, inside the approved root. The
proofs used only `tempfile.TemporaryDirectory()` roots; every proof target was
absent before and removed after, and marker files existed only in the test
temporary directories.

### Controlled-root provisioning (for a future, separately approved bridge work order)

A later `SUPPLY-BRIDGE-001` (or equivalent) that needs a durable controlled
root must provision it **outside the repository** and outside the system
temporary directory, as follows:

```text
1. Choose a parent directory that is NOT the repository root and NOT a
   project data/source/backup/output/collection/KPLC/browser directory,
   for example D:\isolated-bootstrap-controlled (Unix: /srv/isolated-bootstrap-controlled).
2. Create the controlled root as a fresh subdirectory of that parent,
   for example D:\isolated-bootstrap-controlled\bridge-root (the root must
   not exist beforehand and must be dedicated to isolated bootstrap).
3. Create the marker file <root>\.isolated-bootstrap-controlled-root whose
   UTF-8 text is exactly:  isolated-bootstrap-controlled-root-v1
4. Run the runner with --root <root> and
   --controlled-root-parent <parent> (or set
   ISOLATED_BOOTSTRAP_CONTROLLED_ROOT_PARENT=<parent>).
```

Marker creation must happen only under that dedicated parent; a marker is the
only admission credential besides temporary containment, and it is a fixed,
non-secret contract value. No controlled root may be created inside the
repository, `<local-scratch>/`, data, source, backup, output, collection, KPLC, or
browser directories, and no existing directory may be repurposed as a
controlled root. This section is a provisioning specification only: it
authorizes nothing and no controlled root was created by either bootstrap work
order.

## 4. Wrapper Behavior Reused or Reproduced

The apply wrappers are read-only legacy modules; the runner reproduces their behavior where it matters:

| Migration | Wrapper behavior reproduced |
| --- | --- |
| v2.16–v2.28, v2.30–v2.36, v2.38 | Check-ledger-then-apply; version verified present after the step. |
| v2.28–v2.39 | Declared wrapper prerequisites enforced before each step (v2.29→v2.28; v2.30→v2.28+v2.29; v2.31→v2.29; v2.32→v2.30+v2.31; v2.34→v2.30; v2.35→v2.34; v2.36→v2.35; v2.37→v2.36; v2.38→v2.37; v2.39→v2.38). |
| v2.29 | `BEGIN IMMEDIATE` rebuild script executed, then the wrapper's legacy-current-revision seed query reproduced (zero rows on a fresh chain); the ledger row inserted in the same transaction with commit/rollback; **fails closed** if the seed query ever finds rows. |
| v2.37 | `questions` rebuild dance reproduced verbatim: capture `questions` indexes and every trigger/view, `PRAGMA foreign_keys=OFF`, drop all triggers/views, apply the rebuild SQL, recreate indexes/views/triggers from captured SQL, `PRAGMA foreign_keys=ON`, require `PRAGMA foreign_key_check` clean. |
| v2.39 | Empty-table refusal reproduced verbatim (`content_item_difficulty_evidence` must contain zero rows, otherwise `v2_39_refuses_ambiguous_legacy_difficulty_values`), dependent triggers/views dropped and recreated from captured SQL, `PRAGMA foreign_key_check` clean. |
| v2.17 / v2.24 / v2.25 | The SQL files themselves perform the `quality_reports` rebuild (v2.17) and the view/trigger replacements (v2.24, then the hardened v2.25 replacement) in file order; the runner applies them in numeric order with ledger verification. |

No rebuild, prerequisite, empty-table, or foreign-key check is silently bypassed.

## 5. Runtime Proof (2026-08-14)

- A fresh chain bootstrapped to `STATUS=BOOTSTRAP_OK`, `LEDGER_COUNT=40`, `FOREIGN_KEY_CHECK=clean`, exit 0, on a target that was absent before and removed after.
- Post-bootstrap verification on the fresh database confirmed: v2.17 `quality_reports` rebuilt with `unsupported`/`missing` gate values and its four triggers recreated (and live); v2.24→v2.25 replacement left the hardened four-validator-bundle view and trigger in place; v2.29's wrapper-inserted ledger row and append-only revision machinery present; v2.37 left `questions.stage`/`difficulty` nullable with indexes, triggers, and views preserved and change-ledger row recorded; v2.39 rebuilt `content_item_difficulty_evidence` with the correct enum on an empty table, preserving the v2.38 eligibility view; final `PRAGMA foreign_key_check` clean.
- Protected known paths and outside-root/existing targets were rejected by path comparison, including a non-existent path under the protected backups directory (proving the decision is comparison-only).
- **Root-admission proof (ISOLATED-BOOTSTRAP-SAFETY-001):** the extended 22-test suite proved on `tempfile.TemporaryDirectory()` databases only that temporary roots still bootstrap; the repository root, repository subdirectories, forbidden directory families (`<local-scratch>/`, data, source, backup, output, collection, KPLC, browser), and ordinary roots are rejected with `ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP` before target creation; a configured controlled parent without the marker is rejected; a correctly marked controlled root is accepted and bootstraps fully; wrong or empty marker content is rejected; a forbidden controlled parent is a policy violation; and every test database and marker is removed during cleanup. CLI end-to-end proofs bootstrapped successfully through both `--controlled-root-parent` and `ISOLATED_BOOTSTRAP_CONTROLLED_ROOT_PARENT`, and a CLI run with the repository root rejected with `STATUS=REJECTED ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP` (exit 1) without creating the target.
- Direct commands (see the completion reports for recorded results): `python -m unittest tests.integration.test_isolated_schema_bootstrap` (22 tests OK), `python -m pytest -q tests/integration/test_isolated_schema_bootstrap.py` (22 passed), `python -m pytest -q -m integration tests/integration/test_isolated_schema_bootstrap.py` (22 passed).

## 6. Prohibited Inputs

The bootstrap proof never used, opened, read, hashed, copied, attached, or modified: any existing database (question-bank, development, backup, snapshot, historical, or test); any `.db` file under `<local-scratch>/` or elsewhere; any source artifact or source-question content; any binary snapshot as bootstrap input (the runner creates the database from the SQL chain only); any collection/KPLC output; any browser state; any credentials, cookies, tokens, or passwords. The runner contains no code path that accepts such inputs. No controlled root was created anywhere, and marker files existed only inside the tests' temporary directories.

## 7. Scope Boundary and Handoff Condition

- **The proof covers only a fresh, empty bootstrap from the SQL chain under the approved-root policy.** It does not cover, and does not approve: importer correctness, source fidelity, canonical QSR identity, Bridge Manifest records, bridge identity binding, candidate qualification, duplicate-review signals, collection/KPLC linkage, production safety, or delivery.
- A later `SUPPLY-BRIDGE-001` may rely on this contract to receive a bootstrapped isolated database (ledger = exactly the 40 expected versions; foreign keys clean) **only if** that work order is separately approved by the user, provisions a controlled root under the section 3 policy (never the repository, never `<local-scratch>/`, data, source, backup, output, collection, KPLC, or browser directories, and never the system temporary directory), and implements the missing-capability migrations (canonical QSR identity, Bridge Manifest records, duplicate-review signal storage, and any required KPLC/collection linkage fields) as explicit chain migrations. Nothing in this contract authorizes any follow-on work automatically.
