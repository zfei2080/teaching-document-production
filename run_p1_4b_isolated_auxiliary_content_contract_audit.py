from __future__ import annotations
from p1_4b_isolated_auxiliary_content_contract_audit import DEFAULT_REPORT,run_isolated_auxiliary_content_contract_audit

def main()->int:
 r=run_isolated_auxiliary_content_contract_audit(); print(f'audit={DEFAULT_REPORT}'); print('supply='+repr(r['supply'])); print('live_database_unchanged='+str(r['live_database']['unchanged'])); return 0
if __name__=='__main__': raise SystemExit(main())
