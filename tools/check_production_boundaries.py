"""Dependency-free static production-boundary check (ENGINEERING-BASELINE-001).

Rule (minimum, future scope only)
---------------------------------
Future production modules under ``src/teaching_docs/`` may not call
``sqlite3.connect`` outside ``src/teaching_docs/persistence/``.

Scope and guarantees
--------------------
- Only ``src/teaching_docs/**/*.py`` is scanned. Legacy root modules are never
  scanned and can never be flagged by this tool.
- The check passes (exit 0) when the future directory does not exist.
- Detection is AST-based: the literal call expression ``sqlite3.connect(...)``
  only. Occurrences inside comments or string literals, or indirect aliases
  such as ``import sqlite3 as s; s.connect(...)``, are not violations of this
  minimum rule.
- Output is deterministic and path-only; it never contains source content.
- A file that cannot be parsed or decoded is reported as a syntax error so a
  future module is never silently skipped (fail closed).
- No third-party dependency is used; only the Python standard library.

The boundary itself (which future modules belong under ``persistence/``) is a
governance decision for future work orders; this tool only enforces the
directory rule above.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

DEFAULT_ROOT = Path("src/teaching_docs")
PERSISTENCE_DIR = "persistence"


def find_sqlite_connect_calls(tree: ast.AST) -> list[tuple[int, int]]:
    """Return (lineno, col_offset) of literal ``sqlite3.connect(...)`` calls."""
    calls: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "connect"
                and isinstance(func.value, ast.Name)
                and func.value.id == "sqlite3"
            ):
                calls.append((node.lineno, node.col_offset))
    return calls


def check_root(root: Path) -> list[str]:
    """Return deterministic violation lines for Python modules under *root*.

    Returns an empty list when *root* does not exist. Violation lines contain
    only relative paths, line numbers, and fixed labels (no source content).
    """
    if not root.is_dir():
        return []
    violations: list[str] = []
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(root)
        if rel.parts[0] == PERSISTENCE_DIR:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (SyntaxError, UnicodeDecodeError) as exc:
            violations.append(
                f"{rel}:0: syntax error ({type(exc).__name__}); cannot statically check"
            )
            continue
        for lineno, _col in find_sqlite_connect_calls(tree):
            violations.append(f"{rel}:{lineno}: sqlite3.connect outside {PERSISTENCE_DIR}/")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Static production-boundary check (ENGINEERING-BASELINE-001)."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=f"future production root to scan (default: {DEFAULT_ROOT})",
    )
    args = parser.parse_args(argv)
    violations = check_root(args.root)
    for line in violations:
        print(line)
    if violations:
        print(
            f"check_production_boundaries: {len(violations)} violation(s); "
            f"future production code may only call sqlite3.connect under {PERSISTENCE_DIR}/"
        )
        return 1
    print("check_production_boundaries: OK (no violations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
