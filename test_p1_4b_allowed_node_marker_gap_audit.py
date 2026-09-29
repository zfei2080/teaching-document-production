from __future__ import annotations
from pathlib import Path
import tempfile,unittest
from p1_4b_allowed_node_marker_gap_audit import build_allowed_node_marker_gap_audit
class AllowedNodeMarkerGapAuditTests(unittest.TestCase):
 def test_marker_scan_neither_assigns_roles_nor_rejects_by_directory_label(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'current.doc').write_bytes(b'current');(root/'chapter.doc').write_bytes(b'chapter');(root/'conflict.doc').write_bytes(b'conflict')
   text={'current.doc':'总结','chapter.doc':'诊断自评','conflict.doc':'诊断自评'}
   report=build_allowed_node_marker_gap_audit(source_root=root,source_specs=(('current.doc','current','current_topic_exact'),('chapter.doc','chapter','chapter_mixed_requires_segment_mapping'),('conflict.doc','conflict','catalog_prior_node_requires_question_level_mapping')),profiler=lambda path:{'normalized_content_text':text[Path(path).name],'normalized_content_sha256':'A'*64,'paragraph_count':1})
   self.assertEqual(report['current_topic_g1_candidate_count'],0)
   self.assertEqual(report['catalog_prior_node_requires_question_level_mapping_marker_count'],2)
   self.assertTrue(report['directory_labels_not_used_for_scope_decision'])
   self.assertTrue(report['marker_scan_is_not_a_role_assignment_gate'])
   self.assertEqual(report['current_topic_g7_self_assessment_candidate_count'],0)
   self.assertEqual(report['decision'],'observational_marker_scan_requires_separate_question_mapping_and_task_role_contract')
   self.assertFalse(report['student_document_generation_authorized'])
if __name__=='__main__':unittest.main()
