from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class OpenRouterEvidenceReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.script = Path(__file__).resolve().parents[1] / "scripts" / "openrouter_evidence_report.py"
        self.workflow_id = "workflow-live-smoke"
        self.workflow_dir = self.root / "output" / "runs" / self.workflow_id
        self.workflow_dir.mkdir(parents=True)
        (self.workflow_dir / "roles").mkdir()
        self.stdout_log = self.root / "stdout.log"
        self.stderr_log = self.root / "stderr.log"
        self.stdout_log.write_text("Workflow ID: workflow-live-smoke\n", encoding="utf-8")
        self.stderr_log.write_text("", encoding="utf-8")
        self.report = self.root / "docs" / "deploy" / "evidence" / "report.md"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_workflow(self, *, roles: list[dict[str, object]]) -> None:
        payload = {
            "version": "workflow-run/v1",
            "workflow_id": self.workflow_id,
            "work_id": "openrouter-live-smoke",
            "lane": "article",
            "action": "repair",
            "status": "completed",
            "execution_status": "succeeded",
            "readiness_status": "strong-draft-with-blockers",
            "role_runs": roles,
            "blockers": [],
        }
        (self.workflow_dir / "workflow.json").write_text(json.dumps(payload), encoding="utf-8")

    def run_report(self, *, secret: str = "sk-or-v1-unit-test-secret-1234567890") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["OPENROUTER_API_KEY"] = secret
        env["ACADEMIC_ENGINE_OPENROUTER_MODEL"] = "openrouter/test-model"
        return subprocess.run(
            [
                sys.executable,
                str(self.script),
                "--root",
                str(self.root),
                "--workflow-id",
                self.workflow_id,
                "--stdout-log",
                str(self.stdout_log),
                "--stderr-log",
                str(self.stderr_log),
                "--report",
                str(self.report),
            ],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def test_report_passes_for_allowed_openrouter_routes(self) -> None:
        self.write_workflow(
            roles=[
                {
                    "role_run_id": "01-academic-repair-orchestrator",
                    "role_id": "academic-repair-orchestrator",
                    "status": "succeeded",
                    "executor_route": "default",
                    "executor_id": "codex-cli",
                    "blockers": [],
                },
                {
                    "role_run_id": "02-academic-source-verifier",
                    "role_id": "academic-source-verifier",
                    "status": "succeeded",
                    "executor_route": "verifier",
                    "executor_id": "openrouter",
                    "blockers": [],
                },
                {
                    "role_run_id": "04-academic-submission-evaluator",
                    "role_id": "academic-submission-evaluator",
                    "status": "succeeded",
                    "executor_route": "evaluator",
                    "executor_id": "openrouter",
                    "blockers": [{"code": "primary-support-gap"}],
                },
            ]
        )

        result = self.run_report()

        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.report.read_text(encoding="utf-8")
        self.assertIn("Route policy: PASS", text)
        self.assertIn("Secret scan: PASS", text)
        self.assertIn("| academic-source-verifier | verifier | openrouter | succeeded |", text)
        self.assertIn("| academic-submission-evaluator | evaluator | openrouter | succeeded |", text)

    def test_report_fails_when_openrouter_reaches_finalizer(self) -> None:
        self.write_workflow(
            roles=[
                {
                    "role_run_id": "01-academic-finalizer",
                    "role_id": "academic-finalizer",
                    "status": "succeeded",
                    "executor_route": "default",
                    "executor_id": "openrouter",
                    "blockers": [],
                }
            ]
        )

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Route policy violation", result.stderr)

    def test_report_fails_when_source_verifier_uses_evaluator_route(self) -> None:
        self.write_workflow(
            roles=[
                {
                    "role_run_id": "01-academic-source-verifier",
                    "role_id": "academic-source-verifier",
                    "status": "succeeded",
                    "executor_route": "evaluator",
                    "executor_id": "openrouter",
                    "blockers": [],
                }
            ]
        )

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Route policy violation", result.stderr)

    def test_report_fails_when_submission_evaluator_uses_verifier_route(self) -> None:
        self.write_workflow(
            roles=[
                {
                    "role_run_id": "01-academic-submission-evaluator",
                    "role_id": "academic-submission-evaluator",
                    "status": "succeeded",
                    "executor_route": "verifier",
                    "executor_id": "openrouter",
                    "blockers": [],
                }
            ]
        )

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Route policy violation", result.stderr)

    def test_report_fails_on_exact_secret_leak(self) -> None:
        secret = "sk-or-v1-unit-test-secret-1234567890"
        self.write_workflow(
            roles=[
                {
                    "role_run_id": "01-academic-submission-evaluator",
                    "role_id": "academic-submission-evaluator",
                    "status": "succeeded",
                    "executor_route": "evaluator",
                    "executor_id": "openrouter",
                    "blockers": [],
                }
            ]
        )
        self.stdout_log.write_text(f"leaked {secret}\n", encoding="utf-8")

        result = self.run_report(secret=secret)

        self.assertEqual(result.returncode, 1)
        self.assertIn("Secret scan failed", result.stderr)

    def test_report_fails_on_readme_secret_pattern(self) -> None:
        self.write_workflow(
            roles=[
                {
                    "role_run_id": "01-academic-submission-evaluator",
                    "role_id": "academic-submission-evaluator",
                    "status": "succeeded",
                    "executor_route": "evaluator",
                    "executor_id": "openrouter",
                    "blockers": [],
                }
            ]
        )
        (self.root / "README.md").write_text(
            "OPENROUTER_API_KEY=sk-or-v1-unit-test-secret-1234567890\n",
            encoding="utf-8",
        )

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Secret scan failed", result.stderr)
