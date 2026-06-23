from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from academic_engine.autonomous_daemon import daemon_state_path
from academic_engine.autonomous_runner import execute_autonomous_command, run_autonomous_plan
from academic_engine.autonomous_scheduler import multi_daemon_state_path, multi_daemon_stop_path
from tests.test_academic_engine import (
    TEST_ARTICLE_DRAFT,
    TEST_WORK_ID,
    WorkflowOrchestrator,
    add_demo_work_clone,
    build_fake_repo,
    work_cli_module,
    write_file,
    write_raw_manifest,
    write_submission_ready_workflow,
)


class AutonomousCliTests(unittest.TestCase):
    def test_autonomous_run_executes_all_completed_steps_up_to_plan_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            plan = SimpleNamespace(
                to_dict=lambda: {
                    "kind": "autonomous-plan",
                    "mode": "autonomous-safe",
                    "work_id": TEST_WORK_ID,
                    "status": "ready",
                    "steps": [
                        {
                            "command": "work-status",
                            "policy": {"decision": "allowed"},
                        },
                        {
                            "command": "standards-status",
                            "policy": {"decision": "allowed"},
                        },
                    ],
                    "stop_reason": None,
                }
            )

            state = run_autonomous_plan(root_dir=root, plan=plan, dry_run=False, execute=True)

            self.assertEqual(state["status"], "completed")
            self.assertEqual(len(state["executed_steps"]), 2)
            self.assertEqual(
                [item["command"] for item in state["executed_steps"]],
                ["work-status", "standards-status"],
            )

    def test_autonomous_run_passes_work_id_to_execution(self) -> None:
        class FakeOrchestrator:
            def __init__(self) -> None:
                self.work_ids: list[str | None] = []

            def start_run(
                self,
                lane: str,
                action: str,
                target: str,
                *,
                work_id: str | None = None,
            ) -> dict[str, str]:
                self.work_ids.append(work_id)
                return {"run_id": f"{work_id}:{lane}:{action}", "work_id": str(work_id)}

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            fake = FakeOrchestrator()
            plan = SimpleNamespace(
                to_dict=lambda: {
                    "kind": "autonomous-plan",
                    "mode": "autonomous-full",
                    "work_id": "zeta-work",
                    "status": "ready",
                    "steps": [
                        {
                            "command": "launch-academic review works/zeta-work/articles/drafts/demo.md",
                            "policy": {"decision": "allowed"},
                        }
                    ],
                    "stop_reason": None,
                }
            )

            with patch("academic_engine.autonomous_runner.WorkflowOrchestrator", return_value=fake):
                state = run_autonomous_plan(root_dir=root, plan=plan, dry_run=False, execute=True)

            self.assertEqual(fake.work_ids, ["zeta-work"])
            self.assertEqual(state["executed_steps"][0]["status"], "started-run")

    def test_execute_autonomous_command_rejects_invalid_export_result(self) -> None:
        class FakeOrchestrator:
            def export_docx(self, subject: str, *, work_id: str | None = None) -> dict[str, str]:
                return {"subject": subject, "path": ""}

        result = execute_autonomous_command(
            FakeOrchestrator(),
            "export-thesis-docx",
            work_id=TEST_WORK_ID,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "invalid-export-result")

    def test_execute_autonomous_command_rejects_mismatched_started_work(self) -> None:
        class FakeOrchestrator:
            def start_run(
                self,
                lane: str,
                action: str,
                target: str,
                *,
                work_id: str | None = None,
            ) -> dict[str, str]:
                return {"run_id": "wrong:article:review", "work_id": "wrong-work"}

        result = execute_autonomous_command(
            FakeOrchestrator(),
            "launch-academic review works/demo-work/articles/drafts/demo.md",
            work_id=TEST_WORK_ID,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "invalid-start-result")

    def test_autonomous_plan_cli_prints_policy_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["autonomous", "plan", "--mode", "autonomous-safe"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Autonomous plan:", stdout.getvalue())
            self.assertIn("launch-academic review", stdout.getvalue())
            self.assertIn("decision=allowed", stdout.getvalue())
            self.assertNotIn("submission-ready", stdout.getvalue())

    def test_autonomous_run_dry_run_writes_state_without_launching(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "run", "--mode", "autonomous-safe", "--max-steps", "2", "--dry-run"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Autonomous run: dry-run", stdout.getvalue())
            state_path = root / "output" / "runtime" / "autonomous" / "demo-work.json"
            self.assertTrue(state_path.exists())
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "dry-run")
            self.assertEqual(payload["mode"], "autonomous-safe")
            self.assertEqual(payload["readiness_claim"], "none")

    def test_autonomous_run_execute_stops_when_plan_has_no_allowed_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            orchestrator = WorkflowOrchestrator(root)
            orchestrator.store.set_active_run(
                {
                    "run_id": "default:active",
                    "run_dir": str(root / "output" / "runtime" / "runs" / "active"),
                    "pid": os.getpid(),
                    "lane": "article",
                    "action": "review",
                    "started_at": "2026-04-18T10:22:00+00:00",
                    "project_root": str(root),
                    "work_id": TEST_WORK_ID,
                    "target": TEST_ARTICLE_DRAFT.as_posix(),
                }
            )

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "run", "--mode", "autonomous-safe", "--execute"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Autonomous run: stopped", stdout.getvalue())
            state_path = root / "output" / "runtime" / "autonomous" / "demo-work.json"
            payload = json.loads(state_path.read_text())
            self.assertEqual(payload["status"], "stopped")
            self.assertEqual(payload["stop_reason"], "A workflow run is already active for this work.")
            self.assertEqual(payload["readiness_claim"], "none")

    def test_autonomous_full_run_executes_export_after_finalization_check(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            write_file(root / "works" / TEST_WORK_ID / "articles" / "reviews" / "demo.md", "# Review\n")
            write_submission_ready_workflow(root, "article")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "run", "--mode", "autonomous-full", "--max-steps", "1", "--execute"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Autonomous run: completed", stdout.getvalue())
            docx_path = root / "output" / "docx" / "demo-work" / "articles" / "demo.docx"
            self.assertTrue(docx_path.exists())
            state_path = root / "output" / "runtime" / "autonomous" / "demo-work.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["readiness_claim"], "none")
            self.assertEqual(payload["executed_steps"][0]["status"], "completed")
            self.assertIn("export-article-docx", payload["executed_steps"][0]["command"])

    def test_autonomous_status_json_handles_corrupted_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            state_path = root / "output" / "runtime" / "autonomous" / f"{TEST_WORK_ID}.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text("{broken", encoding="utf-8")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["autonomous", "status", "--json"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "autonomous-run-state")
            self.assertEqual(payload["status"], "not-started")
            self.assertEqual(payload["readiness_claim"], "none")
            self.assertIsNone(payload["stop_reason"])

    def test_autonomous_status_invalid_work_keeps_json_error_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["autonomous", "status", "--work", "missing-work", "--json"], root_dir=root)

            self.assertEqual(code, 1)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "autonomous-cli-error")
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["stop_reason"], "workspace-config-error")
            self.assertEqual(payload["readiness_claim"], "none")


