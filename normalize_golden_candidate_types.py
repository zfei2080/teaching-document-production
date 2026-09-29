"""Normalize mechanically evident types in the B2 golden review sample.

Only source questions 17-19 are changed. Their original document section is
"填空题" and each stem asks for a blank result. This script does not approve
questions or change source-fidelity, answer, analysis, or review state.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"
TARGETS = ("17", "18", "19")


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            rows = conn.execute(
                "SELECT id, source_question_no, question_type FROM questions WHERE source_question_no IN (?, ?, ?)",
                TARGETS,
            ).fetchall()
            if len(rows) != len(TARGETS):
                raise RuntimeError(f"Expected {len(TARGETS)} golden candidates, found {len(rows)}")
            conn.execute(
                "UPDATE questions SET question_type='填空题' WHERE source_question_no IN (?, ?, ?)",
                TARGETS,
            )
        print("NORMALIZED=", [row[1] for row in rows])
        print(
            "STATES=",
            conn.execute(
                "SELECT source_question_no, question_type, quality_status, review_status "
                "FROM questions WHERE source_question_no IN (?, ?, ?) ORDER BY CAST(source_question_no AS INTEGER)",
                TARGETS,
            ).fetchall(),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
