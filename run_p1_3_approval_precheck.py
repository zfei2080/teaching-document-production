"""Emit the read-only P1-3 gap report.  No database writes are permitted."""
from __future__ import annotations

from pathlib import Path

from p1_3_approval_precheck import build_p1_3_precheck, write_precheck_report


ROOT = Path(__file__).resolve().parent


def main() -> int:
    report = build_p1_3_precheck(
        ROOT / "data" / "dev" / "teaching_docs_dev.db",
        p12b_audit_path=ROOT / "output" / "audits" / "p1-2b_controlled_content_validation.json",
    )
    destination = write_precheck_report(report, ROOT / "output" / "audits" / "p1-3_first_pilot_approval_precheck.json")
    print(f"report={destination}")
    print(f"decision={report['decision']}")
    print(f"eligible_question_count={report['eligible_question_count']}")
    print(f"database_unchanged={report['database']['unchanged']}")
    return 0 if report["decision"] == "blocked_zero_approval_gap_report" and report["database"]["unchanged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
