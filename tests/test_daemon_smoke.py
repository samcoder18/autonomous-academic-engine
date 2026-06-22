from __future__ import annotations

import os
import signal
import tempfile
import time
import unittest
import warnings
from pathlib import Path

from academic_engine.autonomous_daemon import (
    DAEMON_TERMINAL_STATUSES,
    daemon_lock_path,
    daemon_status_payload,
    start_daemon_process,
)
from academic_engine.autonomous_launchd import AutonomousDaemonLaunchdManager
from academic_engine.autonomous_scheduler import (
    multi_daemon_lock_path,
    multi_daemon_status_payload,
    start_multi_work_daemon_process,
)
from tests.test_academic_engine import (
    TEST_WORK_ID,
    FakeLaunchctl,
    build_fake_repo,
    write_file,
    write_raw_manifest,
    write_submission_ready_workflow,
)


def _terminate_pid(pid: int | None) -> None:
    if not pid or pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return


class DaemonSmokeTests(unittest.TestCase):
    def test_background_daemon_process_reaches_terminal_state_and_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            write_file(root / "works" / TEST_WORK_ID / "articles" / "reviews" / "demo.md", "# Review\n")
            write_submission_ready_workflow(root, "article")

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"subprocess .* is still running",
                    category=ResourceWarning,
                )
                state = start_daemon_process(
                    root_dir=root,
                    work_id=TEST_WORK_ID,
                    mode="autonomous-full",
                    poll_seconds=0,
                    max_cycles=1,
                    max_runtime_minutes=2,
                )
            pid = int(state["pid"])
            self.addCleanup(_terminate_pid, pid)

            terminal_state = None
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                payload = daemon_status_payload(root, TEST_WORK_ID)
                if payload.get("status") in DAEMON_TERMINAL_STATUSES:
                    terminal_state = payload
                    break
                time.sleep(0.1)

            self.assertIsNotNone(terminal_state, "daemon did not reach a terminal state in time")
            assert terminal_state is not None
            self.assertEqual(terminal_state["status"], "completed")
            self.assertEqual(terminal_state["stop_reason"], "terminal-export")
            self.assertTrue((root / "output" / "docx" / TEST_WORK_ID / "articles" / "demo.docx").exists())

            release_deadline = time.monotonic() + 2.0
            while time.monotonic() < release_deadline and daemon_lock_path(root, TEST_WORK_ID).exists():
                time.sleep(0.05)
            self.assertFalse(daemon_lock_path(root, TEST_WORK_ID).exists())

    def test_background_multi_work_daemon_reaches_terminal_state_and_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            write_file(root / "works" / TEST_WORK_ID / "articles" / "reviews" / "demo.md", "# Review\n")
            write_submission_ready_workflow(root, "article")

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"subprocess .* is still running",
                    category=ResourceWarning,
                )
                state = start_multi_work_daemon_process(
                    root_dir=root,
                    works_scope="all",
                    mode="autonomous-full",
                    poll_seconds=0,
                    max_cycles=1,
                    max_runtime_minutes=2,
                )
            pid = int(state["pid"])
            self.addCleanup(_terminate_pid, pid)

            terminal_state = None
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                payload = multi_daemon_status_payload(root, works_scope="all")
                if payload.get("status") in DAEMON_TERMINAL_STATUSES:
                    terminal_state = payload
                    break
                time.sleep(0.1)

            self.assertIsNotNone(terminal_state, "multi-work daemon did not reach a terminal state in time")
            assert terminal_state is not None
            self.assertEqual(terminal_state["status"], "completed")
            self.assertEqual(terminal_state["stop_reason"], "terminal-export")

            release_deadline = time.monotonic() + 2.0
            while time.monotonic() < release_deadline and multi_daemon_lock_path(root).exists():
                time.sleep(0.05)
            self.assertFalse(multi_daemon_lock_path(root).exists())


class LaunchdSmokeTests(unittest.TestCase):
    def test_launchd_manager_lifecycle_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            fake_launchctl = FakeLaunchctl()
            manager = AutonomousDaemonLaunchdManager(
                root,
                home_dir=root / "home",
                command_runner=fake_launchctl,
                python_executable="/usr/bin/python3",
            )

            install_result = manager.install(
                works_scope="all",
                mode="autonomous-full",
                poll_seconds=15,
                max_cycles=25,
                max_runtime_minutes=120,
            )
            plist_text = manager.paths.installed_plist.read_text(encoding="utf-8")
            self.assertTrue(install_result.status.loaded)
            self.assertIn("academic_engine.work_cli", plist_text)
            self.assertIn("<string>--works</string>", plist_text)
            self.assertIn("<string>all</string>", plist_text)
            self.assertIn("<key>KeepAlive</key>\n  <true/>", plist_text)
            self.assertIn("<key>ThrottleInterval</key>\n  <integer>15</integer>", plist_text)

            restarted = manager.restart(works_scope="all")
            self.assertTrue(restarted.installed)
            self.assertTrue(restarted.loaded)

            stopped = manager.stop(works_scope="all")
            self.assertTrue(stopped.installed)
            self.assertFalse(stopped.loaded)

            uninstalled = manager.uninstall(works_scope="all")
            self.assertFalse(uninstalled.installed)
            self.assertFalse(uninstalled.loaded)
            self.assertFalse(manager.paths.installed_plist.exists())


if __name__ == "__main__":
    unittest.main()
