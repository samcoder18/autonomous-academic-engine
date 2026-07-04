from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from tests.test_academic_engine import (
    TEST_ARTICLE_CHECKLIST,
    TEST_THESIS_SECTION,
    TEST_WORK_ID,
    build_fake_repo,
    work_cli_module,
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
                root / "output" / "runtime" / "runs" / "thesis-repair-runtime",
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
                root / "output" / "runtime" / "runs" / "article-contract-gate-runtime",
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

    def test_runtime_index_refresh_and_status_cli_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["runtime-index", "refresh", "--json"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            refresh = json.loads(stdout.getvalue())
            self.assertEqual(refresh["kind"], "runtime-index-refresh")
            self.assertEqual(refresh["status"], "refreshed")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["runtime-index", "status", "--work", TEST_WORK_ID, "--limit", "3", "--json"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "runtime-index")
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["works"][0]["work_id"], TEST_WORK_ID)

    def test_runtime_index_status_cli_human_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            work_cli_module.main(["runtime-index", "refresh"], root_dir=root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["runtime-index", "status"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Runtime index:", stdout.getvalue())
            self.assertIn("Works:", stdout.getvalue())
            self.assertIn("Recent runs:", stdout.getvalue())
            self.assertNotIn("{", stdout.getvalue())

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
