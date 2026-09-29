"""Run deterministic source-fidelity checks for the current golden sample."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from docx_forensics import extract_paragraphs
from fidelity_audit import all_pass, check_question_fields

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"
SOURCE_DOC = ROOT / "data" / "dev" / "golden-samples" / "golden_source_001.docx"


def main() -> None:
    source_fragments = extract_paragraphs(SOURCE_DOC)
    by_source_id = {f"golden-source-001-p{fragment.index:03d}": fragment for fragment in source_fragments}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, stem, options_json, answer, analysis, question_type FROM questions ORDER BY id"
        ).fetchall()
        failures = []
        for row in rows:
            provenance: dict[str, list[str]] = {}
            for item in conn.execute(
                "SELECT field_name, source_fragment_id FROM question_source_fragments WHERE question_id=? ORDER BY rowid",
                (row["id"],),
            ):
                provenance.setdefault(item["field_name"], []).append(item["source_fragment_id"])
            checks = check_question_fields(
                stem=row["stem"],
                options_json=row["options_json"],
                answer=row["answer"],
                analysis=row["analysis"],
                question_type=row["question_type"],
                fragments=by_source_id,
                provenance=provenance,
            )
            if all_pass(checks):
                print(f"PASS {row['id']}")
            else:
                details = "; ".join(f"{check.field_name}: {check.detail}" for check in checks if not check.passed)
                failures.append((row["id"], details))
                print(f"FAIL {row['id']} {details}")
        print(f"AUDITED_QUESTIONS={len(rows)}")
        print(f"FAILED_QUESTIONS={len(failures)}")
        if failures:
            raise SystemExit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
