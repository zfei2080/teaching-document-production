"""Run the project-reviewed P1-4a q013 development-database admission."""
from __future__ import annotations

import argparse

from p1_4a_q013_admission import DEFAULT_BACKUP_DIR, DEFAULT_DATABASE, DEFAULT_REPORT, run_q013_admission


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the P1-4a q013-only automatic admission")
    parser.add_argument("--db", default=DEFAULT_DATABASE)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    args = parser.parse_args()
    report = run_q013_admission(args.db, report_path=args.report, backup_dir=args.backup_dir)
    print(f"report={args.report}")
    print(f"action={report['action']}")
    print(f"database_changes_committed={report['database_changes_committed']}")
    return 0 if report["action"] in {
        "approved_q013_after_rehearsed_automatic_verification",
        "reused_existing_q013_admission",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
