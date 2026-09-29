"""Preview keyword classification candidates without modifying the dev DB.

Usage:
  python run_classification_on_devdb.py [--dry-run] [--textbook TEXTBOOK_ID]

Defaults:
  textbook = bsd-math-grade8-lower-2026-spring-extsrc
  confidence_threshold = 0.35
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_TEXTBOOK = "bsd-math-grade8-lower-2026-spring-extsrc"
DEFAULT_THRESHOLD = 0.35
DB_PATH = Path(__file__).parent / "data" / "dev" / "teaching_docs_dev.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-classify questions to curriculum nodes.")
    parser.add_argument("--dry-run", action="store_true", help="Deprecated; this command is always read-only")
    parser.add_argument("--textbook", default=DEFAULT_TEXTBOOK, help="Target textbook id")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--db", default=str(DB_PATH), help="DB path")
    args = parser.parse_args()

    from question_classifier import classify_questions

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")

    # Find unclassified questions (no entry in question_textbooks for this textbook,
    # or classification_method IS NULL)
    all_qids = [
        r[0] for r in conn.execute("SELECT id FROM questions ORDER BY id")
    ]
    classified = {
        r[0] for r in conn.execute(
            "SELECT question_id FROM question_textbooks WHERE textbook_id=? AND classification_method IS NOT NULL",
            (args.textbook,),
        )
    }
    to_classify = [qid for qid in all_qids if qid not in classified]

    print(f"Total questions: {len(all_qids)}")
    print(f"Already classified: {len(classified)}")
    print(f"To classify: {len(to_classify)}")
    print(f"Textbook: {args.textbook}")
    print(f"Confidence threshold: {args.threshold}")
    print("Read only: True")
    print()

    if not to_classify:
        print("Nothing to classify.")
        conn.close()
        return

    results = classify_questions(
        conn,
        to_classify,
        args.textbook,
        confidence_threshold=args.threshold,
        dry_run=True,
    )
    conn.close()

    mapped = 0
    skipped = 0
    for qid in to_classify:
        matches = results.get(qid, [])
        if matches:
            mapped += 1
            top = matches[0]
            print(f"  [{qid}] → {top['node_name']} (confidence={top['confidence']:.3f})")
            if len(matches) > 1:
                for m in matches[1:]:
                    print(f"            + {m['node_name']} (confidence={m['confidence']:.3f})")
        else:
            skipped += 1
            print(f"  [{qid}] → (low confidence, skipped)")

    print()
    print(f"Summary: mapped={mapped}  skipped={skipped}  total={len(to_classify)}")
    print("(read-only preview: automatic classification never writes mappings or approvals)")


if __name__ == "__main__":
    main()
