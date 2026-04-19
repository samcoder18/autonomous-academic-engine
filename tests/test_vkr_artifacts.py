"""Tests for telegram_console.vkr_artifacts."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from telegram_console.vkr_artifacts import (
    build_bundle,
    load_metadata,
    render_bundle,
    write_bundle,
)


def _write_metadata(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(contents), encoding="utf-8")


_ABSTRACT_RU = "А" * 220
_ABSTRACT_EN = "A" * 220


def _valid_metadata_toml() -> str:
    return f"""
        title = "Защита биометрических данных"
        university = "СОГУ им. К. Л. Хетагурова"
        faculty = "Юридический факультет"
        department = "Кафедра конституционного права"
        year = 2026
        city = "Владикавказ"
        research_tasks = ["Проанализировать 572-ФЗ", "Сопоставить с GDPR"]
        defense_date = "2026-06-20"

        [program]
        code = "40.03.01"
        name = "Юриспруденция"

        [author]
        full_name = "Иванова Анна Петровна"
        group = "ЮР-22"

        [supervisor]
        full_name = "Петров Петр Петрович"
        degree = "к.ю.н."
        position = "доцент"

        [abstract]
        ru = "{_ABSTRACT_RU}"
        en = "{_ABSTRACT_EN}"

        [keywords]
        ru = ["биометрия", "572-ФЗ", "конституция", "персональные данные"]
        en = ["biometrics", "572-FZ", "constitution", "personal data"]
    """


class LoadMetadataTests(unittest.TestCase):
    def test_missing_file_reports_blocker(self) -> None:
        metadata, issues = load_metadata(Path("/nonexistent/metadata.toml"))
        self.assertIsNone(metadata)
        self.assertEqual(issues[0].code, "metadata-missing")

    def test_valid_metadata_roundtrips(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.toml"
            _write_metadata(path, _valid_metadata_toml())
            metadata, issues = load_metadata(path)
            self.assertEqual(issues, [])
            self.assertIsNotNone(metadata)
            assert metadata is not None
            self.assertEqual(metadata.author_full_name, "Иванова Анна Петровна")
            self.assertEqual(metadata.program_code, "40.03.01")
            self.assertEqual(len(metadata.keywords_ru), 4)

    def test_missing_required_fields_aggregate(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.toml"
            _write_metadata(
                path,
                """
                title = "X"
                year = 2026
                city = "Moscow"

                [author]
                full_name = ""

                [program]
                code = ""

                [abstract]
                ru = ""
                en = ""

                [keywords]
                ru = []
                en = []
                """,
            )
            metadata, issues = load_metadata(path)
            self.assertIsNone(metadata)
            codes = {issue.code for issue in issues}
            self.assertIn("metadata-missing-university", codes)
            self.assertIn("metadata-author-missing-full_name", codes)
            self.assertIn("abstract-ru-too-short", codes)
            self.assertIn("keywords-ru-insufficient", codes)


class RenderTests(unittest.TestCase):
    def _build(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "metadata.toml"
        _write_metadata(path, _valid_metadata_toml())
        return path

    def test_bundle_renders_all_files(self) -> None:
        bundle = build_bundle(self._build())
        self.assertFalse(bundle.has_blockers)
        self.assertEqual(
            set(bundle.rendered),
            {
                "title-page.md",
                "abstract-ru.md",
                "abstract-en.md",
                "keywords.md",
                "task-sheet.md",
            },
        )

    def test_title_page_contains_key_fields(self) -> None:
        bundle = build_bundle(self._build())
        title_page = bundle.rendered["title-page.md"]
        self.assertIn("СОГУ", title_page)
        self.assertIn("Иванова Анна Петровна", title_page)
        self.assertIn("40.03.01", title_page)
        self.assertIn("Владикавказ", title_page)

    def test_keywords_block_has_ru_and_en(self) -> None:
        bundle = build_bundle(self._build())
        kw = bundle.rendered["keywords.md"]
        self.assertIn("RU:", kw)
        self.assertIn("EN:", kw)

    def test_abstract_contains_keywords(self) -> None:
        bundle = build_bundle(self._build())
        self.assertIn("Ключевые слова", bundle.rendered["abstract-ru.md"])
        self.assertIn("Keywords", bundle.rendered["abstract-en.md"])

    def test_task_sheet_lists_tasks(self) -> None:
        bundle = build_bundle(self._build())
        task_sheet = bundle.rendered["task-sheet.md"]
        self.assertIn("Проанализировать 572-ФЗ", task_sheet)
        self.assertIn("1. ", task_sheet)


class WriteBundleTests(unittest.TestCase):
    def test_write_bundle_creates_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_path = root / "metadata.toml"
            _write_metadata(meta_path, _valid_metadata_toml())
            bundle = build_bundle(meta_path)
            destination = root / "frontmatter"
            written = write_bundle(bundle, destination=destination)
            self.assertEqual(len(written), 5)
            self.assertTrue((destination / "title-page.md").exists())

    def test_write_skips_if_blockers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_path = root / "missing.toml"
            bundle = build_bundle(meta_path)
            destination = root / "frontmatter"
            written = write_bundle(bundle, destination=destination)
            self.assertEqual(written, [])
            self.assertFalse(destination.exists())


class DeterminismTests(unittest.TestCase):
    def test_render_bundle_is_deterministic_apart_from_date(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.toml"
            _write_metadata(path, _valid_metadata_toml())
            metadata, _issues = load_metadata(path)
            assert metadata is not None
            first = render_bundle(metadata)
            second = render_bundle(metadata)
            for key, value in first.items():
                if key == "task-sheet.md":
                    continue
                self.assertEqual(value, second[key])


if __name__ == "__main__":
    unittest.main()
