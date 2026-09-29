"""Command-line entry point for the read-only P1-4 readiness decision."""
from __future__ import annotations

import argparse
from pathlib import Path

from p1_4_pilot_readiness_audit import (
    DEFAULT_DATABASE,
    ROOT,
    build_p1_4_readiness_audit,
    write_p1_4_readiness_audit,
)


DEFAULT_OUTPUT = ROOT / "output" / "audits" / "p1-4_pilot_readiness_audit.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the read-only P1-4 lecture readiness audit.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    report = build_p1_4_readiness_audit(args.db)
    output = write_p1_4_readiness_audit(report, args.output)
    print(f"report={output}")
    print(f"decision={report['decision']}")
    print(f"student_document_generation_authorized={report['student_document_generation_authorized']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
