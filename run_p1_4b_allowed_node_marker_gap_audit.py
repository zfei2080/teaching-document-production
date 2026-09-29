from __future__ import annotations
from p1_4b_allowed_node_marker_gap_audit import DEFAULT_REPORT,build_allowed_node_marker_gap_audit,write_allowed_node_marker_gap_audit

def main()->int:
 report=build_allowed_node_marker_gap_audit();write_allowed_node_marker_gap_audit(report,DEFAULT_REPORT);print(f'audit={DEFAULT_REPORT}');print('candidate_counts='+repr(report['candidate_counts']));print('decision='+str(report['decision']));return 0
if __name__=='__main__':raise SystemExit(main())
