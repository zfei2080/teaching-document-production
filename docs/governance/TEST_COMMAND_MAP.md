# Test Command Map

Defined by [`ENGINEERING-BASELINE-001`](../work_orders/ENGINEERING-BASELINE-001.md) on 2026-08-14. This map separates test levels and gives the commands to run them. Only checks actually executed may be claimed as passing; see the completion report for the results recorded by the baseline session.

## Levels

| Level | Scope | Allowed resources | Status |
| --- | --- | --- | --- |
| L0 | Syntax/import/config/boundary/document checks | Nothing: no source artifact, API, browser, or production DB access | Current |
| L1 | Deterministic unit/contract tests | Temporary fixtures or temporary SQLite only | Current |
| L2 | Isolated importer/schema/bridge integration | Isolated environment; the focused isolated-bootstrap suite is current, broader L2 remains future | Current for the focused isolated-bootstrap suite (2026-08-14, `ISOLATED-BOOTSTRAP-001`, root policy hardened by `ISOLATED-BOOTSTRAP-SAFETY-001`); broader L2 remains future |
| L3 | Authorized source-fidelity evidence | Approved non-production inputs and explicit gate evidence | Future |
| L4 | Delivery Card through `delivered/question_usage` evidence | Formal delivery evidence | Future |

## Commands

### L0 (current)

- `python -m compileall -q tools/check_production_boundaries.py`
- `python -m unittest tests.test_check_production_boundaries`
- `python -m pytest -q tests/test_check_production_boundaries.py` (pytest equivalent)
- `python tools/check_production_boundaries.py` (production-boundary check; exit 0 expected)
- UTF-8 readability and trailing-whitespace checks on changed text files
- `git diff --check`
- Bounded sensitive-text scan on changed documents and configuration

L0 checks never touch source artifacts, APIs, browsers, or production databases.

### L1 (current)

- `python -m pytest -q -m "unit or contract" --ignore=test_pipeline.py` — the current L1 marker gate. As of 2026-08-14 (`ENGINEERING-TEST-MARKER-001`) this command demonstrably selects and passes the focused `unit`-marked production-boundary suite (`tests/test_check_production_boundaries.py`, 9 tests), which is the first current L1 `unit`-marked suite. It is claimed as current only for that focused marked suite; it does not assert coverage of any legacy test.
- `python -m pytest -q tests/test_check_production_boundaries.py` (focused pytest equivalent)
- `python -m unittest tests.test_check_production_boundaries` (focused unittest equivalent)
- `python -m unittest discover -s tests` (unittest discovery; currently the same 9 focused tests)
- Temporary fixtures and temporary SQLite databases only; never a production database.

The archived `test_pipeline.py` collection fact is distinct from this focused gate: observed 2026-08-14, identical with and without this baseline's configuration, `test_pipeline.py` fails pytest collection with `ImportError: cannot import name '_is_renderable_question' from 'lecture_generator'`. It is already excluded from `run_offline_regression.py` as an archived pipeline and is not a current production gate; full-tree pytest selection therefore keeps `--ignore=test_pipeline.py` until a future work order handles that module. No legacy test is reclassified by `ENGINEERING-TEST-MARKER-001`.

### L2 (current for one focused suite only)

- `python -m unittest tests.integration.test_isolated_schema_bootstrap`
- `python -m pytest -q tests/integration/test_isolated_schema_bootstrap.py`
- `python -m pytest -q -m integration tests/integration/test_isolated_schema_bootstrap.py`

As of 2026-08-14 (`ISOLATED-BOOTSTRAP-001`, extended by `ISOLATED-BOOTSTRAP-SAFETY-001`) these commands select and pass the focused `integration`-marked isolated-bootstrap suite (`tests/integration/test_isolated_schema_bootstrap.py`, 22 tests) on `tempfile.TemporaryDirectory()` databases only. The suite proves the v2 fresh-chain bootstrap (exactly 40 ledger versions in order; wrapper-sensitive v2.17/v2.24/v2.25/v2.29/v2.37/v2.39 behavior; clean `PRAGMA foreign_key_check`; path-comparison rejection of existing, outside-root, and protected targets) and the hardened approved-root policy (temporary roots only, or controlled roots under one explicitly configured parent with the exact marker; repository root, repository subdirectories, `<local-scratch>/`, data, source, backup, output, collection, KPLC, browser, and arbitrary roots rejected with `ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP` before target creation; controlled-parent-without-marker and wrong-marker rejection; all test databases and markers removed during cleanup). This is a direct L2 command for that focused suite; it claims no broader L2 coverage and no L3/L4 coverage.

### L3, L4 (future)

- No commands exist yet. The `e2e` (L3/L4) pytest marker is reserved; tests carrying it are not a pass/fail gate.
- L3/L4 require approved inputs, gate evidence, and the governing work orders.

## Legacy Regression Entry

`python run_offline_regression.py` remains the legacy offline regression entry. It is NOT a clean-clone proof, source-fidelity proof, live-browser proof, source-policy proof, or formal-delivery proof. Its historical coverage claims must be re-measured before any promotion decision.

Baseline session decision for `ENGINEERING-BASELINE-001`: the legacy regression was run once on 2026-08-14 and recorded: `Ran 318 tests in 663.425s; FAILED (failures=1)`. The single failure is the pre-existing `test_legacy_entry_blocking.py::test_readme_names_only_the_control_document_as_current_entry` assertion about `README.md` (identical failure observed in pytest with and without this baseline's configuration; `README.md` is outside this work order's scope). The script's own guard confirmed `protected_databases_unchanged=True` (SHA-256 of `question_bank.db` and `data/dev/teaching_docs_dev.db` before and after). No new or modified files resulted from the run. This run is not a pass gate for any promotion decision.
