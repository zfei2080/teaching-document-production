# Dependency and Environment Inventory

Observed on 2026-08-14 during `ENGINEERING-BASELINE-001`. Records only observed facts. No dependency was installed, upgraded, removed, or pinned by this work order.

## Observed Environment

| Item | Observed value | Method |
| --- | --- | --- |
| Python (PATH `python`) | version string `3.13.14`; executable `<local-path>` | `python --version`; `python -c "import sys; print(sys.executable)"` |
| Python launcher | `py -0p` lists `-V:3.15-32 *` (default marked) at `<local-path>` | `py -0p` |
| pytest | `9.1.1`, available as `python -m pytest` | `python -m pytest --version` |
| Ruff | NOT installed: `ruff` is not on PATH and `python -m ruff` reports `No module named ruff` | `ruff --version`; `python -m ruff --version` |

## Runtime Requirements (observed, not verified)

`requirements.txt` declares runtime dependencies with minimum-version pins (`python-docx>=1.0.0`, `PyMuPDF>=1.23.0`, `openai>=1.0.0`, `Pillow>=10.0.0`, `lxml>=4.9.0`) plus a Chinese header comment. It has no lockfile, no development-tooling section, and no dev/test dependencies.

`package.json` declares a `lecture-generator` project (version 1.0.0) whose `build` and `batch` scripts invoke root Python scripts (`lecture_generator.py`, `batch_processor.py`); it declares no Node dependencies.

Whether these runtime packages are actually installed or importable in this environment was NOT verified explicitly: verification would require import checks or installation, which this work order does not perform.

Indirect observation: full-tree pytest collection (baseline configuration, 2026-08-14) imported all root test modules except the archived `test_pipeline.py` without import errors, which implies the third-party imports used by those modules resolve in this environment. This was not separately verified per package.

## Development Tooling

- pytest 9.1.1 is available in the environment and is the configured test runner in `pyproject.toml` (markers, discovery settings, repository-root `pythonpath`). It is not declared in `requirements.txt` and is not a pinned project dependency.
- Ruff is a future proposed tool. Because it was not available locally on 2026-08-14, `pyproject.toml` contains no `[tool.ruff]` section and this work order adds no Ruff dependency or configuration. A future work order may evaluate Ruff when it is locally available or explicitly declared.
- No linter, formatter, type checker, lockfile, or package manager was added by this work order.

## What Cannot Be Determined Without Installing or Changing Dependencies

- Whether the `requirements.txt` runtime packages are importable in a clean environment.
- Whether legacy root modules run under the declared conservative Python minimum.
- pytest behavior in an environment without pytest installed (relevant to any future CI without installation steps).

## Declared Python Requirement

`pyproject.toml` declares `requires-python = ">=3.9"`. This is a conservative minimum for future code, NOT verified against current local project usage (the implementation environment observes Python `3.13.14`). It is not a claim that legacy root modules support Python 3.9.

## Decision

No dependency is installed, upgraded, removed, or pinned by this work order. `requirements.txt` and `package.json` were not modified. No environment secrets are recorded here.
