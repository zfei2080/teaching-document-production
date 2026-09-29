"""Run the authorized P1-3c q013 revised-source mapping approval exercise."""
from __future__ import annotations

import argparse

from p1_3c_mapping_approval import DEFAULT_DATABASE, DEFAULT_EVIDENCE_DIR, DEFAULT_REPORT, run_p1_3c


def main() -> int:
    parser = argparse.ArgumentParser(description="Run P1-3c q013 revised-source mapping approval")
    parser.add_argument("--db", default=DEFAULT_DATABASE)
    parser.add_argument("--evidence-dir", default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = run_p1_3c(args.db, evidence_dir=args.evidence_dir, report_path=args.report)
    print(f"report={args.report}")
    print(f"action={report['action']}")
    print(f"approved_mapping_count={report['approved_mapping_count']}")
    return 0 if report["action"] in {"approved_one_mapping", "reused_current_approved_mapping"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
