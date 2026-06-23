from __future__ import annotations

import tempfile
import tomllib
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from academic_engine import work_cli
from academic_engine.work_bootstrap import (
    DEFAULT_ARTICLE_PROFILE,
    DEFAULT_DISSERTATION_CANDIDATE_PROFILE,
    DEFAULT_DISSERTATION_DOCTOR_PROFILE,
    DEFAULT_THESIS_PROFILE,
    WorkBootstrapError,
    WorkBootstrapRequest,
    bootstrap_work,
    register_work_in_workspace_toml,
    render_work_toml,
    validate_slug,
)

MINIMAL_WORKSPACE_TOML = """\
version = 1
default_work = "starter-work"
supported_lanes = ["thesis", "article"]

[default_profiles]
thesis = "thesis-v1"
article = "ru-law-article-v1"

[outputs]
runs_dir = "output/runs"
docx_dir = "output/docx"

[works]
starter-work = "works/starter-work"
"""


def _prepare_workspace(tmp: Path) -> Path:
    (tmp / "workspace.toml").write_text(MINIMAL_WORKSPACE_TOML, encoding="utf-8")
    starter_dir = tmp / "works" / "starter-work"
    starter_dir.mkdir(parents=True, exist_ok=True)
    # Place a marker so the existing starter-work directory is non-empty.
    (starter_dir / "placeholder.txt").write_text("placeholder", encoding="utf-8")
    return tmp


class ValidateSlugTests(unittest.TestCase):
    def test_valid_slugs(self) -> None:
        for slug in ("article", "smart-contracts", "vkr-2026", "a1b2-c3"):
            validate_slug(slug)

    def test_rejects_invalid_slugs(self) -> None:
        for slug in ("", "Upper", "-leading", "trailing-", "double--hyphen", "has space", "кириллица"):
            with self.subTest(slug=slug), self.assertRaises(WorkBootstrapError):
                validate_slug(slug)


class RenderWorkTomlTests(unittest.TestCase):
    def test_article_only_lanes(self) -> None:
        request = WorkBootstrapRequest(
            slug="smart-contracts-article",
            title="Правовая природа смарт-контрактов",
            topic="Смарт-контракты в ГК РФ",
            artifact_type="article",
        )
        text = render_work_toml(request)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["slug"], "smart-contracts-article")
        self.assertEqual(parsed["artifact_type"], "article")
        self.assertEqual(parsed["active_lanes"], ["article"])
        self.assertIn("article", parsed)
        self.assertNotIn("thesis", parsed)
        self.assertEqual(parsed["standards"]["article_profile"], DEFAULT_ARTICLE_PROFILE)

    def test_vkr_defaults_to_thesis_lane(self) -> None:
        request = WorkBootstrapRequest(
            slug="my-vkr-2026",
            title="ВКР по праву",
            topic="Тема ВКР",
            artifact_type="vkr-bachelor",
        )
        text = render_work_toml(request)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["active_lanes"], ["thesis"])
        self.assertIn("thesis", parsed)
        self.assertNotIn("article", parsed)
        self.assertEqual(parsed["standards"]["thesis_profile"], DEFAULT_THESIS_PROFILE)
        section_order = parsed["thesis"]["section_order"]
        self.assertTrue(any("01-introduction.md" in path for path in section_order))
        self.assertTrue(any("06-bibliography.md" in path for path in section_order))
        self.assertEqual(parsed["thesis"]["docx_filename"], "my-vkr-2026.docx")

    def test_dissertation_artifact_with_dual_lanes(self) -> None:
        request = WorkBootstrapRequest(
            slug="phd-law-2027",
            title="Кандидатская диссертация",
            topic="Правовое регулирование",
            artifact_type="dissertation-candidate",
            lanes=("thesis", "article"),
        )
        text = render_work_toml(request)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["active_lanes"], ["thesis", "article"])
        self.assertIn("thesis", parsed)
        self.assertIn("article", parsed)
        self.assertEqual(parsed["standards"]["thesis_profile"], DEFAULT_DISSERTATION_CANDIDATE_PROFILE)
        self.assertIn("article_profile", parsed["standards"])

    def test_doctor_dissertation_uses_doctor_profile_and_fourth_chapter(self) -> None:
        request = WorkBootstrapRequest(
            slug="doctor-law-2027",
            title="Докторская диссертация",
            topic="Правовое регулирование",
            artifact_type="dissertation-doctor",
        )
        text = render_work_toml(request)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["standards"]["thesis_profile"], DEFAULT_DISSERTATION_DOCTOR_PROFILE)
        section_order = parsed["thesis"]["section_order"]
        self.assertTrue(any("05-chapter-4.md" in path for path in section_order))
        self.assertTrue(any("07-bibliography.md" in path for path in section_order))

    def test_rejects_unknown_artifact_type(self) -> None:
        request = WorkBootstrapRequest(
            slug="x",
            title="x",
            topic="x",
            artifact_type="monograph",
        )
        with self.assertRaises(WorkBootstrapError):
            render_work_toml(request)

    def test_rejects_unknown_lane(self) -> None:
        request = WorkBootstrapRequest(
            slug="x",
            title="x",
            topic="x",
            artifact_type="article",
            lanes=("podcast",),
        )
        with self.assertRaises(WorkBootstrapError):
            render_work_toml(request)

    def test_title_with_quotes_is_escaped(self) -> None:
        request = WorkBootstrapRequest(
            slug="escape-test",
            title='Title with "quotes"',
            topic="Topic with \\ backslash",
            artifact_type="article",
        )
        text = render_work_toml(request)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["title"], 'Title with "quotes"')
        self.assertEqual(parsed["topic"], "Topic with \\ backslash")


