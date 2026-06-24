"""Tests for shared DOCX preview formatting helper."""

from __future__ import annotations

import subprocess
import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from unittest.mock import patch

from academic_engine import docx_preview


def _write_demo_workspace(root: Path) -> None:
    (root / "works" / "demo-work").mkdir(parents=True)
    (root / "output" / "docx" / "demo-work").mkdir(parents=True)
    (root / "workspace.toml").write_text(
        dedent(
            """\
            default_work = "demo-work"
            supported_lanes = ["thesis"]

            [default_profiles]
            thesis = "ru-vkr-university-default"

            [outputs]
            runs_dir = "output/runs"
            docx_dir = "output/docx"

            [works]
            demo-work = "works/demo-work"
            """
        ),
        encoding="utf-8",
    )
    (root / "works" / "demo-work" / "work-canon.md").write_text("# Canon\n", encoding="utf-8")
    (root / "works" / "demo-work" / "work.toml").write_text(
        dedent(
            """\
            version = 1
            slug = "demo-work"
            title = "Demo Thesis"
            topic = "Demo topic"
            artifact_type = "vkr"
            language = "ru"
            active_lanes = ["thesis"]
            work_canon = "work-canon.md"

            [standards]
            thesis_profile = "ru-vkr-university-default"

            [thesis]
            root_dir = "thesis"
            chapters_dir = "thesis/chapters"
            sources_dir = "thesis/sources"
            manuscript_dir = "thesis/manuscript"
            manuscript_sections_dir = "thesis/manuscript/sections"
            reviews_dir = "thesis/reviews"
            sync_dir = "thesis/sync"
            full_draft_path = "thesis/manuscript/full-draft.md"
            docx_filename = "demo-work.docx"
            section_order = ["thesis/manuscript/sections/00-title.md"]

            [thesis.docx_preview]
            enabled = true
            subject = "Demo subject"
            author = "Demo Author"
            keywords = "demo; preview"
            major_titles = ["Введение", "Заключение"]
            format_contents_table = true
            title_center_until = 14
            title_right_align_indices = [8, 9, 10, 11]
            title_spacing = [
              { index = 0, before_pt = 0, after_pt = 10 },
              { index = 12, before_pt = 0, after_pt = 105 },
            ]
            """
        ),
        encoding="utf-8",
    )


class DocxPreviewConfigTests(unittest.TestCase):
    def test_loads_docx_preview_config_from_work_toml(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_demo_workspace(root)

            config = docx_preview.load_docx_preview_config(root, "demo-work")

        self.assertEqual(config.title, "Demo Thesis")
        self.assertEqual(config.subject, "Demo subject")
        self.assertEqual(config.author, "Demo Author")
        self.assertEqual(config.keywords, "demo; preview")
        self.assertEqual(config.major_titles, ("Введение", "Заключение"))
        self.assertTrue(config.format_contents_table)
        self.assertEqual(config.title_center_until, 14)
        self.assertEqual(config.title_right_align_indices, (8, 9, 10, 11))
        self.assertEqual(config.title_spacing, {0: (0, 10), 12: (0, 105)})

    def test_cli_reports_missing_python_docx_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_demo_workspace(root)
            input_docx = root / "input.docx"
            input_docx.write_bytes(b"not a real docx")
            stdout = StringIO()
            stderr = StringIO()

            with patch(
                "academic_engine.docx_preview._load_docx_dependencies",
                side_effect=docx_preview.DocxPreviewDependencyError("python-docx is not installed"),
            ):
                code = docx_preview.main(
                    ["--work", "demo-work", "--input", str(input_docx)],
                    root_dir=root,
                    stdout=stdout,
                    stderr=stderr,
                )

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("python-docx is not installed", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_script_wrapper_avoids_academic_engine_import_shadowing(self) -> None:
        root = Path(__file__).resolve().parents[1]

        with TemporaryDirectory() as tmp:
            workspace_root = Path(tmp)
            _write_demo_workspace(workspace_root)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "render_docx_preview.py"),
                    "--work",
                    "demo-work",
                    "--input",
                    "missing.docx",
                ],
                cwd=workspace_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("Input DOCX not found", proc.stderr)
        self.assertNotIn("No module named", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
