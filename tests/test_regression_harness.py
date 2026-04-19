"""End-to-end regression harness for the autonomous VKR pipeline.

Builds a minimal fake workspace in a temporary directory, runs the key
CLI entrypoints (``build-vkr-frontmatter``, ``one-shot-thesis``) and
checks:

- the happy path produces ``submission-ready``;
- deliberately introduced blockers downgrade the status;
- rerunning the pipeline on the same workspace is idempotent (same
  gates, same blockers).

Pandoc and Codex are **not** invoked. We construct the manuscript
manually so that this harness runs offline in CI.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from telegram_console.one_shot import OneShotConfig, run_one_shot

_METADATA_TOML = dedent(
    f"""
    title = "Защита биометрических данных"
    university = "СОГУ им. К. Л. Хетагурова"
    year = 2026
    city = "Владикавказ"

    [program]
    code = "40.03.01"
    name = "Юриспруденция"

    [author]
    full_name = "Иванова А. П."

    [supervisor]
    full_name = "Петров П. П."

    [abstract]
    ru = "{"А" * 220}"
    en = "{"A" * 220}"

    [keywords]
    ru = ["a", "b", "c"]
    en = ["a", "b", "c"]
    """
)


_CLEAN_MANUSCRIPT = dedent(
    """\
    # Глава 1

    Тело главы.

    ## Список использованных источников

    1. Биометрия в России / Иванов И. И. — Москва: Норма, 2024. — 240 с.
    2. О единой биометрической системе: Федеральный закон от 29.12.2022 № 572-ФЗ.
    """
)


_BROKEN_MANUSCRIPT = dedent(
    """\
    ## Список использованных источников

    1. Too short.
    2. Другой источник / Автор А. — Москва: Норма, 2024. — 100 с
    """
)


class EndToEndRegressionTests(unittest.TestCase):
    def _setup_work(self, root: Path, *, manuscript: str) -> OneShotConfig:
        thesis_root = root / "thesis"
        thesis_root.mkdir(parents=True, exist_ok=True)
        manuscript_path = thesis_root / "full-draft.md"
        manuscript_path.write_text(manuscript, encoding="utf-8")
        metadata_path = thesis_root / "metadata.toml"
        metadata_path.write_text(_METADATA_TOML, encoding="utf-8")
        frontmatter = thesis_root / "frontmatter"
        return OneShotConfig(
            manuscript_md=manuscript_path,
            docx_path=None,
            metadata_path=metadata_path,
            frontmatter_destination=frontmatter,
        )

    def test_happy_path_is_submission_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            config = self._setup_work(Path(tmp), manuscript=_CLEAN_MANUSCRIPT)
            report = run_one_shot(config)
            self.assertEqual(report.status, "submission-ready")

    def test_broken_manuscript_is_downgraded(self) -> None:
        with TemporaryDirectory() as tmp:
            config = self._setup_work(Path(tmp), manuscript=_BROKEN_MANUSCRIPT)
            report = run_one_shot(config)
            self.assertEqual(report.status, "strong-draft-with-blockers")
            categories = {b.category for b in report.all_blockers}
            self.assertIn("gost-bibliography", categories)

    def test_pipeline_is_idempotent_on_gates(self) -> None:
        """Rerunning with the same inputs must produce the same gate verdicts."""
        with TemporaryDirectory() as tmp:
            config = self._setup_work(Path(tmp), manuscript=_CLEAN_MANUSCRIPT)
            first = run_one_shot(config)
            second = run_one_shot(config)
            self.assertEqual(first.status, second.status)
            self.assertEqual(
                [g.name for g in first.gates],
                [g.name for g in second.gates],
            )
            self.assertEqual(
                [g.passed for g in first.gates],
                [g.passed for g in second.gates],
            )

    def test_frontmatter_is_regenerated_on_rerun(self) -> None:
        """Rerunning must overwrite frontmatter (so fixes propagate)."""
        with TemporaryDirectory() as tmp:
            config = self._setup_work(Path(tmp), manuscript=_CLEAN_MANUSCRIPT)
            run_one_shot(config)
            title_path = config.frontmatter_destination / "title-page.md"
            self.assertTrue(title_path.exists())
            title_path.write_text("mutated", encoding="utf-8")
            run_one_shot(config)
            self.assertNotEqual(title_path.read_text(encoding="utf-8"), "mutated")

    def test_missing_metadata_does_not_crash_pipeline(self) -> None:
        """When metadata is intentionally omitted, the other gates still run."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_CLEAN_MANUSCRIPT, encoding="utf-8")
            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                )
            )
            self.assertEqual(report.status, "submission-ready")
            gate_names = [gate.name for gate in report.gates]
            self.assertNotIn("vkr-frontmatter", gate_names)


class RepairBudgetInvariantsTests(unittest.TestCase):
    """Repair-kernel budget is tested elsewhere; here we assert that the
    one-shot pipeline does not loop: each call produces a single report."""

    def test_single_call_produces_single_report(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "m.md"
            manuscript.write_text(_BROKEN_MANUSCRIPT, encoding="utf-8")
            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                )
            )
            self.assertEqual(len(report.gates), 1)


if __name__ == "__main__":
    unittest.main()
