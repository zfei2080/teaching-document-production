"""Tests for complete DOCX embedded-media instance inventory."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from docx_media_forensics import MediaInstance, ParagraphMedia, media_instances


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
</Types>"""
ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdBody" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/body.png"/>
  <Relationship Id="rIdCell" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/cell.png"/>
  <Relationship Id="rIdBox" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/box.png"/>
  <Relationship Id="rIdExternal" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="https://example.test/diagram.png" TargetMode="External"/>
  <Relationship Id="rIdMissing" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/missing.png"/>
</Relationships>"""
HEADER_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/header.png"/>
</Relationships>"""
DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:v="urn:schemas-microsoft-com:vml">
 <w:body>
  <w:p><w:r><w:drawing><wp:anchor behindDoc="0" relativeHeight="42"><wp:positionH relativeFrom="column"><wp:posOffset>123</wp:posOffset></wp:positionH><wp:positionV relativeFrom="paragraph"><wp:align>top</wp:align></wp:positionV><a:blip r:embed="rIdBody"/></wp:anchor></w:drawing></w:r></w:p>
  <w:p><w:r><w:drawing><a:blip r:embed="rIdExternal"/></w:drawing></w:r></w:p>
  <w:p><w:r><w:drawing><a:blip r:embed="rIdMissing"/></w:drawing></w:r></w:p>
  <w:p><w:r><w:drawing><a:blip r:embed="rIdAbsent"/></w:drawing></w:r></w:p>
  <w:tbl><w:tr><w:tc><w:p><w:r><w:pict><v:imagedata r:id="rIdCell"/></w:pict></w:r></w:p></w:tc></w:tr></w:tbl>
  <w:p><w:r><w:pict><w:txbxContent><w:p><w:r><w:drawing><a:blip r:embed="rIdBox"/></w:drawing></w:r></w:p></w:txbxContent></w:pict></w:r></w:p>
  <w:sectPr/>
 </w:body>
</w:document>"""
HEADER_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <w:p><w:r><w:drawing><a:blip r:embed="rIdHeader"/></w:drawing></w:r></w:p>
</w:hdr>"""


class MediaForensicsTests(unittest.TestCase):
    def test_result_dataclasses_preserve_evidence(self):
        paragraph = ParagraphMedia(paragraph_index=7, filenames=("image1.png", "image2.png"))
        self.assertEqual(paragraph.paragraph_index, 7)
        instance = MediaInstance("word/document.xml", "rId1", "word/media/a.png", "a.png", "abc", "body_paragraph", "/x", 2)
        self.assertEqual(instance.relationship_id, "rId1")
        self.assertEqual(instance.paragraph_index, 2)

    def _fixture(self) -> tuple[Path, dict[str, bytes]]:
        handle = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        handle.close()
        path = Path(handle.name)
        media = {
            "word/media/body.png": b"body-image",
            "word/media/cell.png": b"cell-image",
            "word/media/box.png": b"box-image",
            "word/media/header.png": b"header-image",
        }
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", CONTENT_TYPES)
            archive.writestr("_rels/.rels", ROOT_RELS)
            archive.writestr("word/document.xml", DOCUMENT_XML)
            archive.writestr("word/_rels/document.xml.rels", DOC_RELS)
            archive.writestr("word/header1.xml", HEADER_XML)
            archive.writestr("word/_rels/header1.xml.rels", HEADER_RELS)
            for name, data in media.items():
                archive.writestr(name, data)
        return path, media

    def test_inventory_covers_body_table_textbox_and_header_with_hashes(self):
        path, media = self._fixture()
        try:
            result = media_instances(path)
        finally:
            path.unlink(missing_ok=True)
        by_id = {item.relationship_id: item for item in result}
        self.assertEqual(set(by_id), {"rIdBody", "rIdCell", "rIdBox", "rIdHeader", "rIdExternal", "rIdMissing", "rIdAbsent"})
        self.assertEqual(by_id["rIdBody"].host_kind, "body_paragraph")
        self.assertEqual(by_id["rIdBody"].paragraph_index, 0)
        self.assertEqual(by_id["rIdCell"].host_kind, "table_cell")
        self.assertIsNone(by_id["rIdCell"].paragraph_index)
        self.assertEqual(by_id["rIdBox"].host_kind, "textbox")
        self.assertIsNone(by_id["rIdBox"].paragraph_index)
        self.assertEqual(by_id["rIdHeader"].host_kind, "header")
        self.assertEqual(by_id["rIdHeader"].package_part, "word/header1.xml")
        self.assertEqual(by_id["rIdExternal"].relationship_status, "external_target")
        self.assertIsNone(by_id["rIdExternal"].media_sha256)
        self.assertEqual(by_id["rIdMissing"].relationship_status, "missing_media_part")
        self.assertIsNone(by_id["rIdMissing"].media_sha256)
        self.assertEqual(by_id["rIdAbsent"].relationship_status, "missing_relationship")
        self.assertIsNone(by_id["rIdAbsent"].media_sha256)
        layout = dict(by_id["rIdBody"].drawing_layout)
        self.assertEqual(layout["drawing_kind"], "anchor")
        self.assertEqual(layout["anchor.horizontal.posOffset"], "123")
        self.assertEqual(layout["anchor.vertical.align"], "top")
        for item in result:
            if item.relationship_status == "resolved_internal":
                self.assertEqual(item.media_sha256, hashlib.sha256(media[item.media_path]).hexdigest())
            self.assertTrue(item.host_location.startswith("/"))


if __name__ == "__main__":
    unittest.main()
