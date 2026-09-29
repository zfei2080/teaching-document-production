"""Explicit import entry for source-derived knowledge and worked-example items."""
from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path
from content_knowledge_item_import import import_knowledge_items
ROOT=Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT=ROOT/"\u9898\u5e93\u6e90\u6587\u4ef6"
DEFAULT_DATABASE=ROOT/"data"/"dev"/"teaching_docs_dev.db"
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("--source-version",action="append",required=True);p.add_argument("--source-root",type=Path,default=DEFAULT_SOURCE_ROOT);p.add_argument("--database",type=Path,default=DEFAULT_DATABASE);p.add_argument("--actor",default="codex");a=p.parse_args();c=sqlite3.connect(a.database)
 try: results=[import_knowledge_items(c,source_version_id=x,source_root=a.source_root,workspace=ROOT,actor=a.actor).as_dict() for x in a.source_version]
 finally:c.close()
 print(json.dumps({"results":results},ensure_ascii=False,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