class RegisterWorkInWorkspaceTomlTests(unittest.TestCase):
    def test_adds_entry_under_works_section(self) -> None:
        updated = register_work_in_workspace_toml(
            MINIMAL_WORKSPACE_TOML,
            slug="smart-contracts-article",
            rel_path="works/smart-contracts-article",
            set_default=False,
        )
        self.assertIn('smart-contracts-article = "works/smart-contracts-article"', updated)
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["default_work"], "starter-work")
        self.assertIn("smart-contracts-article", parsed["works"])

    def test_set_default_replaces_default_work(self) -> None:
        updated = register_work_in_workspace_toml(
            MINIMAL_WORKSPACE_TOML,
            slug="new-vkr",
            rel_path="works/new-vkr",
            set_default=True,
        )
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["default_work"], "new-vkr")
        self.assertIn("new-vkr", parsed["works"])

    def test_idempotent_when_same_path(self) -> None:
        once = register_work_in_workspace_toml(
            MINIMAL_WORKSPACE_TOML,
            slug="new-vkr",
            rel_path="works/new-vkr",
            set_default=False,
        )
        twice = register_work_in_workspace_toml(
            once,
            slug="new-vkr",
            rel_path="works/new-vkr",
            set_default=False,
        )
        self.assertEqual(once, twice)

    def test_conflict_when_existing_path_differs(self) -> None:
        once = register_work_in_workspace_toml(
            MINIMAL_WORKSPACE_TOML,
            slug="clash",
            rel_path="works/clash",
            set_default=False,
        )
        with self.assertRaises(WorkBootstrapError):
            register_work_in_workspace_toml(
                once,
                slug="clash",
                rel_path="works/other-path",
                set_default=False,
            )

    def test_fails_when_no_works_section(self) -> None:
        with self.assertRaises(WorkBootstrapError):
            register_work_in_workspace_toml(
                'version = 1\ndefault_work = "x"\n',
                slug="new",
                rel_path="works/new",
                set_default=False,
            )


class BootstrapWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = _prepare_workspace(Path(self._tempdir.name))

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_creates_article_work_end_to_end(self) -> None:
        request = WorkBootstrapRequest(
            slug="smart-contracts-article",
            title="Правовая природа смарт-контрактов",
            topic="Смарт-контракты в ГК РФ",
            artifact_type="article",
        )
        result = bootstrap_work(self.root, request)
        self.assertTrue(result.work_toml.exists())
        self.assertTrue(result.work_canon.exists())
        self.assertTrue((result.work_dir / "articles" / "briefs").is_dir())
        self.assertTrue((result.work_dir / "articles" / "drafts").is_dir())
        self.assertFalse((result.work_dir / "thesis").exists())

        workspace_text = (self.root / "workspace.toml").read_text(encoding="utf-8")
        parsed_workspace = tomllib.loads(workspace_text)
        self.assertIn("smart-contracts-article", parsed_workspace["works"])
        self.assertEqual(parsed_workspace["default_work"], "starter-work")

        work_toml = tomllib.loads(result.work_toml.read_text(encoding="utf-8"))
        self.assertEqual(work_toml["slug"], "smart-contracts-article")
        self.assertEqual(work_toml["active_lanes"], ["article"])

    def test_creates_vkr_work_with_section_placeholders(self) -> None:
        request = WorkBootstrapRequest(
            slug="my-new-vkr",
            title="ВКР новая",
            topic="Тема ВКР",
            artifact_type="vkr-bachelor",
            set_default=True,
        )
        result = bootstrap_work(self.root, request)
        sections_dir = result.work_dir / "thesis" / "manuscript" / "sections"
        self.assertTrue(sections_dir.is_dir())
        self.assertTrue((sections_dir / "01-introduction.md").exists())
        self.assertTrue((sections_dir / "06-bibliography.md").exists())

        workspace_text = (self.root / "workspace.toml").read_text(encoding="utf-8")
        parsed_workspace = tomllib.loads(workspace_text)
        self.assertEqual(parsed_workspace["default_work"], "my-new-vkr")

    def test_creates_doctor_dissertation_specific_placeholders(self) -> None:
        request = WorkBootstrapRequest(
            slug="doctor-law-2027",
            title="Докторская диссертация",
            topic="Теория публичного права",
            artifact_type="dissertation-doctor",
        )
        result = bootstrap_work(self.root, request)
        sections_dir = result.work_dir / "thesis" / "manuscript" / "sections"
        self.assertTrue((sections_dir / "05-chapter-4.md").exists())
        self.assertTrue((sections_dir / "07-bibliography.md").exists())
        self.assertTrue((result.work_dir / "thesis" / "dissertation" / "metadata.toml").exists())
        self.assertTrue((result.work_dir / "thesis" / "dissertation" / "defense" / "leading-organization.md").exists())
        self.assertTrue((result.work_dir / "thesis" / "dissertation" / "defense" / "opponents.md").exists())

    def test_creates_candidate_publication_matrix_without_doctor_extras(self) -> None:
        request = WorkBootstrapRequest(
            slug="candidate-law-2027",
            title="Кандидатская диссертация",
            topic="Цифровая идентификация",
            artifact_type="dissertation-candidate",
        )
        result = bootstrap_work(self.root, request)
        self.assertTrue(
            (result.work_dir / "thesis" / "dissertation" / "publications" / "publication-claim-matrix.md").exists()
        )
        self.assertFalse((result.work_dir / "thesis" / "dissertation" / "defense" / "leading-organization.md").exists())
        self.assertFalse((result.work_dir / "thesis" / "dissertation" / "defense" / "opponents.md").exists())

    def test_refuses_when_target_directory_non_empty(self) -> None:
        conflict_dir = self.root / "works" / "already-here"
        conflict_dir.mkdir(parents=True)
        (conflict_dir / "x.txt").write_text("x", encoding="utf-8")

        with self.assertRaises(WorkBootstrapError):
            bootstrap_work(
                self.root,
                WorkBootstrapRequest(
                    slug="already-here",
                    title="t",
                    topic="t",
                    artifact_type="article",
                ),
            )

    def test_refuses_when_workspace_missing(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(WorkBootstrapError):
                bootstrap_work(
                    Path(empty),
                    WorkBootstrapRequest(
                        slug="x",
                        title="t",
                        topic="t",
                        artifact_type="article",
                    ),
                )


class WorkInitCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = _prepare_workspace(Path(self._tempdir.name))

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_cli_happy_path_prints_summary(self) -> None:
        with patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            rc = work_cli.main(
                [
                    "work",
                    "init",
                    "smart-contracts",
                    "--artifact-type",
                    "article",
                    "--title",
                    "Статья по смарт-контрактам",
                    "--topic",
                    "Смарт-контракты",
                ],
                root_dir=self.root,
            )
        self.assertEqual(rc, 0)
        stdout = fake_stdout.getvalue()
        self.assertIn("Created work `smart-contracts`", stdout)
        self.assertIn("default_work remains `starter-work`", stdout)

    def test_cli_json_output(self) -> None:
        with patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            rc = work_cli.main(
                [
                    "work",
                    "init",
                    "phd-law",
                    "--artifact-type",
                    "dissertation-candidate",
                    "--title",
                    "Кандидатская диссертация",
                    "--set-default",
                    "--json",
                ],
                root_dir=self.root,
            )
        self.assertEqual(rc, 0)
        import json as _json

        payload = _json.loads(fake_stdout.getvalue())
        self.assertEqual(payload["slug"], "phd-law")
        self.assertTrue(payload["set_default"])
        self.assertEqual(payload["default_work"], "phd-law")

    def test_cli_whitespace_topic_stays_empty_in_json_payload(self) -> None:
        with patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            rc = work_cli.main(
                [
                    "work",
                    "init",
                    "whitespace-topic",
                    "--artifact-type",
                    "article",
                    "--title",
                    "Fallback title",
                    "--topic",
                    "   ",
                    "--json",
                ],
                root_dir=self.root,
            )
        self.assertEqual(rc, 0)
        import json as _json

        payload = _json.loads(fake_stdout.getvalue())
        work_toml = tomllib.loads(Path(payload["work_toml"]).read_text(encoding="utf-8"))
        self.assertEqual(work_toml["topic"], "")

    def test_cli_empty_topic_defaults_to_title(self) -> None:
        with patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            rc = work_cli.main(
                [
                    "work",
                    "init",
                    "empty-topic",
                    "--artifact-type",
                    "article",
                    "--title",
                    "Fallback title",
                    "--topic",
                    "",
                    "--json",
                ],
                root_dir=self.root,
            )
        self.assertEqual(rc, 0)
        import json as _json

        payload = _json.loads(fake_stdout.getvalue())
        work_toml = tomllib.loads(Path(payload["work_toml"]).read_text(encoding="utf-8"))
        self.assertEqual(work_toml["topic"], "Fallback title")

    def test_cli_invalid_slug_returns_error_code(self) -> None:
        with patch("sys.stderr", new_callable=StringIO) as fake_stderr:
            rc = work_cli.main(
                [
                    "work",
                    "init",
                    "Not-Valid",
                    "--artifact-type",
                    "article",
                    "--title",
                    "x",
                ],
                root_dir=self.root,
            )
        self.assertEqual(rc, 2)
        self.assertIn("work init failed", fake_stderr.getvalue())


class OneShotCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = _prepare_workspace(Path(self._tempdir.name))

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_dissertation_one_shot_cli_returns_report_instead_of_crashing(self) -> None:
        bootstrap_work(
            self.root,
            WorkBootstrapRequest(
                slug="candidate-law-2027",
                title="Кандидатская диссертация",
                topic="Цифровая идентификация",
                artifact_type="dissertation-candidate",
            ),
        )

        with patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            rc = work_cli.main(
                [
                    "one-shot-dissertation",
                    "--work",
                    "candidate-law-2027",
                    "--skip-docx",
                ],
                root_dir=self.root,
            )

        self.assertEqual(rc, 1)
        stdout = fake_stdout.getvalue()
        self.assertIn("[one-shot] status: blocked", stdout)
        self.assertIn("one-shot-dissertation-report.md", stdout)
        report_paths = sorted(
            (self.root / "works" / "candidate-law-2027" / "thesis" / "reviews").glob(
                "*-one-shot-dissertation-report.md"
            )
        )
        self.assertEqual(len(report_paths), 1)


if __name__ == "__main__":
    unittest.main()
