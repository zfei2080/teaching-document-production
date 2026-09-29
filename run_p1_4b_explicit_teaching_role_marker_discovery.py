"""Write the read-only P1-4b literal teaching-role marker candidate audit."""
from __future__ import annotations

from pathlib import Path

from p1_4b_explicit_teaching_role_marker_discovery import (
    ROOT,
    build_explicit_teaching_role_marker_discovery,
    write_explicit_teaching_role_marker_discovery,
)


OUTPUT = ROOT / "output" / "audits" / "p1-4b_g1_g3_g7_explicit_marker_discovery.json"


def main() -> int:
    report = build_explicit_teaching_role_marker_discovery()
    output = write_explicit_teaching_role_marker_discovery(report, OUTPUT)
    print(f"audit={output}")
    print("candidate_counts=" + repr(report["candidate_counts"]))
    print("role_assignment_performed=" + str(report["role_assignment_performed"]))
    print("student_document_generation_authorized=" + str(report["student_document_generation_authorized"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
