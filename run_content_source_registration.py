"""Explicit baseline/delta source registration entry point for database enrichment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from source_library_intake import register_sources

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = ROOT / "\u9898\u5e93\u6e90\u6587\u4ef6"
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="Register trusted source files without implicit rescans.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--baseline", action="store_true", help="Perform the one explicit whole-root baseline registration.")
    mode.add_argument("--file", action="append", dest="files", help="Register one changed/new path relative to source root; repeatable.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--actor", default="codex")
    arguments = parser.parse_args()
    registration_mode = "baseline" if arguments.baseline else "delta"
    connection = sqlite3.connect(arguments.database)
    try:
        result = register_sources(
            connection, source_root=arguments.source_root, mode=registration_mode,
            explicit_paths=None if arguments.baseline else arguments.files,
            workspace=ROOT, actor=arguments.actor,
        )
    finally:
        connection.close()
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
