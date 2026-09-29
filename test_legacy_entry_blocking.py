"""Negative tests proving archived production entry points fail closed."""

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LEGACY_ENTRIES = ("lecture_generator.py", "batch_processor.py", "run_simple.py")
PROTECTED_PATHS = (
    ROOT / "question_bank.db",
    ROOT / "data" / "dev" / "teaching_docs_dev.db",
)


def digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LegacyEntryBlockingTests(unittest.TestCase):
    def test_all_legacy_cli_entries_fail_closed_without_mutating_databases(self):
        before = {path: digest(path) for path in PROTECTED_PATHS}
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.docx"
            source.write_bytes(b"do-not-delete")
            for entry in LEGACY_ENTRIES:
                result = subprocess.run(
                    [sys.executable, str(ROOT / entry), "--dir", temp_dir, "--topic", "test", "--delete-after"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertNotEqual(result.returncode, 0, entry)
                combined = result.stdout + result.stderr
                self.assertIn("BLOCKED:", combined, entry)
                self.assertIn("docs/交接文档.md", combined, entry)
                self.assertTrue(source.exists(), entry)
                self.assertEqual(source.read_bytes(), b"do-not-delete", entry)
        after = {path: digest(path) for path in PROTECTED_PATHS}
        self.assertEqual(before, after)

    def test_root_legacy_files_do_not_contain_old_write_or_delete_paths(self):
        forbidden = ("question_bank.db", "--delete-after", "unlink(", "os.remove(", "generate_both(")
        for entry in LEGACY_ENTRIES:
            content = (ROOT / entry).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, content, f"{entry}: {token}")

    def test_readme_names_only_the_control_document_as_current_entry(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/交接文档.md", readme)
        self.assertIn("尚不能生成或交付正式教学文档", readme)
        self.assertNotIn("python lecture_generator.py", readme)
        self.assertNotIn("python batch_processor.py", readme)


if __name__ == "__main__":
    unittest.main()
