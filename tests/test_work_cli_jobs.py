from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from academic_engine import work_cli as work_cli_module


class WorkCliJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def run_cli(self, argv: list[str]) -> tuple[int, str, str, FakeService]:
        fake = FakeService(self.root)
        stdout = StringIO()
        stderr = StringIO()
        with patch.object(work_cli_module, "EngineService", lambda root: fake):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(argv, root_dir=self.root)
        return code, stdout.getvalue(), stderr.getvalue(), fake

    def test_jobs_submit_workflow_json(self) -> None:
        code, stdout, stderr, fake = self.run_cli(
            [
                "jobs",
                "submit-workflow",
                "--work",
                "demo-work",
                "--lane",
                "thesis",
                "--action",
                "verify",
                "--target",
                "chapter-1",
                "--notes",
                "check",
                "--json",
            ]
        )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["job_id"], "job-demo")
        self.assertEqual(fake.submitted[0].work_id, "demo-work")
        self.assertEqual(fake.submitted[0].target_or_topic, "chapter-1")

    def test_jobs_list_status_and_dispatch_json(self) -> None:
        for argv, expected_kind in (
            (["jobs", "list", "--status", "queued", "--json"], "job-list"),
            (["jobs", "status", "job-demo", "--json"], "engine-job"),
            (["jobs", "dispatch", "--limit", "1", "--json"], "job-dispatch"),
        ):
            with self.subTest(argv=argv):
                code, stdout, stderr, _fake = self.run_cli(argv)
                self.assertEqual(code, 0)
                self.assertEqual(stderr, "")
                self.assertEqual(json.loads(stdout)["kind"], expected_kind)

    def test_jobs_cancel_retry_resume_text(self) -> None:
        for argv, expected in (
            (["jobs", "cancel", "job-demo", "--reason", "operator"], "blocked"),
            (["jobs", "retry", "job-demo"], "queued"),
            (["jobs", "resume", "job-demo"], "queued"),
        ):
            with self.subTest(argv=argv):
                code, stdout, stderr, _fake = self.run_cli(argv)
                self.assertEqual(code, 0)
                self.assertEqual(stderr, "")
                self.assertIn("job-demo", stdout)
                self.assertIn(expected, stdout)

    def test_jobs_dispatch_text_includes_job_details(self) -> None:
        code, stdout, stderr, _fake = self.run_cli(["jobs", "dispatch", "--limit", "1"])

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("Dispatched: 1", stdout)
        self.assertIn("job-dispatched", stdout)
        self.assertIn("running", stdout)
        self.assertIn("Skipped: 1", stdout)
        self.assertIn("job-skipped", stdout)
        self.assertIn("global-concurrency-limit", stdout)
        self.assertIn("Blocked: 1", stdout)
        self.assertIn("job-blocked", stdout)
        self.assertIn("Reconciled: 1", stdout)
        self.assertIn("job-reconciled", stdout)

    def test_job_inspect_json(self) -> None:
        code, stdout, stderr, _fake = self.run_cli(["job-inspect", "job-demo", "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "job-inspection")

    def test_export_explain_json(self) -> None:
        code, stdout, stderr, _fake = self.run_cli(["export-explain", "thesis", "--work", "demo-work", "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "export-explanation")
        self.assertEqual(payload["status"], "blocked")


class FakeService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.submitted: list[object] = []

    def submit_workflow_job(self, request: object) -> dict[str, object]:
        self.submitted.append(request)
        return {"kind": "engine-job", "job_id": "job-demo", "status": "queued", "work_id": request.work_id}

    def list_jobs(self, *, work_id: str | None = None, status: str | None = None) -> dict[str, object]:
        return {
            "kind": "job-list",
            "version": "v1",
            "jobs": [{"job_id": "job-demo", "work_id": work_id, "status": status or "queued"}],
        }

    def get_job(self, job_id: str) -> dict[str, object]:
        return {"kind": "engine-job", "job_id": job_id, "status": "queued", "work_id": "demo-work"}

    def cancel_job(self, request: object) -> dict[str, object]:
        return {"kind": "engine-job", "job_id": request.job_id, "status": "blocked", "work_id": "demo-work"}

    def retry_job(self, request: object) -> dict[str, object]:
        return {"kind": "engine-job", "job_id": request.job_id, "status": "queued", "work_id": "demo-work"}

    def resume_job(self, request: object) -> dict[str, object]:
        return {"kind": "engine-job", "job_id": request.job_id, "status": "queued", "work_id": "demo-work"}

    def dispatch_jobs(self, request: object) -> dict[str, object]:
        return {
            "kind": "job-dispatch",
            "version": "v1",
            "dispatched": [{"job_id": "job-dispatched", "status": "running", "work_id": "demo-work"}],
            "skipped": [
                {
                    "job_id": "job-skipped",
                    "status": "queued",
                    "work_id": "demo-work",
                    "reason": "global-concurrency-limit",
                }
            ],
            "blocked": [{"job_id": "job-blocked", "status": "blocked", "work_id": "demo-work"}],
            "reconciled": [{"job_id": "job-reconciled", "status": "completed", "work_id": "demo-work"}],
            "limit": request.limit,
        }

    def inspect_job(self, request: object) -> dict[str, object]:
        return {"kind": "job-inspection", "version": "v1", "job": {"job_id": request.job_id}}

    def explain_export(self, subject: str, *, work_id: str | None = None) -> dict[str, object]:
        return {
            "kind": "export-explanation",
            "version": "v1",
            "subject": subject,
            "work_id": work_id,
            "status": "blocked",
            "reasons": [{"code": "no-successful-workflow"}],
        }