class AutonomousDaemonCliTests(unittest.TestCase):
    def test_autonomous_daemon_tick_json_writes_machine_state(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "autonomous",
                        "daemon",
                        "tick",
                        "--work",
                        TEST_WORK_ID,
                        "--mode",
                        "autonomous-full",
                        "--poll-seconds",
                        "0",
                        "--max-cycles",
                        "5",
                        "--max-runtime-minutes",
                        "10",
                        "--json",
                    ],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "autonomous-daemon-state")
            self.assertEqual(payload["work_id"], TEST_WORK_ID)
            self.assertEqual(payload["readiness_claim"], "none")
            self.assertEqual(payload["assessment_scope"]["depth"], "signals-only")
            state_path = root / "output" / "runtime" / "autonomous" / "demo-work.daemon.json"
            self.assertTrue(state_path.exists())

    def test_autonomous_daemon_start_status_and_stop_are_json_first(self) -> None:
        class FakeProcess:
            pid = 5432

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            with patch("academic_engine.autonomous_daemon.subprocess.Popen", return_value=FakeProcess()):
                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(
                        [
                            "autonomous",
                            "daemon",
                            "start",
                            "--work",
                            TEST_WORK_ID,
                            "--mode",
                            "autonomous-full",
                            "--poll-seconds",
                            "0",
                            "--max-cycles",
                            "5",
                            "--max-runtime-minutes",
                            "10",
                            "--json",
                        ],
                        root_dir=root,
                    )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            start_payload = json.loads(stdout.getvalue())
            self.assertEqual(start_payload["status"], "running")
            self.assertEqual(start_payload["pid"], 5432)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "daemon", "status", "--work", TEST_WORK_ID, "--json"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            status_payload = json.loads(stdout.getvalue())
            self.assertEqual(status_payload["status"], "running")
            self.assertEqual(status_payload["readiness_claim"], "none")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "daemon", "stop", "--work", TEST_WORK_ID, "--reason", "operator-stop", "--json"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            stop_payload = json.loads(stdout.getvalue())
            self.assertEqual(stop_payload["kind"], "autonomous-daemon-stop-request")
            self.assertEqual(stop_payload["reason"], "operator-stop")
            self.assertEqual(stop_payload["readiness_claim"], "none")

    def test_autonomous_daemon_start_refuses_duplicate_with_json_error(self) -> None:
        class FakeProcess:
            pid = 6543

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            with patch("academic_engine.autonomous_daemon.subprocess.Popen", return_value=FakeProcess()):
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    work_cli_module.main(
                        [
                            "autonomous",
                            "daemon",
                            "start",
                            "--work",
                            TEST_WORK_ID,
                            "--mode",
                            "autonomous-full",
                            "--json",
                        ],
                        root_dir=root,
                    )

                with patch("academic_engine.autonomous_daemon._pid_is_alive", return_value=True):
                    stdout = StringIO()
                    stderr = StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = work_cli_module.main(
                            [
                                "autonomous",
                                "daemon",
                                "start",
                                "--work",
                                TEST_WORK_ID,
                                "--mode",
                                "autonomous-full",
                                "--json",
                            ],
                            root_dir=root,
                        )

            self.assertEqual(code, 1)
            self.assertIn("daemon/lock-blocked", stderr.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["stop_reason"], "daemon-already-running")
            self.assertEqual(payload["readiness_claim"], "none")

    def test_autonomous_daemon_tick_works_all_returns_aggregate_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            add_demo_work_clone(root, "zeta-work")
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "autonomous",
                        "daemon",
                        "tick",
                        "--works",
                        "all",
                        "--mode",
                        "autonomous-full",
                        "--poll-seconds",
                        "0",
                        "--max-cycles",
                        "5",
                        "--max-runtime-minutes",
                        "10",
                        "--json",
                    ],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "autonomous-multi-work-daemon-state")
            self.assertEqual(payload["works_scope"], "all")
            self.assertEqual(payload["work_count"], 2)
            self.assertEqual(payload["selected_work_id"], TEST_WORK_ID)
            self.assertEqual(payload["last_schedule"]["kind"], "autonomous-daemon-schedule")
            self.assertEqual(payload["readiness_claim"], "none")
            state_path = root / "output" / "runtime" / "autonomous" / "multi-work.daemon.json"
            self.assertTrue(state_path.exists())

    def test_autonomous_daemon_tick_works_active_and_comma_list_are_public_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            add_demo_work_clone(root, "zeta-work")
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "autonomous",
                        "daemon",
                        "tick",
                        "--works",
                        "active",
                        "--mode",
                        "autonomous-full",
                        "--poll-seconds",
                        "0",
                        "--max-cycles",
                        "5",
                        "--max-runtime-minutes",
                        "10",
                        "--json",
                    ],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            active_payload = json.loads(stdout.getvalue())
            self.assertEqual(active_payload["works_scope"], "active")
            self.assertEqual(active_payload["work_ids"], [TEST_WORK_ID])
            self.assertEqual(active_payload["work_count"], 1)

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            add_demo_work_clone(root, "zeta-work")
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "autonomous",
                        "daemon",
                        "tick",
                        "--works",
                        "zeta-work,demo-work",
                        "--mode",
                        "autonomous-full",
                        "--poll-seconds",
                        "0",
                        "--max-cycles",
                        "5",
                        "--max-runtime-minutes",
                        "10",
                        "--json",
                    ],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            list_payload = json.loads(stdout.getvalue())
            self.assertEqual(list_payload["works_scope"], "zeta-work,demo-work")
            self.assertEqual(list_payload["work_ids"], ["zeta-work", TEST_WORK_ID])
            self.assertEqual(list_payload["work_count"], 2)

    def test_autonomous_daemon_start_status_and_stop_works_all_are_json_first(self) -> None:
        class FakeProcess:
            pid = 8765

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            add_demo_work_clone(root, "zeta-work")
            with patch("academic_engine.autonomous_scheduler.subprocess.Popen", return_value=FakeProcess()):
                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(
                        [
                            "autonomous",
                            "daemon",
                            "start",
                            "--works",
                            "all",
                            "--mode",
                            "autonomous-full",
                            "--poll-seconds",
                            "0",
                            "--max-cycles",
                            "5",
                            "--max-runtime-minutes",
                            "10",
                            "--json",
                        ],
                        root_dir=root,
                    )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            start_payload = json.loads(stdout.getvalue())
            self.assertEqual(start_payload["status"], "running")
            self.assertEqual(start_payload["works_scope"], "all")
            self.assertEqual(start_payload["pid"], 8765)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "daemon", "status", "--works", "all", "--json"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            status_payload = json.loads(stdout.getvalue())
            self.assertEqual(status_payload["status"], "running")
            self.assertEqual(status_payload["works_scope"], "all")
            self.assertEqual(status_payload["readiness_claim"], "none")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "daemon", "stop", "--works", "all", "--reason", "operator-stop", "--json"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            stop_payload = json.loads(stdout.getvalue())
            self.assertEqual(stop_payload["kind"], "autonomous-daemon-stop-request")
            self.assertEqual(stop_payload["works_scope"], "all")
            self.assertEqual(stop_payload["reason"], "operator-stop")
            self.assertEqual(stop_payload["readiness_claim"], "none")
            self.assertTrue(multi_daemon_stop_path(root).exists())

    def test_autonomous_daemon_start_works_all_refuses_duplicate_with_json_error(self) -> None:
        class FakeProcess:
            pid = 9876

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            with patch("academic_engine.autonomous_scheduler.subprocess.Popen", return_value=FakeProcess()):
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    work_cli_module.main(
                        [
                            "autonomous",
                            "daemon",
                            "start",
                            "--works",
                            "all",
                            "--mode",
                            "autonomous-full",
                            "--json",
                        ],
                        root_dir=root,
                    )

                with patch("academic_engine.autonomous_scheduler._pid_is_alive", return_value=True):
                    stdout = StringIO()
                    stderr = StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = work_cli_module.main(
                            [
                                "autonomous",
                                "daemon",
                                "start",
                                "--works",
                                "all",
                                "--mode",
                                "autonomous-full",
                                "--json",
                            ],
                            root_dir=root,
                        )

            self.assertEqual(code, 1)
            self.assertIn("daemon/lock-blocked", stderr.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["stop_reason"], "daemon-already-running")
            self.assertEqual(payload["readiness_claim"], "none")

    def test_autonomous_daemon_status_json_handles_corrupted_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            path = daemon_state_path(root, TEST_WORK_ID)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{broken", encoding="utf-8")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "daemon", "status", "--work", TEST_WORK_ID, "--json"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "autonomous-daemon-state")
            self.assertEqual(payload["status"], "not-started")
            self.assertEqual(payload["readiness_claim"], "none")
            self.assertIsNone(payload["lock"])
            self.assertIsNone(payload["stop_request"])

    def test_autonomous_multi_daemon_status_json_handles_corrupted_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            add_demo_work_clone(root, "zeta-work")
            path = multi_daemon_state_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{broken", encoding="utf-8")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "daemon", "status", "--works", "all", "--json"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "autonomous-multi-work-daemon-state")
            self.assertEqual(payload["status"], "not-started")
            self.assertEqual(payload["works_scope"], "all")
            self.assertEqual(payload["readiness_claim"], "none")

    def test_autonomous_daemon_status_invalid_work_keeps_json_error_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "daemon", "status", "--work", "missing-work", "--json"],
                    root_dir=root,
                )

            self.assertEqual(code, 1)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "autonomous-daemon-error")
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["stop_reason"], "workspace-config-error")
            self.assertEqual(payload["readiness_claim"], "none")


if __name__ == "__main__":
    unittest.main()
