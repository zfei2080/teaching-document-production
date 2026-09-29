"""Explicit Word COM extraction entry point for registered source versions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from content_library_extraction import import_current_word_source

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = ROOT / "\u9898\u5e93\u6e90\u6587\u4ef6"
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"
DEFAULT_MANIFEST_ROOT = ROOT / "data" / "dev" / "content-extraction-manifests"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract explicitly selected registered Word source versions into the database.")
    parser.add_argument("--source-version", action="append", required=True, help="Current registered source version id; repeatable.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--manifest-root", type=Path, default=DEFAULT_MANIFEST_ROOT)
    parser.add_argument("--actor", default="codex")
    arguments = parser.parse_args()
    connection = sqlite3.connect(arguments.database)
    try:
        results = [
            import_current_word_source(
                connection, source_version_id=source_version_id, source_root=arguments.source_root,
                workspace=ROOT, manifest_root=arguments.manifest_root, actor=arguments.actor,
            ).as_dict()
            for source_version_id in arguments.source_version
        ]
    finally:
        connection.close()
    print(json.dumps({"results": results}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
