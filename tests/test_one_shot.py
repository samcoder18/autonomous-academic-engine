"""Tests for telegram_console.one_shot."""

from __future__ import annotations

import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from telegram_console.one_shot import (
    OneShotConfig,
    run_one_shot,
    write_report,
)
from telegram_console.originality.corpus import OriginalityCorpus

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _minimal_docx(path: Path) -> Path:
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{_W}">
  <w:body>
    <w:p><w:pPr><w:spacing w:line="360"/></w:pPr>
      <w:r><w:rPr><w:rFonts w:ascii="Times New Roman"/><w:sz w:val="28"/></w:rPr>
        <w:t>Body</w:t></w:r></w:p>
    <w:sectPr><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1701"/></w:sectPr>
  </w:body>
</w:document>"""
    styles = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{_W}">
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
  </w:style>
</w:styles>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/footnotes.xml", f'<w:footnotes xmlns:w="{_W}"/>')
    return path


def _write_metadata(path: Path) -> None:
    abstract = "А" * 220
    abstract_en = "A" * 220
    path.write_text(
        dedent(
            f"""
            title = "Test"
            university = "U"
            year = 2026
            city = "City"

            [program]
            code = "40.03.01"
            name = "Юриспруденция"

            [author]
            full_name = "Иванова А. П."

            [supervisor]
            full_name = "Петров П. П."

            [abstract]
            ru = "{abstract}"
            en = "{abstract_en}"

            [keywords]
            ru = ["a", "b", "c"]
            en = ["a", "b", "c"]
            """
        ),
        encoding="utf-8",
    )


_GOOD_MANUSCRIPT = dedent(
    """\
    # Глава 1

    Тело.

    ## Список использованных источников

    1. Биометрия в России / Иванов И. И. — Москва: Норма, 2024. — 240 с.
    2. О единой биометрической системе: Федеральный закон от 29.12.2022 № 572-ФЗ.
    """
)


class OneShotTests(unittest.TestCase):
    def test_happy_path_reports_submission_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_GOOD_MANUSCRIPT, encoding="utf-8")
            docx = _minimal_docx(root / "thesis.docx")
            metadata = root / "metadata.toml"
            _write_metadata(metadata)
            frontmatter_dir = root / "frontmatter"
            config = OneShotConfig(
                manuscript_md=manuscript,
                docx_path=docx,
                metadata_path=metadata,
                frontmatter_destination=frontmatter_dir,
            )
            report = run_one_shot(config)
            self.assertEqual(report.status, "submission-ready")
            self.assertTrue(all(g.passed for g in report.gates))
            self.assertTrue((frontmatter_dir / "title-page.md").exists())

    def test_missing_docx_is_blocker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_GOOD_MANUSCRIPT, encoding="utf-8")
            config = OneShotConfig(
                manuscript_md=manuscript,
                docx_path=root / "missing.docx",
                metadata_path=None,
                frontmatter_destination=None,
            )
            report = run_one_shot(config)
            self.assertEqual(report.status, "strong-draft-with-blockers")
            codes = {b.code for b in report.all_blockers}
            self.assertIn("docx-missing", codes)

    def test_gost_blocker_downgrades_status(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(
                dedent(
                    """\
                    ## Список использованных источников

                    1. Too short.
                    """
                ),
                encoding="utf-8",
            )
            config = OneShotConfig(
                manuscript_md=manuscript,
                docx_path=None,
                metadata_path=None,
                frontmatter_destination=None,
            )
            report = run_one_shot(config)
            self.assertEqual(report.status, "strong-draft-with-blockers")

    def test_originality_gate_blocks_on_high_similarity(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            passage = "В исследовании рассматривается защита биометрических данных " * 10
            manuscript.write_text(
                "## Список использованных источников\n\n"
                "1. Работа / Иванов И. И. — Москва: Норма, 2024.\n\n"
                f"{passage}\n",
                encoding="utf-8",
            )
            corpus_path = root / "corpus.json"
            corpus = OriginalityCorpus()
            corpus.add_document(
                identifier="ref",
                title="Reference passage",
                text=passage,
            )
            corpus.save(corpus_path)
            config = OneShotConfig(
                manuscript_md=manuscript,
                docx_path=None,
                metadata_path=None,
                frontmatter_destination=None,
                corpus_path=corpus_path,
                originality_threshold=0.2,
            )
            report = run_one_shot(config)
            codes = {b.code for b in report.all_blockers}
            self.assertTrue(any(code.startswith("high-similarity") for code in codes))

    def test_write_report_creates_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_GOOD_MANUSCRIPT, encoding="utf-8")
            config = OneShotConfig(
                manuscript_md=manuscript,
                docx_path=None,
                metadata_path=None,
                frontmatter_destination=None,
            )
            report = run_one_shot(config)
            md_path = root / "report.md"
            json_path = root / "report.json"
            write_report(report, markdown_path=md_path, json_path=json_path)
            self.assertTrue(md_path.exists())
            self.assertTrue(json_path.exists())
            self.assertIn("One-shot VKR report", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
