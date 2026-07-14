from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from academic_engine import work_cli as work_cli_module
from academic_engine.executors import ProviderExecutionError


class QualificationWorkCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_qualification_cli_forwards_bounded_arguments_and_prints_safe_summary(self) -> None:
        workflow = SimpleNamespace(
            workflow_id="qualification-1",
            execution_status="succeeded",
            promotion=SimpleNamespace(status="skipped"),
            metadata={"canonical_unchanged": True},
        )
        stdout = StringIO()
        stderr = StringIO()
        seed = "works/openrouter-live-smoke/articles/briefs/academic-intake-qualification.md"

        with patch.object(work_cli_module, "run_openrouter_role_qualification", return_value=workflow) as runner:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "qualify-openrouter-role",
                        "academic-intake",
                        "--work",
                        "openrouter-live-smoke",
                        "--seed",
                        seed,
                        "--no-search",
                        "--model",
                        "operator-selected-model",
                    ],
                    root_dir=self.root,
                )

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        runner.assert_called_once_with(
            self.root.resolve(),
            "academic-intake",
            "openrouter-live-smoke",
            seed,
            use_search=False,
            model="operator-selected-model",
        )
        self.assertEqual(
            stdout.getvalue(),
            "Workflow ID: qualification-1\n"
            "Execution status: succeeded\n"
            "Promotion status: skipped\n"
            "Canonical fixture unchanged: true\n",
        )
        self.assertNotIn(seed, stdout.getvalue())
        self.assertNotIn("operator-selected-model", stdout.getvalue())

    def test_qualification_cli_reports_forbidden_route_without_provider_output(self) -> None:
        stdout = StringIO()
        stderr = StringIO()

        with patch.object(
            work_cli_module,
            "run_openrouter_role_qualification",
            side_effect=ProviderExecutionError("provider-route-forbidden", "candidate is not enabled"),
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "qualify-openrouter-role",
                        "not-a-qualified-role",
                        "--work",
                        "openrouter-live-smoke",
                        "--seed",
                        "works/openrouter-live-smoke/articles/briefs/academic-intake-qualification.md",
                    ],
                    root_dir=self.root,
                )

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("provider-route-forbidden", stderr.getvalue())
        self.assertNotIn("provider output", stderr.getvalue())

    def test_source_acquirer_qualification_cli_forwards_fixed_target_without_printing_it(self) -> None:
        workflow = SimpleNamespace(
            workflow_id="qualification-source-1",
            execution_status="succeeded",
            promotion=SimpleNamespace(status="skipped"),
            metadata={"canonical_unchanged": True},
        )
        stdout = StringIO()
        stderr = StringIO()
        seed = "works/openrouter-live-smoke/articles/briefs/academic-source-acquirer-qualification.md"
        target = "works/openrouter-live-smoke/articles/evidence/academic-source-acquirer-qualification.md"

        with patch.object(work_cli_module, "run_openrouter_role_qualification", return_value=workflow) as runner:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "qualify-openrouter-role",
                        "academic-source-acquirer",
                        "--work",
                        "openrouter-live-smoke",
                        "--seed",
                        seed,
                        "--target",
                        target,
                        "--no-search",
                    ],
                    root_dir=self.root,
                )

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        runner.assert_called_once_with(
            self.root.resolve(),
            "academic-source-acquirer",
            "openrouter-live-smoke",
            seed,
            use_search=False,
            model=None,
            target_path=target,
        )
        self.assertNotIn(seed, stdout.getvalue())
        self.assertNotIn(target, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
