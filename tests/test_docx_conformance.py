"""Tests for telegram_console.docx_conformance."""

from __future__ import annotations

import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram_console.docx_conformance import (
    ConformanceProfile,
    check_docx,
    check_docx_to_blockers,
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _document_xml(
    *,
    font: str = "Times New Roman",
    size_half_pt: int = 28,
    margin_left_twips: int = 1701,
    margin_right_twips: int = 1134,
    margin_top_twips: int = 1134,
    margin_bottom_twips: int = 1134,
    line_twips: int = 360,
) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{_W}">
  <w:body>
    <w:p>
      <w:pPr>
        <w:spacing w:line="{line_twips}" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="{font}" w:hAnsi="{font}"/>
          <w:sz w:val="{size_half_pt}"/>
        </w:rPr>
        <w:t>Body text</w:t>
      </w:r>
    </w:p>
    <w:sectPr>
      <w:pgMar w:top="{margin_top_twips}" w:right="{margin_right_twips}"
               w:bottom="{margin_bottom_twips}" w:left="{margin_left_twips}"/>
    </w:sectPr>
  </w:body>
</w:document>""".encode()


def _styles_xml(*, include_heading: bool = True) -> bytes:
    heading = (
        """<w:style w:type="paragraph" w:styleId="Heading1">
            <w:name w:val="heading 1"/>
          </w:style>"""
        if include_heading
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{_W}">
  <w:docDefaults>
    <w:rPrDefault><w:rPr/></w:rPrDefault>
  </w:docDefaults>
  {heading}
  <w:style w:type="paragraph" w:styleId="Normal">
    <w:name w:val="Normal"/>
  </w:style>
</w:styles>""".encode()


def _footnotes_xml() -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{_W}"/>""".encode()


def _build_docx(path: Path, *, document: bytes, styles: bytes, footnotes: bytes | None) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        if footnotes is not None:
            archive.writestr("word/footnotes.xml", footnotes)
    return path


class DocxConformanceTests(unittest.TestCase):
    def test_valid_document_has_no_issues(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _build_docx(
                Path(tmp) / "ok.docx",
                document=_document_xml(),
                styles=_styles_xml(include_heading=True),
                footnotes=_footnotes_xml(),
            )
            report = check_docx(path)
            self.assertFalse(report.has_blockers, report.to_dict())

    def test_missing_footnotes_reports_blocker(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _build_docx(
                Path(tmp) / "nofn.docx",
                document=_document_xml(),
                styles=_styles_xml(include_heading=True),
                footnotes=None,
            )
            blockers = check_docx_to_blockers(path)
            codes = {b.code for b in blockers}
            self.assertIn("footnotes-missing", codes)

    def test_wrong_font_and_size_report_issues(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _build_docx(
                Path(tmp) / "bad-font.docx",
                document=_document_xml(font="Arial", size_half_pt=24),
                styles=_styles_xml(include_heading=True),
                footnotes=_footnotes_xml(),
            )
            report = check_docx(path)
            codes = {issue.code for issue in report.issues}
            self.assertIn("font-family-mismatch", codes)
            self.assertIn("font-size-mismatch", codes)

    def test_wrong_margins_report_issues(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _build_docx(
                Path(tmp) / "bad-margins.docx",
                document=_document_xml(margin_left_twips=1000, margin_right_twips=500),
                styles=_styles_xml(include_heading=True),
                footnotes=_footnotes_xml(),
            )
            report = check_docx(path)
            codes = {issue.code for issue in report.issues}
            self.assertIn("margin-left-mismatch", codes)
            self.assertIn("margin-right-mismatch", codes)

    def test_missing_heading_style_reports_issue(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _build_docx(
                Path(tmp) / "no-heading.docx",
                document=_document_xml(),
                styles=_styles_xml(include_heading=False),
                footnotes=_footnotes_xml(),
            )
            report = check_docx(path)
            codes = {issue.code for issue in report.issues}
            self.assertIn("heading-styles-missing", codes)

    def test_missing_file_reports_docx_missing(self) -> None:
        report = check_docx(Path("/nonexistent/thesis.docx"))
        self.assertTrue(report.has_blockers)
        self.assertEqual(report.issues[0].code, "docx-missing")

    def test_profile_override(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _build_docx(
                Path(tmp) / "overridden.docx",
                document=_document_xml(font="Arial"),
                styles=_styles_xml(include_heading=True),
                footnotes=_footnotes_xml(),
            )
            profile = ConformanceProfile(font_family="Arial")
            report = check_docx(path, profile)
            codes = {issue.code for issue in report.issues}
            self.assertNotIn("font-family-mismatch", codes)


if __name__ == "__main__":
    unittest.main()
