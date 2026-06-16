from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests.test_telegram_console import (
    AutonomousDaemonLaunchdManager,
    FakeLaunchctl,
    build_fake_repo,
    work_cli_module,
)


class AutonomousLaunchdCliTests(unittest.TestCase):
    def test_autonomous_daemon_launchd_install_status_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            home_dir = root / "home"
            fake_launchctl = FakeLaunchctl()
            manager = AutonomousDaemonLaunchdManager(
                root,
                home_dir=home_dir,
                command_runner=fake_launchctl,
                python_executable="/usr/bin/python3",
            )

            with patch("telegram_console.work_cli_autonomous.AutonomousDaemonLaunchdManager", return_value=manager):
                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(
                        [
                            "autonomous",
                            "daemon",
                            "launchd",
                            "install",
                            "--works",
                            "all",
                            "--mode",
                            "autonomous-full",
                            "--poll-seconds",
                            "15",
                            "--max-cycles",
                            "25",
                            "--max-runtime-minutes",
                            "120",
                            "--json",
                        ],
                        root_dir=root,
                    )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            install_payload = json.loads(stdout.getvalue())
            self.assertEqual(install_payload["kind"], "autonomous-daemon-launchd-result")
            self.assertEqual(install_payload["status"]["works_scope"], "all")
            self.assertEqual(install_payload["status"]["status"], "loaded")
            self.assertEqual(install_payload["readiness_claim"], "none")
            self.assertTrue(install_payload["status"]["installed"])
            self.assertTrue(manager.paths.installed_plist.exists())
            plist_text = manager.paths.installed_plist.read_text(encoding="utf-8")
            self.assertIn("<key>KeepAlive</key>\n  <true/>", plist_text)
            self.assertIn("<key>ThrottleInterval</key>\n  <integer>15</integer>", plist_text)

            with patch("telegram_console.work_cli_autonomous.AutonomousDaemonLaunchdManager", return_value=manager):
                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(
                        ["autonomous", "daemon", "launchd", "status", "--works", "all", "--json"],
                        root_dir=root,
                    )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            status_payload = json.loads(stdout.getvalue())
            self.assertEqual(status_payload["kind"], "autonomous-daemon-launchd-status")
            self.assertEqual(status_payload["status"], "loaded")
            self.assertEqual(status_payload["readiness_claim"], "none")
            self.assertTrue(status_payload["loaded"])
            self.assertEqual(status_payload["works_scope"], "all")

    def test_autonomous_daemon_launchd_invalid_scope_keeps_json_error_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "daemon", "launchd", "status", "--works", "missing-work", "--json"],
                    root_dir=root,
                )

            self.assertEqual(code, 1)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "autonomous-daemon-launchd-error")
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["stop_reason"], "workspace-config-error")
            self.assertEqual(payload["readiness_claim"], "none")


if __name__ == "__main__":
    unittest.main()
