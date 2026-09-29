import unittest
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

from artifact_validation import (
    build_manifest,
    extract_artifact_view,
    manifest_hash,
    validate_artifact,
)

PNG_1X1 = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
    b'\x00\x00\x00\x0cIDAT\x08\x99c```\x00\x00\x00\x04\x00\x01\x0b\xe7\x02\x9d\x00\x00\x00\x00IEND\xaeB`\x82'
)


def make_docx(paragraphs, include_asset=False, asset_bytes=PNG_1X1):
    content_types = """<?xml version='1.0' encoding='UTF-8'?>
<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>
  <Default Extension='rels' ContentType='application/vnd.openxmlformats-package.relationships+xml'/>
  <Default Extension='xml' ContentType='application/xml'/>
  <Default Extension='png' ContentType='image/png'/>
  <Override PartName='/word/document.xml' ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'/>
</Types>"""
    body = []
    for text in paragraphs:
        body.append(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>")
    if include_asset:
        body.append(
            """
<w:p><w:r><w:drawing><wp:inline xmlns:wp='http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'>
  <a:graphic xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main'>
    <a:graphicData uri='http://schemas.openxmlformats.org/drawingml/2006/picture'>
      <pic:pic xmlns:pic='http://schemas.openxmlformats.org/drawingml/2006/picture'>
        <pic:blipFill><a:blip r:embed='rIdImage1'/></pic:blipFill>
      </pic:pic>
    </a:graphicData>
  </a:graphic>
</wp:inline></w:drawing></w:r></w:p>
"""
        )
    document_xml = f"""<?xml version='1.0' encoding='UTF-8'?>
<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>
  <w:body>{''.join(body)}</w:body>
</w:document>"""
    rels = """<?xml version='1.0' encoding='UTF-8'?>
<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>
  <Relationship Id='rIdImage1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/image' Target='media/image1.png'/>
</Relationships>"""
    mem = BytesIO()
    with ZipFile(mem, 'w', ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', content_types)
        zf.writestr('word/document.xml', document_xml)
        if include_asset:
            zf.writestr('word/_rels/document.xml.rels', rels)
            zf.writestr('word/media/image1.png', asset_bytes)
    return mem.getvalue()


class ArtifactValidationTests(unittest.TestCase):
    def test_manifest_hash_is_deterministic(self):
        payload = make_docx(['1. 题目一（5分）', '2. 题目二（5分）'])
        manifest1 = build_manifest(
            artifact_type='docx', audience='student', content_bytes=payload, source_name='fixture.docx',
            expected_question_numbers=('1', '2'), expected_total_score=10,
        )
        manifest2 = build_manifest(
            artifact_type='docx', audience='student', content_bytes=payload, source_name='fixture.docx',
            expected_question_numbers=('1', '2'), expected_total_score=10,
        )
        self.assertEqual(manifest_hash(manifest1), manifest_hash(manifest2))

    def test_extract_docx_view_reads_questions_and_assets(self):
        payload = make_docx(['1. 题目一（5分）', '2. 题目二（5分）'], include_asset=True)
        manifest = build_manifest(
            artifact_type='docx', audience='teacher', content_bytes=payload, source_name='fixture.docx',
            expected_question_numbers=('1', '2'), expected_total_score=10,
            expected_asset_names=('word/media/image1.png',),
            expected_asset_hashes=(sha256(PNG_1X1).hexdigest(),),
        )
        view = extract_artifact_view(manifest)
        self.assertEqual(view.question_lines[0].number, '1')
        self.assertEqual(view.question_lines[1].score, 5)
        self.assertEqual(view.embedded_asset_names, ('word/media/image1.png',))

    def test_student_answer_leak_fails_closed(self):
        payload = make_docx(['1. 题目一（5分）', '答案：A', '2. 题目二（5分）'])
        manifest = build_manifest(
            artifact_type='docx', audience='student', content_bytes=payload, source_name='student.docx',
            expected_question_numbers=('1', '2'), expected_total_score=10,
        )
        bundle = validate_artifact(manifest)
        self.assertEqual(bundle.overall_status, 'fail')
        leak_check = next(check for check in bundle.checks if check.name == 'answer_leak')
        self.assertEqual(leak_check.status, 'fail')

    def test_question_number_and_score_mismatch_fail(self):
        payload = make_docx(['1. 题目一（5分）', '3. 题目三（6分）'])
        manifest = build_manifest(
            artifact_type='docx', audience='teacher', content_bytes=payload, source_name='teacher.docx',
            expected_question_numbers=('1', '2'), expected_total_score=10,
        )
        bundle = validate_artifact(manifest)
        self.assertEqual(bundle.overall_status, 'fail')
        check = next(check for check in bundle.checks if check.name == 'questions_and_score')
        self.assertEqual(check.status, 'fail')
        self.assertTrue(any(f.code == 'question_numbers_mismatch' for f in check.findings))
        self.assertTrue(any(f.code == 'total_score_mismatch' for f in check.findings))

    def test_asset_binding_mismatch_fails(self):
        payload = make_docx(['1. 题目一（5分）'], include_asset=True)
        manifest = build_manifest(
            artifact_type='docx', audience='teacher', content_bytes=payload, source_name='teacher.docx',
            expected_question_numbers=('1',), expected_total_score=5,
            expected_asset_names=('word/media/other.png',),
            expected_asset_hashes=('deadbeef',),
        )
        bundle = validate_artifact(manifest)
        self.assertEqual(bundle.overall_status, 'fail')
        check = next(check for check in bundle.checks if check.name == 'assets')
        self.assertEqual(check.status, 'fail')

    def test_pdf_is_explicitly_unsupported(self):
        manifest = build_manifest(
            artifact_type='pdf', audience='student', content_bytes=b'%PDF-1.7 stub', source_name='fixture.pdf',
            expected_question_numbers=(), expected_total_score=None,
        )
        bundle = validate_artifact(manifest)
        self.assertEqual(bundle.overall_status, 'unsupported')
        self.assertEqual(bundle.checks[0].status, 'unsupported')


if __name__ == '__main__':
    unittest.main()
