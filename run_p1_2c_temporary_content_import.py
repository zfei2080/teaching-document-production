"""Execute P1-2c only against a fresh temporary SQLite database."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import tempfile

from controlled_content_temp_import import build_manifest, import_manifest

ROOT = Path(__file__).resolve().parent


def digest(path: Path) -> str: return sha256(path.read_bytes()).hexdigest().upper()

def main() -> int:
    protected = ROOT / "data" / "dev" / "teaching_docs_dev.db"
    before = digest(protected)
    audit_path = ROOT / "output" / "audits" / "p1-2b_controlled_content_validation.json"
    temp_root = ROOT / "data" / "dev" / "p1-2c-temporary"
    temp_root.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(audit_path, workspace=ROOT)
    with tempfile.TemporaryDirectory(dir=temp_root, prefix="import-") as directory:
        db = Path(directory) / "controlled_content.db"
        conn = sqlite3.connect(db)
        try:
            conn.executescript((ROOT / "schema_p1_2c_temporary.sql").read_text(encoding="utf-8"))
            outcome = import_manifest(conn, manifest)
            counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("controlled_content_import_runs","controlled_content_sources","controlled_content_segments")}
        finally:
            conn.close()
        report = {"schema":"p1-2c-temporary-import-run-v1","outcome":outcome,"counts":counts,"manifest":manifest,"temporary_database_sha256":digest(db),"protected_database_sha256_before":before,"protected_database_sha256_after":digest(protected),"protected_database_unchanged":before==digest(protected)}
    destination = ROOT / "output" / "audits" / "p1-2c_temporary_content_import.json"
    destination.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(f"report={destination}")
    print(f"segments={counts['controlled_content_segments']}")
    print("protected_database_unchanged="+str(report["protected_database_unchanged"]))
    return 0 if counts["controlled_content_segments"] == 2 and report["protected_database_unchanged"] else 2
if __name__ == "__main__": raise SystemExit(main())
