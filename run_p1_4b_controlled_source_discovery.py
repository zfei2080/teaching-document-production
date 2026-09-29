"""CLI entry point for P1-4b read-only source candidate discovery."""
from __future__ import annotations

import argparse
from pathlib import Path

from p1_4b_controlled_source_discovery import (
    DEFAULT_DATABASE,
    ROOT,
    build_p1_4b_source_discovery,
    write_p1_4b_source_discovery,
)


DEFAULT_OUTPUT = ROOT / "output" / "audits" / "p1-4b_controlled_source_discovery.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover P1-4b source-bound candidate material without importing it.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build_p1_4b_source_discovery(args.db)
    output = write_p1_4b_source_discovery(report, args.output)
    print(f"report={output}")
    print(f"decision={report['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
