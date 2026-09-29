"""Print bounded current-state checks for the isolated automatic admission path."""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"
REQUIRED = (
    "source_fidelity",
    "structural_consistency",
    "mathematical_independent",
    "textbook_scope",
    "asset_semantics",
)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        latest = """
            WITH ranked AS (
                SELECT question_id, verification_type, status,
                       ROW_NUMBER() OVER (
                           PARTITION BY question_id, verification_type
                           ORDER BY verified_at DESC, rowid DESC
                       ) AS rank
                FROM question_verifications
            )
            SELECT verification_type, status, COUNT(*)
            FROM ranked
            WHERE rank=1
            GROUP BY verification_type, status
            ORDER BY verification_type, status
        """
        print("LATEST_EVIDENCE")
        for row in conn.execute(latest):
            print("|".join(str(value) for value in row))
        print("QUESTION_STATES")
        for row in conn.execute(
            "SELECT quality_status, review_status, COUNT(*) FROM questions GROUP BY quality_status, review_status ORDER BY quality_status, review_status"
        ):
            print("|".join(str(value) for value in row))
        print("APPROVED_QUESTION_COUNT=" + str(conn.execute(
            "SELECT COUNT(*) FROM questions WHERE quality_status='approved' AND review_status='approved'"
        ).fetchone()[0]))
        print("CATALOG_RELEASE_COUNT=" + str(conn.execute(
            "SELECT COUNT(*) FROM catalog_releases"
        ).fetchone()[0]))
        print("REQUIRED_TYPES=" + ",".join(REQUIRED))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
