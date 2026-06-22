from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from academic_engine.dissertation_artifacts import build_bundle, write_bundle


def _metadata_text(author_abstract: str) -> str:
    return dedent(
        f"""
        title = "Кандидатская диссертация"
        university = "U"
        year = 2026
        city = "City"

        [program]
        code = "5.1.3"
        name = "Юридические науки"

        [author]
        full_name = "Иванова А. П."

        [supervisor]
        full_name = "Петров П. П."

        [dissertation]
        degree = "кандидат юридических наук"
        specialty_code = "5.1.3"
        specialty_name = "Частно-правовые науки"
        novelty_summary = "Новизна состоит в развитии модели правовой защиты данных."
        contribution_summary = "Вклад автора состоит в новой доктринальной связке между режимами данных."
        methodology_summary = "Методология включает формально-юридический и сравнительный анализ."

        [author_abstract]
        ru = "{author_abstract}"

        [defense]
        council = "Д 999.999.99"
        leading_organization = "Юридический институт"
        date = "2026-06-01"
        """
    )


class DissertationArtifactTests(unittest.TestCase):
    def test_missing_metadata_is_a_blocker(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = build_bundle(Path(tmp) / "missing.toml")
            self.assertTrue(bundle.has_blockers)
            codes = {issue.code for issue in bundle.issues}
            self.assertIn("metadata-missing", codes)

    def test_bundle_renders_author_abstract_and_checklist(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = root / "metadata.toml"
            metadata.write_text(_metadata_text("А" * 450), encoding="utf-8")
            bundle = build_bundle(metadata)
            self.assertFalse(bundle.has_blockers)
            written = write_bundle(bundle, destination=root / "artifacts")
            names = {path.name for path in written}
            self.assertIn("author-abstract.md", names)
            self.assertIn("defense-checklist.md", names)


if __name__ == "__main__":
    unittest.main()
