"""Apply source-asset evidence support for question candidates."""
from __future__ import annotations
import sqlite3,sys
from pathlib import Path
MIGRATION="v2.36-question-source-asset-evidence"
REQUIRED_MIGRATION="v2.35-database-enrichment-complete-source-inventory"
def apply(db_path:str|Path)->None:
 c=sqlite3.connect(db_path)
 try:
  c.execute("pragma foreign_keys=on")
  if c.execute("select 1 from schema_migrations where version=?",(MIGRATION,)).fetchone():print(f"MIGRATION_ALREADY_APPLIED={MIGRATION}");return
  if c.execute("select 1 from schema_migrations where version=?",(REQUIRED_MIGRATION,)).fetchone() is None:raise RuntimeError("v2_35_migration_required_before_v2_36")
  c.executescript((Path(__file__).parent/"schema_v2_36.sql").read_text(encoding="utf-8"));print(f"MIGRATION_APPLIED={MIGRATION}")
 finally:c.close()
if __name__=="__main__":
 if len(sys.argv)!=2:raise SystemExit("Usage: python apply_schema_v2_36.py <database_path>")
 apply(sys.argv[1])
