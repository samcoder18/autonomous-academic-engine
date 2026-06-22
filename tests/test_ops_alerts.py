from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from academic_engine.ops_alerts import AlertSeverity, OpsAlertSink, emit_alert


class OpsAlertTests(unittest.TestCase):
    def test_emit_writes_to_log_file(self) -> None:
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "ops.log"
            sink = OpsAlertSink(log_path=log_path)
            emit_alert(
                severity=AlertSeverity.WARNING,
                code="daemon-stale-lock",
                message="Stale lock detected",
                component="autonomous_daemon",
                work_id="starter-work",
                details={"lock": "/tmp/x.lock"},
                sink=sink,
            )
            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["code"], "daemon-stale-lock")
            self.assertEqual(payload["severity"], "warning")
            self.assertEqual(payload["work_id"], "starter-work")
            self.assertEqual(payload["details"], {"lock": "/tmp/x.lock"})

    def test_emit_invokes_telegram_sender(self) -> None:
        delivered: list[tuple[str | int, str]] = []

        def fake_sender(chat_id: str | int, text: str) -> None:
            delivered.append((chat_id, text))

        sink = OpsAlertSink(chat_id=42, sender=fake_sender)
        emit_alert(
            severity=AlertSeverity.ERROR,
            code="connector-down",
            message="pravo.gov.ru returns 5xx",
            component="sources",
            sink=sink,
        )
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0][0], 42)
        self.assertIn("connector-down", delivered[0][1])
        self.assertIn("OPS ERROR", delivered[0][1])

    def test_emit_swallows_telegram_exceptions(self) -> None:
        def angry_sender(chat_id: str | int, text: str) -> None:
            raise RuntimeError("network down")

        sink = OpsAlertSink(chat_id=1, sender=angry_sender)
        alert = emit_alert(
            severity=AlertSeverity.INFO,
            code="heartbeat",
            message="ok",
            sink=sink,
        )
        self.assertEqual(alert.code, "heartbeat")

    def test_unknown_severity_rejected(self) -> None:
        with self.assertRaises(ValueError):
            emit_alert(severity="nope", code="x", message="y")


if __name__ == "__main__":
    unittest.main()
