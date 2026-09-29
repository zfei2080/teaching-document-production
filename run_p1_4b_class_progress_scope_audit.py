from __future__ import annotations
from p1_4b_class_progress_scope_audit import DEFAULT_REPORT,build_class_progress_scope_audit,write_class_progress_scope_audit

def main()->int:
 report=build_class_progress_scope_audit();write_class_progress_scope_audit(report,DEFAULT_REPORT);print(f'audit={DEFAULT_REPORT}');print('allowed_node_count='+str(len(report['resolved_progress']['allowed_node_ids'])));print('target_topic_within_progress='+str(report['target_topic_within_progress']));return 0
if __name__=='__main__':raise SystemExit(main())
