"""Write the read-only P1-2e conversion-prerequisite audit."""
from __future__ import annotations

from pathlib import Path

from p1_2e_content_conversion_prerequisite_audit import (
    DEFAULT_DATABASE,
    DEFAULT_REPORT,
    build_conversion_prerequisite_audit,
    write_conversion_prerequisite_audit,
)


def main() -> int:
    report = build_conversion_prerequisite_audit(DEFAULT_DATABASE)
    destination = write_conversion_prerequisite_audit(report, DEFAULT_REPORT)
    print(f"report={destination}")
    print(f"status={report['status']}")
    print("database_unchanged=" + str(report["database"]["unchanged"]))
    return 0 if report["database"]["unchanged"] and report["status"] in {"ready", "blocked"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
