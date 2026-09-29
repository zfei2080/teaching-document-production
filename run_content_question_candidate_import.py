"""Explicit source-question candidate import entry point."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from content_question_candidate_import import import_question_candidates

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = ROOT / "\u9898\u5e93\u6e90\u6587\u4ef6"
DEFAULT_DATABASE = ROOT / "data" / "dev" / "teaching_docs_dev.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create source-derived question candidates for explicit source versions.")
    parser.add_argument("--source-version", action="append", required=True)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--actor", default="codex")
    arguments = parser.parse_args()
    connection = sqlite3.connect(arguments.database)
    try:
        results = [
            import_question_candidates(
                connection, source_version_id=value, source_root=arguments.source_root,
                workspace=ROOT, actor=arguments.actor,
            ).as_dict()
            for value in arguments.source_version
        ]
    finally:
        connection.close()
    print(json.dumps({"results": results}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
