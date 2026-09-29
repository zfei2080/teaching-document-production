"""Unpack the shipped sample banks into the paths the tests expect.

This edition carries two databases, both with every piece of question text removed:

- `data/sample/p1-2f_baseline_stripped.db.gz` (~1 MB) - a pre-migration snapshot used as the
  starting fixture by the migration and lineage test modules.
- `data/sample/teaching_docs_sample_bank.db.gz` (~51 MB) - the full bank at the end of the
  project's own progression, kept as a scale reference rather than a test fixture.

The test modules were written against snapshots of the development bank, so this tool places
the stripped baseline at the exact paths they read. Everything it writes is under `data/dev/`,
which is gitignored.

    python tools/materialize_sample_bank.py          # fixtures only (fast)
    python tools/materialize_sample_bank.py --full   # also unpack the 188 MB scale bank
"""
from __future__ import annotations

import gzip
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "data" / "sample"

BASELINE_GZ = SAMPLE_DIR / "p1-2f_baseline_stripped.db.gz"
BASELINE_PATHS = (
    ROOT / "data" / "dev" / "backups" / "p1-2f"
    / "teaching_docs_dev.before-p1-2f.20260730T172738Z.8af367e47a55494c9f3a26e552355f4c.db",
    ROOT / "data" / "dev" / "backups" / "p1-2f-20260730"
    / "teaching_docs_dev_before_18A8B0805DCFC4A3.db",
)
FULL_GZ = SAMPLE_DIR / "teaching_docs_sample_bank.db.gz"
FULL_TARGET = ROOT / "data" / "dev" / "teaching_docs_dev.db"


def unpack(gz: Path, target: Path) -> None:
    if not gz.exists():
        print(f"missing packed bank: {gz}", file=sys.stderr)
        raise SystemExit(1)
    if not target.exists():
        with gzip.open(gz, "rb") as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        print(f"unpacked {gz.name} -> {target.relative_to(ROOT)}")


def main() -> int:
    working = SAMPLE_DIR / "_working"
    working.mkdir(parents=True, exist_ok=True)
    baseline = working / "p1-2f_baseline_stripped.db"
    unpack(BASELINE_GZ, baseline)
    for target in BASELINE_PATHS:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(baseline, target)
        print(f"fixture ready: {target.relative_to(ROOT)}")
    if "--full" in sys.argv:
        unpack(FULL_GZ, FULL_TARGET)
    print("note: stripped banks contain no question text, so assertions that compare "
          "content hashes of removed text are expected to report a mismatch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
