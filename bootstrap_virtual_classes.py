"""Create non-production virtual classes for end-to-end workflow tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"


VIRTUAL_CLASSES = (
    {
        "id": "demo-j-nbsd-a",
        "name": "虚拟初中演示 A 班",
        "textbook_id": "demo-nbsd-v1",
        "grade_level": "八升九",
        "profile": {"level": "常规", "purpose": "复习巩固", "is_virtual": True},
        "lesson_minutes": 120,
    },
    {
        "id": "demo-j-nbsd-b",
        "name": "虚拟初中演示 B 班",
        "textbook_id": "demo-nbsd-v1",
        "grade_level": "八升九",
        "profile": {"level": "常规", "purpose": "复习巩固", "is_virtual": True},
        "lesson_minutes": 120,
    },
)


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Development database is missing: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            conn.execute(
                """INSERT OR IGNORE INTO textbooks
                (id, name, subject, publisher, status, catalog_version)
                VALUES ('demo-nbsd-v1', '北师大版', '数学', '虚拟验收教材', 'active', 'demo-v1')"""
            )
            conn.execute(
                """INSERT OR IGNORE INTO rule_sets
                (id, document_type, purpose, grade_scope, rules_json, version, status)
                VALUES ('demo-exercise-rules-v1', 'exercise', '同步巩固', '初中', ?, 'v1', 'active')""",
                (json.dumps({"is_virtual": True, "min_quality": "approved"}, ensure_ascii=False),),
            )
            for item in VIRTUAL_CLASSES:
                conn.execute(
                    """INSERT OR IGNORE INTO classes
                    (id, name, textbook_id, grade_level, student_profile_json, default_lesson_minutes)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        item["id"], item["name"], item["textbook_id"], item["grade_level"],
                        json.dumps(item["profile"], ensure_ascii=False), item["lesson_minutes"],
                    ),
                )
        classes = conn.execute("SELECT id, name, grade_level FROM classes WHERE id LIKE 'demo-%' ORDER BY id").fetchall()
        print("VIRTUAL_CLASSES=", classes)
        print("APPROVED_QUESTIONS=", conn.execute("SELECT COUNT(*) FROM questions WHERE quality_status='approved' AND review_status='approved'").fetchone()[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
