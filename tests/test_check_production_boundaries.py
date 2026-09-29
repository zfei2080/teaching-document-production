"""Focused tests for tools/check_production_boundaries.py (L1, unit level).

This module carries the registered pytest ``unit`` marker (see
``pyproject.toml``) and is the first current L1 ``unit``-marked suite
(ENGINEERING-TEST-MARKER-001). The marker is module-level ``pytestmark``, so
the same nine tests remain runnable under unittest.

Run with:
    python -m unittest tests.test_check_production_boundaries
or:
    python -m pytest tests/test_check_production_boundaries.py
or (L1 marker gate):
    python -m pytest -q -m "unit or contract" --ignore=test_pipeline.py
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError:
    pytest = None  # markers only; these tests are plain unittest and the L0 gate has no pytest

if pytest is not None:
    pytestmark = pytest.mark.unit

_TOOLS_PY = Path(__file__).resolve().parents[1] / "tools" / "check_production_boundaries.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_production_boundaries", _TOOLS_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cpb = _load_module()


class CheckProductionBoundariesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "src" / "teaching_docs"

    def _write(self, rel: str, content: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_missing_root_is_not_a_failure(self):
        # The future directory does not exist yet; the check must pass.
        self.assertEqual(cpb.check_root(self.root), [])

    def test_persistence_modules_are_allowed(self):
        self._write("persistence/db.py", "import sqlite3\nconn = sqlite3.connect('x.db')\n")
        self.assertEqual(cpb.check_root(self.root), [])

    def test_non_persistence_module_is_flagged(self):
        self._write("foo.py", "import sqlite3\nconn = sqlite3.connect('x.db')\n")
        lines = cpb.check_root(self.root)
        self.assertEqual(len(lines), 1)
        self.assertIn("foo.py:2:", lines[0])
        self.assertIn("sqlite3.connect", lines[0])
        # Output is path-only: it must not contain any source content.
        self.assertNotIn("x.db", lines[0])
        self.assertNotIn("conn =", lines[0])

    def test_legacy_root_files_are_never_scanned(self):
        legacy = Path(self._tmp.name) / "legacy_db_module.py"
        legacy.write_text(
            "import sqlite3\nconn = sqlite3.connect('legacy.db')\n", encoding="utf-8"
        )
        self.assertEqual(cpb.check_root(self.root), [])

    def test_comments_and_strings_are_not_flagged(self):
        self._write(
            "notes.py",
            "# sqlite3.connect is mentioned here\nlabel = 'sqlite3.connect'\nprint(label)\n",
        )
        self.assertEqual(cpb.check_root(self.root), [])

    def test_indirect_alias_is_not_a_direct_call(self):
        self._write("alias.py", "import sqlite3 as s\ns.connect('x.db')\n")
        self.assertEqual(cpb.check_root(self.root), [])

    def test_syntax_error_fails_closed(self):
        self._write("broken.py", "def broken(:\n")
        lines = cpb.check_root(self.root)
        self.assertEqual(len(lines), 1)
        self.assertIn("broken.py:0:", lines[0])
        self.assertIn("syntax error", lines[0])
        self.assertNotIn("def broken", lines[0])

    def test_multiple_violations_are_sorted(self):
        self._write("z_last.py", "import sqlite3\nsqlite3.connect('a.db')\n")
        self._write("a_first.py", "import sqlite3\nsqlite3.connect('b.db')\n")
        lines = cpb.check_root(self.root)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("a_first.py"), lines)
        self.assertTrue(lines[1].startswith("z_last.py"), lines)

    def test_cli_exit_codes(self):
        self.assertEqual(cpb.main(["--root", str(self.root)]), 0)
        self._write("bad.py", "import sqlite3\nsqlite3.connect('x.db')\n")
        self.assertEqual(cpb.main(["--root", str(self.root)]), 1)


if __name__ == "__main__":
    unittest.main()
