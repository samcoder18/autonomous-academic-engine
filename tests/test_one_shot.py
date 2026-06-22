"""Tests for telegram_console.one_shot."""

from __future__ import annotations

import json
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
from telegram_console.orchestrator_exports import require_machine_gates_passed
from telegram_console.orchestrator_support import WorkflowError
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


def _empty_corpus(path: Path) -> Path:
    OriginalityCorpus().save(path)
    return path


def _write_dissertation_metadata(path: Path) -> None:
    author_abstract = "А" * 450
    path.write_text(
        dedent(
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
            novelty_summary = "Новизна состоит в уточнении доктринальной модели защиты персональных данных."
            contribution_summary = "Вклад автора состоит в построении новой нормативной связки между режимами данных."
            methodology_summary = "Методология включает формально-юридический, сравнительный и доктринальный анализ."

            [author_abstract]
            ru = "{author_abstract}"

            [defense]
            council = "Д 999.999.99"
            leading_organization = "Юридический институт"
            date = "2026-06-01"
            """
        ),
        encoding="utf-8",
    )


def _candidate_manuscript(*, chapter_repetitions: int) -> str:
    paragraph = (
        "Авторский анализ показывает пределы вывода, связь с доктриной "
        "и необходимость адресовать сильный контраргумент. "
    )
    chapter_body = paragraph * chapter_repetitions
    bibliography = "\n".join(
        f"{index}. Источник {index} / Автор И. И. — Москва: Норма, 20{(index % 20) + 5}. — {120 + index} с."
        for index in range(1, 126)
    )
    return "\n".join(
        [
            "# Заголовок",
            "",
            "## Введение",
            "",
            chapter_body,
            "",
            "## Глава 1",
            "",
            chapter_body,
            "",
            "## Глава 2",
            "",
            chapter_body,
            "",
            "## Глава 3",
            "",
            chapter_body,
            "",
            "## Заключение",
            "",
            chapter_body,
            "",
            "## Список использованных источников",
            "",
            bibliography,
            "",
        ]
    )


def _write_candidate_dissertation_contour(root: Path, *, with_historiography: bool = True) -> None:
    (root / "maps").mkdir(parents=True, exist_ok=True)
    (root / "reviews").mkdir(parents=True, exist_ok=True)
    (root / "publications").mkdir(parents=True, exist_ok=True)
    if with_historiography:
        (root / "maps" / "historiography-map.md").write_text(
            dedent(
                """\
                # Historiography Map

                Поле исследования раскрывает школы, спор и неразрешенные вопросы в пределах цифровой идентификации.
                Дополнительно фиксируется, какие школы доминируют и где остается неразрешенный doctrinal gap.
                """
            ),
            encoding="utf-8",
        )
    (root / "maps" / "novelty-contribution-map.md").write_text(
        dedent(
            """\
            # Novelty and Contribution Map

            Новизна формулируется через уточнение модели защиты данных.
            Вклад автора заключается в новой связке doctrinal reasoning и практики.
            Ограничение вывода связано с текущим состоянием законодательства.
            """
        ),
        encoding="utf-8",
    )
    (root / "maps" / "dissertation-claim-map.md").write_text(
        dedent(
            """\
            # Dissertation Claim Map

            Claim coverage shows how each claim is supported.
            A counterargument is attached to the main claim and limits are preserved explicitly.
            """
        ),
        encoding="utf-8",
    )
    (root / "reviews" / "dissertation-review.md").write_text(
        dedent(
            """\
            # Dissertation Review

            Новизна проверена отдельно от обзора.
            Вклад автора описан явно, а методология соотнесена с выводами.
            Ограничения вывода сохранены и не скрыты за стилистическим сглаживанием.
            """
        ),
        encoding="utf-8",
    )
    (root / "reviews" / "counterargument-review.md").write_text(
        dedent(
            """\
            # Counterargument Review

            Сильная позиция конкурирующей школы описана как позиция.
            Ответ автора сохранен в узкой и добросовестной формулировке.
            """
        ),
        encoding="utf-8",
    )
    (root / "publications" / "publication-evidence.md").write_text(
        dedent(
            """\
            # Publication Evidence

            Статус публикации зафиксирован.
            Выходные данные заполнены.
            Связь с диссертацией показана на уровне ключевых положений.
            """
        ),
        encoding="utf-8",
    )
    (root / "publications" / "publication-claim-matrix.md").write_text(
        dedent(
            """\
            # Publication Claim Matrix

            | Тезис | Глава | Публикация | Статус покрытия |
            | --- | --- | --- | --- |
            | Тезис 1 | Глава 1 | Публикация 1 | Полное покрытие |
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
    def test_happy_path_reports_machine_gates_passed(self) -> None:
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
                corpus_path=_empty_corpus(root / "corpus.json"),
            )
            report = run_one_shot(config)
            self.assertEqual(report.status, "machine-gates-passed")
            self.assertTrue(all(g.passed for g in report.gates))
            self.assertTrue((frontmatter_dir / "title-page.md").exists())

    def test_report_dict_contains_v2_version(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_GOOD_MANUSCRIPT, encoding="utf-8")
            docx = _minimal_docx(root / "thesis.docx")
            metadata = root / "metadata.toml"
            _write_metadata(metadata)
            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=docx,
                    metadata_path=metadata,
                    frontmatter_destination=root / "frontmatter",
                    corpus_path=_empty_corpus(root / "corpus.json"),
                )
            )

        self.assertEqual(report.to_dict().get("version"), "one-shot-report/v2")

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
            self.assertEqual(report.status, "blocked")
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
            self.assertEqual(report.status, "blocked")

    def test_missing_corpus_blocks_instead_of_skipping_gate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_GOOD_MANUSCRIPT, encoding="utf-8")

            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                )
            )

        self.assertEqual(report.status, "blocked")
        originality = next(gate for gate in report.gates if gate.name == "originality")
        self.assertFalse(originality.passed)
        self.assertEqual(originality.blockers[0].code, "originality-corpus-required")

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
            self.assertIn("One-shot thesis report", md_path.read_text(encoding="utf-8"))

    def test_legacy_submission_ready_report_does_not_unlock_export(self) -> None:
        with TemporaryDirectory() as tmp:
            reviews_dir = Path(tmp)
            (reviews_dir / "2026-06-14-one-shot-report.json").write_text(
                json.dumps(
                    {
                        "status": "submission-ready",
                        "finished_at": "2026-06-14T00:00:00+00:00",
                        "gates": [{"gate": "originality", "passed": True}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(WorkflowError, "machine gates"):
                require_machine_gates_passed(reviews_dir)

    def test_legacy_machine_gate_report_without_v2_version_does_not_unlock_export(self) -> None:
        with TemporaryDirectory() as tmp:
            reviews_dir = Path(tmp)
            (reviews_dir / "2026-06-14-one-shot-report.json").write_text(
                json.dumps(
                    {
                        "status": "machine-gates-passed",
                        "finished_at": "2026-06-14T00:00:00+00:00",
                        "gates": [{"gate": "originality", "passed": True}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(WorkflowError, "machine gates"):
                require_machine_gates_passed(reviews_dir)

    def test_managed_thesis_bundle_blocks_incomplete_quality_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            thesis_root = root / "thesis"
            manuscript = thesis_root / "manuscript" / "full-draft.md"
            manuscript.parent.mkdir(parents=True, exist_ok=True)
            manuscript.write_text(_GOOD_MANUSCRIPT, encoding="utf-8")
            (thesis_root / "ledgers").mkdir(parents=True, exist_ok=True)
            ledger_header = (
                "| claim_id | section_target | claim_text | basis_type | source_package_item_ids | "
                "primary_identifier | official_primary_link | jurisdiction | statement_precision | "
                "knowledge_date | verification_result | verification_status | support_scope | "
                "draft_use | false_attribution_check | notes |"
            )
            ledger_separator = (
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
            )
            ledger_row = (
                "| CL-001 | thesis/manuscript/sections/01.md | Demo claim | primary-normative | S1 | "
                "Art. 10 | https://example.test/act | RU | exact | 2026-04-19 | "
                "supported in official text | verified | direct | safe | passed | ok |"
            )
            (thesis_root / "ledgers" / "01-ledger.md").write_text(
                dedent(
                    f"""\
                    # Ledger

                    {ledger_header}
                    {ledger_separator}
                    {ledger_row}
                    """
                ),
                encoding="utf-8",
            )

            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                )
            )

        self.assertEqual(report.status, "blocked")
        gate = next(item for item in report.gates if item.name == "thesis-quality-contract")
        codes = {blocker.code for blocker in gate.blockers}
        self.assertFalse(gate.passed)
        self.assertIn("thesis-verification-log-missing", codes)
        self.assertIn("thesis-review-artifact-missing", codes)
        self.assertIn("thesis-claim-passport-incomplete", codes)

    def test_managed_thesis_bundle_passes_strict_quality_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            thesis_root = root / "thesis"
            manuscript = thesis_root / "manuscript" / "full-draft.md"
            manuscript.parent.mkdir(parents=True, exist_ok=True)
            manuscript.write_text(_GOOD_MANUSCRIPT, encoding="utf-8")
            ledgers_dir = thesis_root / "ledgers"
            reviews_dir = thesis_root / "reviews"
            ledgers_dir.mkdir(parents=True, exist_ok=True)
            reviews_dir.mkdir(parents=True, exist_ok=True)
            strict_ledger_header = (
                "| claim_id | section_target | claim_text | basis_type | source_package_item_ids | "
                "primary_identifier | official_primary_link | jurisdiction | statement_precision | "
                "knowledge_date | verification_result | verification_status | support_scope | "
                "pinpoint_locator | support_excerpt | caveat_note | draft_use | "
                "false_attribution_check | notes |"
            )
            strict_ledger_separator = (
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
                "--- | --- | --- | --- |"
            )
            strict_ledger_row = (
                "| CL-001 | thesis/manuscript/sections/01.md | Demo claim | primary-normative | S1 | "
                "Art. 10 | https://example.test/act | RU | exact | 2026-04-19 | supported in official text | "
                "verified | direct | Art. 10 para. 1 | Direct support from the statute. | none | safe | "
                "passed | ok |"
            )
            (ledgers_dir / "01-ledger.md").write_text(
                dedent(
                    f"""\
                    # Ledger

                    {strict_ledger_header}
                    {strict_ledger_separator}
                    {strict_ledger_row}
                    """
                ),
                encoding="utf-8",
            )
            verification_header = (
                "| claim_id | primary_identifier | official_primary_link | jurisdiction | statement_precision | "
                "knowledge_date | verification_result | verification_status | false_attribution_check | "
                "pinpoint_locator | support_excerpt | caveat_note | notes |"
            )
            verification_separator = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
            verification_row = (
                "| CL-001 | Art. 10 | https://example.test/act | RU | exact | 2026-04-19 | "
                "supported in official text | verified | passed | Art. 10 para. 1 | "
                "Direct support from the statute. | none | ok |"
            )
            (ledgers_dir / "01-verification-log.md").write_text(
                dedent(
                    f"""\
                    # Verification log

                    {verification_header}
                    {verification_separator}
                    {verification_row}
                    """
                ),
                encoding="utf-8",
            )
            (reviews_dir / "01-review.md").write_text(
                dedent(
                    """\
                    # Review

                    - Есть ли утверждения без опоры: нет
                    - Есть ли спорные выводы: нет
                    - Все ли динамичные нормы и решения перепроверены на дату написания: да
                    - Что нужно дополнить источниками: нет
                    - Единообразно ли оформлены ссылки: да
                    - Не маскируется ли пересказ под анализ: нет
                    - Достаточно ли данных для выводов: да
                    - Нет ли рискованных близких перефразирований: нет
                    - Отделена ли авторская позиция от обзора литературы: да
                    - Есть ли ограничения выводов там, где они нужны: да
                    """
                ),
                encoding="utf-8",
            )

            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                )
            )

        gate = next(item for item in report.gates if item.name == "thesis-quality-contract")
        self.assertTrue(gate.passed)


class CandidateDissertationOneShotTests(unittest.TestCase):
    def test_missing_historiography_map_is_blocker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_candidate_manuscript(chapter_repetitions=700), encoding="utf-8")
            metadata = root / "dissertation-metadata.toml"
            _write_dissertation_metadata(metadata)
            dissertation_root = root / "dissertation"
            _write_candidate_dissertation_contour(dissertation_root, with_historiography=False)
            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                    dissertation_metadata_path=metadata,
                    dissertation_artifacts_destination=dissertation_root / "artifacts",
                    dissertation_root=dissertation_root,
                    work_type="dissertation-candidate",
                )
            )
            self.assertEqual(report.status, "blocked")
            codes = {b.code for b in report.all_blockers}
            self.assertIn("artifact-missing", codes)
            gate_names = {gate.name for gate in report.gates if not gate.passed}
            self.assertIn("historiography-coverage", gate_names)

    def test_missing_publication_claim_matrix_is_blocker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_candidate_manuscript(chapter_repetitions=700), encoding="utf-8")
            metadata = root / "dissertation-metadata.toml"
            _write_dissertation_metadata(metadata)
            dissertation_root = root / "dissertation"
            _write_candidate_dissertation_contour(dissertation_root)
            (dissertation_root / "publications" / "publication-claim-matrix.md").unlink()
            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                    dissertation_metadata_path=metadata,
                    dissertation_artifacts_destination=dissertation_root / "artifacts",
                    dissertation_root=dissertation_root,
                    work_type="dissertation-candidate",
                )
            )
            self.assertEqual(report.status, "blocked")
            gate_names = {gate.name for gate in report.gates if not gate.passed}
            self.assertIn("publication-claim-coverage", gate_names)

    def test_length_underflow_blocks_submission(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_candidate_manuscript(chapter_repetitions=40), encoding="utf-8")
            metadata = root / "dissertation-metadata.toml"
            _write_dissertation_metadata(metadata)
            dissertation_root = root / "dissertation"
            _write_candidate_dissertation_contour(dissertation_root)
            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                    dissertation_metadata_path=metadata,
                    dissertation_artifacts_destination=dissertation_root / "artifacts",
                    dissertation_root=dissertation_root,
                    work_type="dissertation-candidate",
                )
            )
            self.assertEqual(report.status, "blocked")
            categories = {b.category for b in report.all_blockers}
            self.assertIn("length-conformance", categories)

    def test_missing_author_abstract_blocks_submission(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_candidate_manuscript(chapter_repetitions=700), encoding="utf-8")
            metadata = root / "dissertation-metadata.toml"
            _write_dissertation_metadata(metadata)
            metadata.write_text(metadata.read_text(encoding="utf-8").replace("А" * 450, "А" * 100), encoding="utf-8")
            dissertation_root = root / "dissertation"
            _write_candidate_dissertation_contour(dissertation_root)
            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                    dissertation_metadata_path=metadata,
                    dissertation_artifacts_destination=dissertation_root / "artifacts",
                    dissertation_root=dissertation_root,
                    work_type="dissertation-candidate",
                )
            )
            self.assertEqual(report.status, "blocked")
            gate_names = {gate.name for gate in report.gates if not gate.passed}
            self.assertIn("dissertation-artifacts", gate_names)

    def test_missing_publication_evidence_blocks_submission(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_candidate_manuscript(chapter_repetitions=700), encoding="utf-8")
            metadata = root / "dissertation-metadata.toml"
            _write_dissertation_metadata(metadata)
            dissertation_root = root / "dissertation"
            _write_candidate_dissertation_contour(dissertation_root)
            (dissertation_root / "publications" / "publication-evidence.md").unlink()
            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                    dissertation_metadata_path=metadata,
                    dissertation_artifacts_destination=dissertation_root / "artifacts",
                    dissertation_root=dissertation_root,
                    work_type="dissertation-candidate",
                )
            )
            self.assertEqual(report.status, "blocked")
            gate_names = {gate.name for gate in report.gates if not gate.passed}
            self.assertIn("publication-evidence", gate_names)

    def test_happy_path_generates_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "manuscript.md"
            manuscript.write_text(_candidate_manuscript(chapter_repetitions=700), encoding="utf-8")
            metadata = root / "dissertation-metadata.toml"
            _write_dissertation_metadata(metadata)
            dissertation_root = root / "dissertation"
            _write_candidate_dissertation_contour(dissertation_root)
            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                    dissertation_metadata_path=metadata,
                    dissertation_artifacts_destination=dissertation_root / "artifacts",
                    dissertation_root=dissertation_root,
                    corpus_path=_empty_corpus(root / "corpus.json"),
                    work_type="dissertation-candidate",
                )
            )
            self.assertEqual(report.status, "machine-gates-passed")
            self.assertTrue((dissertation_root / "artifacts" / "author-abstract.md").exists())
            self.assertTrue((dissertation_root / "artifacts" / "defense-checklist.md").exists())
            self.assertTrue((dissertation_root / "publications" / "publication-claim-matrix.md").exists())


if __name__ == "__main__":
    unittest.main()
