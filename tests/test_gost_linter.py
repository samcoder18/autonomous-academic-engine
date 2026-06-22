"""Tests for academic_engine.gost_linter."""

from __future__ import annotations

import unittest
from textwrap import dedent

from academic_engine.gost_linter import (
    extract_bibliography_entries,
    lint_bibliography,
    lint_to_blockers,
)

_VALID_MARKDOWN = dedent(
    """\
    # Глава 1

    Тело главы.

    ## Список использованных источников

    1. Биометрическая идентификация: правовые основы / Иванов И. И. — Москва: Норма, 2024. — 240 с.
    2. О единой биометрической системе: Федеральный закон от 29.12.2022 № 572-ФЗ. — URL: https://publication.pravo.gov.ru/document/0001202212290089.
    """
)


_BROKEN_MARKDOWN = dedent(
    """\
    ## Список использованных источников

    1. Too short.
    2. Полноценная запись без точки в конце / Петров П. П. — Москва: Норма, 2024. — 240 с
    3. Первый дубликат / Сидоров С. С. — URL: https://example.com/a.
    4. Второй дубликат / Сидоров С. С. — URL: https://example.com/a.
    """
)


class ExtractionTests(unittest.TestCase):
    def test_extracts_entries_under_bibliography(self) -> None:
        entries = extract_bibliography_entries(_VALID_MARKDOWN)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].index, 1)

    def test_ignores_entries_outside_section(self) -> None:
        markdown = "# Глава\n\n1. Не библиография.\n"
        self.assertEqual(extract_bibliography_entries(markdown), [])


class LinterTests(unittest.TestCase):
    def test_valid_markdown_has_no_blockers(self) -> None:
        report = lint_bibliography(_VALID_MARKDOWN)
        self.assertFalse(report.has_blockers, report.to_dict())

    def test_reports_too_short_and_missing_period(self) -> None:
        report = lint_bibliography(_BROKEN_MARKDOWN)
        codes = {issue.code for issue in report.issues}
        self.assertIn("too-short", codes)
        self.assertIn("missing-terminal-period", codes)

    def test_detects_duplicate_url(self) -> None:
        report = lint_bibliography(_BROKEN_MARKDOWN)
        codes = {issue.code for issue in report.issues}
        self.assertIn("duplicate-canonical-url", codes)

    def test_lint_to_blockers_uses_gost_category(self) -> None:
        blockers = lint_to_blockers(_BROKEN_MARKDOWN)
        self.assertTrue(blockers)
        self.assertTrue(all(b.category == "gost-bibliography" for b in blockers))


if __name__ == "__main__":
    unittest.main()
