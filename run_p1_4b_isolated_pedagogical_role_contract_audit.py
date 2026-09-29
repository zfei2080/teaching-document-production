"""Write the P1-4b disposable pedagogical-role contract audit."""
from __future__ import annotations

import argparse

from p1_4b_isolated_pedagogical_role_contract_audit import (
    DEFAULT_REPORT,
    run_isolated_pedagogical_role_contract_audit,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the P1-4b isolated pedagogical-role contract audit")
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = run_isolated_pedagogical_role_contract_audit(report_path=args.report)
    print(f"report={args.report}")
    print(f"decision={report['decision']}")
    print(f"live_database_unchanged={report['live_database']['unchanged']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
