from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from tests.test_telegram_console import (
    TEST_ARTICLE_CHECKLIST,
    TEST_THESIS_SECTION,
    TEST_WORK_ID,
    build_fake_repo,
    main,
    work_cli_module,
    write_projects_registry,
    write_raw_manifest,
    write_runtime_status_fixture,
)


class WorkCliRuntimeTests(unittest.TestCase):
    def test_cli_defaults_to_current_working_directory_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            previous_cwd = Path.cwd()
            stdout = StringIO()
            stderr = StringIO()
            try:
                os.chdir(root)
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(["work-status", "--json"])
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "work-state")
            self.assertEqual(payload["work_id"], TEST_WORK_ID)

    def test_work_status_cli_prints_compact_next_safe_action(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Work status:", stdout.getvalue())
            self.assertIn("Scope: signals-only", stdout.getvalue())
            self.assertIn("Next safe action:", stdout.getvalue())
            self.assertIn("launch-academic review", stdout.getvalue())
            self.assertNotIn("{", stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status", "--json"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "work-state")
            self.assertEqual(payload["suggested_next_action"]["action_id"], "article-review")

    def test_export_thesis_docx_blocked_workflow_prints_clean_cli_error(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["export-thesis-docx"], root_dir=root)

            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("DOCX export blocked", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_work_status_cli_exposes_thesis_repair_plan_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            write_runtime_status_fixture(
                root / "output" / "telegram" / "runtime" / "runs" / "thesis-repair-runtime",
                record_id="default:20260418-thesis-verify",
                entity_kind="workflow-run",
                project_id="default",
                project_title=root.name,
                project_root=root,
                work_id=TEST_WORK_ID,
                work_title="Demo work",
                lane="thesis",
                action="verify",
                thesis_repair_plan={
                    "eligible": True,
                    "kind": "thesis-repair-plan",
                    "target": TEST_THESIS_SECTION.as_posix(),
                    "suggested_action": "verify",
                    "suggested_command": f"launch-thesis verify {TEST_THESIS_SECTION.as_posix()}",
                    "safe_repair_actions": [],
                    "blocked_reasons": [],
                    "terminal_reason": None,
                    "readiness_claim": "none",
                },
                repair_iteration=0,
                terminal_reason="blocked-primary-support",
                repair_decision={"action": "repair", "reason": "repair-plan-available"},
                blockers=[
                    {
                        "category": "primary-support",
                        "code": "primary-support-gap",
                        "message": "Thesis section needs primary support.",
                        "repairable": True,
                    }
                ],
            )

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Thesis repair plan:", stdout.getvalue())
            self.assertIn("launch-thesis verify", stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status", "--json"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            latest = payload["runtime"]["recent"][0]
            self.assertEqual(latest["repair_decision"]["action"], "repair")
            self.assertEqual(latest["thesis_repair_plan"]["suggested_action"], "verify")

    def test_work_status_cli_exposes_contract_gate_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            write_runtime_status_fixture(
                root / "output" / "telegram" / "runtime" / "runs" / "article-contract-gate-runtime",
                record_id="default:20260418-article-finalize",
                entity_kind="workflow-run",
                project_id="default",
                project_title=root.name,
                project_root=root,
                work_id=TEST_WORK_ID,
                work_title="Demo work",
                lane="article",
                action="finalize",
                contract_gates=[
                    {
                        "gate_id": "required-output:checklist",
                        "status": "block",
                        "reason": "Required artifact `checklist` is missing.",
                        "blocks_export": True,
                        "blocks_submission_ready": True,
                        "lane": "article",
                        "action": "finalize",
                        "artifact": str(root / TEST_ARTICLE_CHECKLIST),
                    }
                ],
            )

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Contract gates: blocks=1 warnings=0", stdout.getvalue())
            self.assertNotIn("[{", stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status", "--json"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            latest = payload["runtime"]["recent"][0]
            self.assertEqual(latest["contract_gate_summary"]["block_count"], 1)
            self.assertTrue(any(item["category"] == "contract-gate" for item in payload["known_blockers"]))
            self.assertEqual(payload["suggested_next_action"]["action_id"], "article-repair")

    def test_runtime_commands_are_project_aware_and_show_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            bot_home = workspace / "bot-home"
            bot_home.mkdir(parents=True, exist_ok=True)
            repo_a = workspace / "alpha"
            repo_b = workspace / "beta"
            build_fake_repo(repo_a)
            build_fake_repo(repo_b)
            write_projects_registry(
                bot_home,
                [
                    {
                        "id": "alpha",
                        "title": "Диплом А",
                        "root_dir": str(repo_a),
                        "capabilities": ["thesis", "article"],
                    },
                    {
                        "id": "beta",
                        "title": "Диплом Б",
                        "root_dir": str(repo_b),
                        "capabilities": ["thesis"],
                    },
                ],
            )

            workflow_dir = bot_home / "output" / "telegram" / "runtime" / "runs" / "20260418-100000-alpha-thesis-verify"
            workflow_request = workflow_dir / "request.json"
            workflow_result = workflow_dir / "result.json"
            workflow_log = workflow_dir / "launcher.log"
            workflow_manifest = repo_a / "output" / "runs" / TEST_WORK_ID / "thesis" / "20260418-verify.meta.json"
            workflow_trace = repo_a / "output" / "runs" / TEST_WORK_ID / "thesis" / "20260418-verify.md"
            workflow_resolution = workflow_dir / "resolution.json"
            write_runtime_status_fixture(
                workflow_dir,
                record_id="alpha:20260418-thesis-verify",
                entity_kind="workflow-run",
                project_id="alpha",
                project_title="Диплом А",
                project_root=repo_a,
                work_id=TEST_WORK_ID,
                work_title="Demo work",
                lane="thesis",
                action="verify",
                attachments={
                    "request": str(workflow_request),
                    "result": str(workflow_result),
                    "log": str(workflow_log),
                    "manifest": str(workflow_manifest),
                    "trace": str(workflow_trace),
                    "resolution": str(workflow_resolution),
                },
                summary="Workflow verification completed.",
                contract_gates=[
                    {
                        "gate_id": "required-output:target-file",
                        "status": "block",
                        "reason": "Target file is missing.",
                        "blocks_export": True,
                        "blocks_submission_ready": True,
                        "lane": "thesis",
                        "action": "verify",
                    }
                ],
            )
            workflow_resolution.write_text(
                json.dumps(
                    {
                        "target_resolution": {
                            "normalized_path": TEST_THESIS_SECTION.as_posix(),
                            "resolution_mode": "legacy-root",
                            "work_source": "default",
                            "used_legacy_root_mapping": True,
                            "warning_code": "legacy-root-target",
                            "warning_message": (
                                "Legacy target path `manuscript/sections/01-introduction.md` "
                                f"resolved to `{TEST_THESIS_SECTION.as_posix()}`."
                            ),
                        },
                        "thesis_runtime": {
                            "summary_block": {
                                "kind": "thesis-section-summary",
                                "target": TEST_THESIS_SECTION.as_posix(),
                                "review_present": True,
                                "last_run_action": "verify",
                                "last_run_status": "success",
                                "blocker_count": 0,
                                "terminal_reason": None,
                                "suggested_next_action": "review-section",
                            }
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            chat_dir = bot_home / "output" / "telegram" / "runtime" / "agent_tasks" / "20260418-101500-beta-chat"
            chat_request = chat_dir / "request.json"
            chat_result = chat_dir / "result.json"
            chat_response = chat_dir / "assistant.txt"
            chat_stdout = chat_dir / "codex.stdout.jsonl"
            chat_stderr = chat_dir / "codex.stderr.log"
            write_runtime_status_fixture(
                chat_dir,
                record_id="beta:20260418-chat",
                entity_kind="chat-turn",
                project_id="beta",
                project_title="Диплом Б",
                project_root=repo_b,
                work_id=TEST_WORK_ID,
                work_title="Demo work",
                profile="execute",
                action="chat",
                attachments={
                    "request": str(chat_request),
                    "result": str(chat_result),
                    "response": str(chat_response),
                    "stdout": str(chat_stdout),
                    "stderr": str(chat_stderr),
                },
                summary="Chat turn completed.",
            )

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--root", str(bot_home), "runtime", "status", "--project", "alpha"])

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("alpha:20260418-thesis-verify", stdout.getvalue())
            self.assertIn("Lane summary:", stdout.getvalue())
            self.assertIn("gates=1/0", stdout.getvalue())
            self.assertIn("Resolution warning:", stdout.getvalue())
            self.assertNotIn("beta:20260418-chat", stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--root", str(bot_home), "runtime", "status", "--kind", "chat", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(len(payload["records"]), 1)
            self.assertEqual(payload["records"][0]["record_id"], "beta:20260418-chat")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--root", str(bot_home), "runtime", "show", "alpha:20260418-thesis-verify"])
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("workflow-run", stdout.getvalue())
            self.assertIn("next=review-section", stdout.getvalue())
            self.assertIn("Contract gates: blocks=1 warnings=0", stdout.getvalue())
            self.assertIn("Resolution warning:", stdout.getvalue())
            self.assertIn(TEST_THESIS_SECTION.as_posix(), stdout.getvalue())
            self.assertIn(str(workflow_manifest), stdout.getvalue())
            self.assertIn(str(workflow_trace), stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "--root",
                        str(bot_home),
                        "runtime",
                        "path",
                        "beta:20260418-chat",
                        "response",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(stdout.getvalue().strip(), str(chat_response))

    def test_readme_and_agents_keep_runtime_command_truth_in_sync(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        agents = (repo_root / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("autonomous daemon run --work <slug>", readme)
        self.assertIn("autonomous daemon status --work <slug>", readme)
        self.assertIn("meta/runtime-reliability-audit-2026-04-20.md", readme)
        self.assertIn("autonomous daemon run [--stuck-after-minutes N]", agents)
        self.assertIn("meta/runtime-reliability-audit-2026-04-20.md", agents)


if __name__ == "__main__":
    unittest.main()
