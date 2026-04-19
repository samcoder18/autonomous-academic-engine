"""Integration tests for ops-alerts and resource-guards wiring in the autonomous daemon.

Focus is on **observability contracts**, not on exercising the full daemon loop:

- stale-lock recovery emits a ``daemon/stale-lock-recovered`` warning;
- already-running lock rejection emits a ``daemon/lock-blocked`` warning;
- ``run_daemon_foreground`` installs a ``RunGuards`` bundle sourced from
  env / CLI params, without duplicating the existing inline ``max-runtime``
  / ``max-cycles`` checks.

The tests capture alerts via a fake :class:`OpsAlertSink` installed through
``configure_default_sink`` and restored in ``tearDown``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any

from telegram_console import ops_alerts
from telegram_console.autonomous_daemon import (
    _build_foreground_guards,
    _resolve_stuck_after_minutes,
    acquire_daemon_lock,
    release_daemon_lock,
    write_daemon_lock,
)
from telegram_console.ops_alerts import OpsAlert, OpsAlertSink, configure_default_sink


class _RecordingSink(OpsAlertSink):
    def __init__(self) -> None:
        super().__init__(chat_id=None, log_path=None, sender=None)
        self.events: list[OpsAlert] = []

    def emit(self, alert: OpsAlert) -> None:  # type: ignore[override]
        self.events.append(alert)


class DaemonOpsAlertsTests(unittest.TestCase):
    WORK_ID = "ops-alerts-demo"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self._sink = _RecordingSink()
        self._previous_sink = ops_alerts._default_sink  # noqa: SLF001 — test hook
        configure_default_sink(self._sink)

    def tearDown(self) -> None:
        if self._previous_sink is None:
            ops_alerts._default_sink = None  # noqa: SLF001 — test hook
        else:
            configure_default_sink(self._previous_sink)

    # ------------------------------------------------------------------
    # Lock lifecycle alerts

    def test_stale_lock_recovery_emits_warning(self) -> None:
        write_daemon_lock(
            self.root,
            self.WORK_ID,
            {
                "kind": "autonomous-daemon-lock",
                "version": "v1",
                "work_id": self.WORK_ID,
                "mode": "autonomous-full",
                "root_dir": str(self.root),
                "pid": 999999,
                "started_at": "2026-04-18T10:00:00+00:00",
                "heartbeat_at": "2026-04-18T10:00:00+00:00",
            },
        )
        result = acquire_daemon_lock(self.root, self.WORK_ID, mode="autonomous-full", pid=os.getpid())
        self.addCleanup(release_daemon_lock, self.root, self.WORK_ID)

        self.assertTrue(result["acquired"])
        self.assertTrue(result["recovered_stale_lock"])

        codes = [alert.code for alert in self._sink.events]
        self.assertIn("daemon/stale-lock-recovered", codes)
        recovered = next(alert for alert in self._sink.events if alert.code == "daemon/stale-lock-recovered")
        self.assertEqual(recovered.severity, "warning")
        self.assertEqual(recovered.work_id, self.WORK_ID)
        self.assertEqual(recovered.details["previous_pid"], 999999)
        self.assertEqual(recovered.details["owner_pid"], os.getpid())

    def test_already_running_lock_emits_blocked_warning(self) -> None:
        first = acquire_daemon_lock(self.root, self.WORK_ID, mode="autonomous-full", pid=os.getpid())
        self.addCleanup(release_daemon_lock, self.root, self.WORK_ID)
        self.assertTrue(first["acquired"])

        blocked = acquire_daemon_lock(
            self.root,
            self.WORK_ID,
            mode="autonomous-full",
            pid=os.getpid() + 100000,
        )
        self.assertFalse(blocked["acquired"])

        codes = [alert.code for alert in self._sink.events]
        self.assertIn("daemon/lock-blocked", codes)
        event = next(alert for alert in self._sink.events if alert.code == "daemon/lock-blocked")
        self.assertEqual(event.severity, "warning")
        self.assertEqual(event.details["owner_pid"], os.getpid() + 100000)

    def test_reentry_by_same_pid_does_not_spam_alerts(self) -> None:
        first = acquire_daemon_lock(self.root, self.WORK_ID, mode="autonomous-full", pid=os.getpid())
        self.addCleanup(release_daemon_lock, self.root, self.WORK_ID)
        self.assertTrue(first["acquired"])

        again = acquire_daemon_lock(self.root, self.WORK_ID, mode="autonomous-full", pid=os.getpid())
        self.assertTrue(again["acquired"])
        self.assertFalse(again["recovered_stale_lock"])

        codes = [alert.code for alert in self._sink.events]
        self.assertNotIn("daemon/stale-lock-recovered", codes)
        self.assertNotIn("daemon/lock-blocked", codes)


class GuardsConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env: dict[str, Any] = {}
        for key in ("DAEMON_STUCK_AFTER_MINUTES",):
            self._saved_env[key] = os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_resolve_explicit_value_wins_over_env(self) -> None:
        os.environ["DAEMON_STUCK_AFTER_MINUTES"] = "15"
        self.assertEqual(_resolve_stuck_after_minutes(45), 45)
        self.assertIsNone(_resolve_stuck_after_minutes(0))
        self.assertIsNone(_resolve_stuck_after_minutes(-3))

    def test_resolve_falls_back_to_env(self) -> None:
        os.environ["DAEMON_STUCK_AFTER_MINUTES"] = "20"
        self.assertEqual(_resolve_stuck_after_minutes(None), 20)

    def test_resolve_env_invalid_is_ignored(self) -> None:
        os.environ["DAEMON_STUCK_AFTER_MINUTES"] = "not-a-number"
        self.assertIsNone(_resolve_stuck_after_minutes(None))

    def test_build_foreground_guards_sets_limits(self) -> None:
        guards = _build_foreground_guards(max_runtime_minutes=60, stuck_after_minutes=5)
        self.assertGreaterEqual(guards.timeout.limit, timedelta(minutes=60))
        self.assertEqual(guards.stuck.stuck_after, timedelta(minutes=5))

    def test_build_foreground_guards_default_stuck_to_runtime(self) -> None:
        guards = _build_foreground_guards(max_runtime_minutes=90, stuck_after_minutes=None)
        self.assertEqual(guards.stuck.stuck_after, timedelta(minutes=90))


if __name__ == "__main__":
    unittest.main()
