"""Apply the isolated P1-4b auxiliary-content evidence schema."""
from __future__ import annotations
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parent
MIGRATION='v2.33-p14b-controlled-auxiliary-content-evidence'
SQL=(ROOT/'schema_v2_33.sql').read_text(encoding='utf-8')

def apply(database: str|Path)->None:
    connection=sqlite3.connect(Path(database))
    try:
        if connection.execute('SELECT 1 FROM schema_migrations WHERE version=?',(MIGRATION,)).fetchone():
            print(f'MIGRATION_ALREADY_APPLIED={MIGRATION}')
            return
        connection.executescript(SQL)
        print(f'MIGRATION_APPLIED={MIGRATION}')
    finally:
        connection.close()

if __name__=='__main__':
    apply(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'data'/'dev'/'teaching_docs_dev.db')
