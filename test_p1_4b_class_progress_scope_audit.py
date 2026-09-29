from __future__ import annotations
import unittest
from p1_4b_class_progress_scope_audit import build_class_progress_scope_audit
class ClassProgressScopeAuditTests(unittest.TestCase):
 def test_second_chapter_boundary_contains_isosceles_topic_without_using_source_folder_labels(self):
  report=build_class_progress_scope_audit()
  self.assertTrue(report['database']['unchanged'])
  self.assertTrue(report['target_topic_within_progress'])
  self.assertFalse(report['source_directory_labels_used_for_scope_decision'])
  self.assertGreater(len(report['resolved_progress']['allowed_node_ids']),1)
  self.assertFalse(report['student_document_generation_authorized'])
if __name__=='__main__':unittest.main()
