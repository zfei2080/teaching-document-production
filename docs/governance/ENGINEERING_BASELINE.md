# Engineering Baseline

Established by [`ENGINEERING-BASELINE-001`](../work_orders/ENGINEERING-BASELINE-001.md) on 2026-08-14. This is the minimum reproducible engineering baseline for future contract-scoped implementation in this repository.

## Scope

The baseline applies only to newly created or intentionally modified files. It makes no conformance claim about legacy root modules: nothing in this baseline asserts that root scripts are compliant, packaged, import-safe, tested, or production-ready.

## Baseline Surface

| Item | Location | Purpose |
| --- | --- | --- |
| Python tooling configuration | `pyproject.toml` | Single configuration surface: conservative declared Python requirement, pytest markers (`unit`, `contract`, `integration`, `e2e`), protected-directory recursion exclusion, explicit repository-root `pythonpath`. |
| Editor and encoding conventions | `.editorconfig` | UTF-8, LF, final-newline, and whitespace conventions for Python, Markdown, TOML, JSON, YAML, PowerShell, and GitHub workflow files. Applies prospectively only; it does not repair pre-existing encoding damage and does not authorize bulk reformatting. |
| Test taxonomy and command map | [`TEST_COMMAND_MAP.md`](TEST_COMMAND_MAP.md) | Explicit L0-L4 test levels and commands; the legacy regression entry is bounded. |
| Legacy/module baseline | [`LEGACY_MODULE_MANIFEST.json`](LEGACY_MODULE_MANIFEST.json) | Machine-readable, path/token-level facts about root Python modules. |
| Dependency/environment inventory | [`DEPENDENCY_ENVIRONMENT_INVENTORY.md`](DEPENDENCY_ENVIRONMENT_INVENTORY.md) | Observed environment facts; no dependency installed or changed. |
| Production-boundary check | `tools/check_production_boundaries.py` | Dependency-free static check: `sqlite3.connect` is forbidden outside `src/teaching_docs/persistence/`; future scope only. |
| Test package markers | `tests/contract/`, `tests/integration/`, `tests/e2e/` | Physical markers for the test-taxonomy levels. |

## Legacy Boundary

- All root Python modules are classified `legacy` (see the module manifest). No narrower current classification exists in approved governance documents.
- Legacy modules may only be changed by an approved migration or baseline work order.
- No legacy module may move into a future production location without a dedicated adapter work order and contract evidence.
- The future production location is `src/teaching_docs/`. It does not exist yet, and this baseline does not create it.

## Tooling Decisions

- Ruff is a future proposed tool, not a configured dependency: it was not installed in the implementation environment on 2026-08-14, so `pyproject.toml` contains no `[tool.ruff]` section. See the dependency inventory.
- pytest markers are registered for `unit`, `contract`, `integration`, and `e2e`. `integration` (L2) and `e2e` (L3/L4) are future-facing and must not be used as a pass/fail gate until their governing work orders exist.
- Under `ENGINEERING-TEST-MARKER-001` (2026-08-14), `tests/test_check_production_boundaries.py` is the first current L1 `unit`-marked suite: it carries the registered `unit` marker at module level, and the current L1 marker command (`python -m pytest -q -m "unit or contract" --ignore=test_pipeline.py`) selects and passes its nine focused tests. No legacy test is reclassified and no broader marker coverage is claimed.
- `python run_offline_regression.py` remains the legacy offline regression entry and is bounded as documented in the test command map.
- The production-boundary check enforces only the minimum rule of `ENGINEERING-BASELINE-001`: no `sqlite3.connect` outside `src/teaching_docs/persistence/` in future production code. A stronger rule was not added because it could not be validated without scope expansion; the limitation is documented in the tool's docstring.

## CI

The CI decision and its evidence are recorded in the `ENGINEERING-BASELINE-001` completion report. See also the L0 section of the test command map.

## Fact and Uncertainty Boundary

Environment and module facts were observed on 2026-08-14 and recorded in the inventory and manifest. They were not re-measured afterwards. No schema authority, QSR implementation, supply bridge, KPLC reconciliation, delivery implementation, or CI guarantee is established by this baseline.
