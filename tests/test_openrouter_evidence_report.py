from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from academic_engine.executors import OPENROUTER_ROLE_POLICY

FAKE_OPENROUTER_KEY = "sk-or-v1-" + "unit-test-secret-1234567890"


class OpenRouterEvidenceReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.script = Path(__file__).resolve().parents[1] / "scripts" / "openrouter_evidence_report.py"
        self.workflow_id = "workflow-live-smoke"
        self.workflow_dir = self.root / "output" / "runs" / self.workflow_id
        self.workflow_dir.mkdir(parents=True)
        (self.workflow_dir / "roles").mkdir()
        self.runtime_dir = self.root / "output" / "runtime" / "runs" / self.workflow_id
        self.runtime_dir.mkdir(parents=True)
        self.stdout_log = self.root / "stdout.log"
        self.stderr_log = self.root / "stderr.log"
        self.stdout_log.write_text("Workflow ID: workflow-live-smoke\n", encoding="utf-8")
        self.stderr_log.write_text("", encoding="utf-8")
        self.report = self.root / "docs" / "deploy" / "evidence" / "report.md"
        self.write_runtime_request()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_workflow(
        self,
        *,
        roles: list[dict[str, object]],
        execution_status: str = "succeeded",
        work_id: str = "openrouter-live-smoke",
        lane: str = "article",
        action: str = "repair",
    ) -> None:
        payload = {
            "version": "workflow-run/v1",
            "workflow_id": self.workflow_id,
            "work_id": work_id,
            "lane": lane,
            "action": action,
            "status": "completed",
            "execution_status": execution_status,
            "readiness_status": "strong-draft-with-blockers",
            "role_runs": roles,
            "blockers": [],
        }
        (self.workflow_dir / "workflow.json").write_text(json.dumps(payload), encoding="utf-8")

    def write_runtime_request(
        self,
        *,
        work_id: str = "openrouter-live-smoke",
        lane: str = "article",
        action: str = "repair",
        target: str = "works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md",
        search_override: bool = False,
    ) -> None:
        payload = {
            "workflow_id": self.workflow_id,
            "work_id": work_id,
            "lane": lane,
            "action": action,
            "target": target,
            "search_override": search_override,
        }
        (self.runtime_dir / "request.json").write_text(json.dumps(payload), encoding="utf-8")

    def passing_roles(self) -> list[dict[str, object]]:
        return [
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
                "execution_mode": "read-only",
                "blockers": [],
            },
            {
                "role_run_id": "03-academic-citation-checker",
                "role_id": "academic-citation-checker",
                "status": "succeeded",
                "executor_route": "default",
                "executor_id": "codex-cli",
                "blockers": [],
            },
            {
                "role_run_id": "04-academic-submission-evaluator",
                "role_id": "academic-submission-evaluator",
                "status": "succeeded",
                "executor_route": "evaluator",
                "executor_id": "openrouter",
                "execution_mode": "read-only",
                "blockers": [{"code": "primary-support-gap"}],
            },
            {
                "role_run_id": "05-academic-finalizer",
                "role_id": "academic-finalizer",
                "status": "succeeded",
                "executor_route": "default",
                "executor_id": "codex-cli",
                "blockers": [],
            },
        ]

    def run_report(
        self,
        *,
        secret: str = FAKE_OPENROUTER_KEY,
        extra_args: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["OPENROUTER_API_KEY"] = secret
        env["ACADEMIC_ENGINE_OPENROUTER_MODEL"] = "openrouter/test-model"
        command = [
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
        ]
        command.extend(extra_args or [])
        command.extend(("--report", str(self.report)))
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def test_report_passes_for_allowed_openrouter_routes(self) -> None:
        self.write_workflow(roles=self.passing_roles())

        result = self.run_report()

        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.report.read_text(encoding="utf-8")
        self.assertIn("Controlled smoke: PASS", text)
        self.assertIn("Route policy: PASS", text)
        self.assertIn("Secret scan: PASS", text)
        self.assertIn("| academic-source-verifier | verifier | openrouter | read-only | succeeded |", text)
        self.assertIn("| academic-submission-evaluator | evaluator | openrouter | read-only | succeeded |", text)

    def test_report_passes_for_one_role_qualification_with_custom_scope(self) -> None:
        work_id = "openrouter-role-qualification"
        target = f"works/{work_id}/articles/reviews/qualification.md"
        self.write_runtime_request(
            work_id=work_id,
            lane="article",
            action="review",
            target=target,
        )
        self.write_workflow(
            work_id=work_id,
            lane="article",
            action="review",
            roles=[
                {
                    "role_run_id": "01-academic-source-verifier",
                    "role_id": "academic-source-verifier",
                    "status": "succeeded",
                    "executor_route": "verifier",
                    "executor_id": "openrouter",
                    "execution_mode": "read-only",
                    "blockers": [],
                }
            ],
        )

        result = self.run_report(
            extra_args=[
                "--expected-work-id",
                work_id,
                "--expected-lane",
                "article",
                "--expected-action",
                "review",
                "--expected-target",
                target,
                "--expected-role",
                "academic-source-verifier",
            ]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.report.read_text(encoding="utf-8")
        self.assertIn("Controlled smoke: PASS", text)
        self.assertIn("Route policy: PASS", text)

    def test_report_rejects_traversal_work_id_before_secret_scan(self) -> None:
        self.write_workflow(roles=self.passing_roles())

        result = self.run_report(extra_args=["--expected-work-id", "../outside"])

        self.assertEqual(result.returncode, 2)
        self.assertIn("expected work ID", result.stderr)

    def test_report_rejects_absolute_work_id_before_secret_scan(self) -> None:
        self.write_workflow(roles=self.passing_roles())

        result = self.run_report(extra_args=["--expected-work-id", "/tmp/outside"])

        self.assertEqual(result.returncode, 2)
        self.assertIn("expected work ID", result.stderr)

    def test_evidence_policy_matches_router_role_policy(self) -> None:
        spec = importlib.util.spec_from_file_location("openrouter_evidence_report", self.script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module.EXPECTED_OPENROUTER_ROLE_POLICY, OPENROUTER_ROLE_POLICY)

    def test_report_fails_when_openrouter_execution_mode_does_not_match_policy(self) -> None:
        roles = self.passing_roles()
        for role in roles:
            if role["role_id"] == "academic-submission-evaluator":
                role["execution_mode"] = "write-plan"
        self.write_workflow(roles=roles)

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Route policy violation", result.stderr)

    def test_report_fails_when_required_openrouter_role_is_missing(self) -> None:
        roles = [
            role
            for role in self.passing_roles()
            if role["role_id"] != "academic-submission-evaluator"
        ]
        self.write_workflow(roles=roles)

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Route policy violation", result.stderr)

    def test_report_fails_when_default_role_uses_non_codex_executor(self) -> None:
        roles = self.passing_roles()
        for role in roles:
            if role["role_id"] == "academic-citation-checker":
                role["executor_id"] = "stub-api"
        self.write_workflow(roles=roles)

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Route policy violation", result.stderr)

    def test_report_fails_when_thesis_role_uses_openrouter(self) -> None:
        roles = self.passing_roles()
        roles.append(
            {
                "role_run_id": "06-thesis-source-verifier",
                "role_id": "thesis-source-verifier",
                "status": "succeeded",
                "executor_route": "verifier",
                "executor_id": "openrouter",
                "blockers": [],
            }
        )
        self.write_workflow(roles=roles)

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Route policy violation", result.stderr)

    def test_report_fails_when_runtime_request_enables_search(self) -> None:
        self.write_runtime_request(search_override=True)
        self.write_workflow(roles=self.passing_roles())

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Controlled smoke violation", result.stderr)

    def test_report_fails_when_workflow_execution_failed(self) -> None:
        self.write_workflow(roles=self.passing_roles(), execution_status="failed")

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Controlled smoke violation", result.stderr)

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
        secret = FAKE_OPENROUTER_KEY
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
            f"OPENROUTER_API_KEY={FAKE_OPENROUTER_KEY}\n",
            encoding="utf-8",
        )

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Secret scan failed", result.stderr)
