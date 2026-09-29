"""P1-3b command-line entry point for the q013 mapping approval exercise."""
from __future__ import annotations

import argparse

from p1_3b_mapping_approval import DEFAULT_DATABASE, DEFAULT_REPORT, DEFAULT_SOURCE, run_p1_3b


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fail-closed P1-3b q013 mapping approval exercise")
    parser.add_argument("--db", default=DEFAULT_DATABASE)
    parser.add_argument("--mapping-source", default=DEFAULT_SOURCE)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = run_p1_3b(args.db, mapping_source=args.mapping_source, report_path=args.report)
    print(f"report={args.report}")
    print(f"action={report['action']}")
    print(f"fit_status={report['fit_status']}")
    return 0 if report["action"] in {"approved_one_mapping", "reused_current_approved_mapping"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
