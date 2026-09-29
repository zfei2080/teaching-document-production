from __future__ import annotations
import unittest
from p1_4b_isolated_auxiliary_content_contract_audit import run_isolated_auxiliary_content_contract_audit

class IsolatedAuxiliaryContentContractAuditTests(unittest.TestCase):
 def test_source_bound_example_is_verified_but_summary_cannot_be_relabelled_self_assessment(self):
  report=run_isolated_auxiliary_content_contract_audit()
  self.assertTrue(report['live_database']['unchanged'])
  self.assertTrue(report['isolated_database']['v2_33_applied'])
  self.assertTrue(report['isolated_database']['protected_counts_unchanged'])
  self.assertTrue(report['supply']['worked_example_ready'])
  self.assertTrue(report['supply']['summary_ready'])
  self.assertFalse(report['supply']['self_assessment_ready'])
  self.assertFalse(report['supply']['summary_self_assessment_ready'])
  self.assertTrue(report['self_assessment_from_summary_rejected'])
  self.assertFalse(report['question_role_assignment_performed'])
  self.assertFalse(report['student_document_generation_authorized'])
if __name__=='__main__': unittest.main()
