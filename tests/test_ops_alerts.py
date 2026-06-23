from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr
from io import StringIO
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

    def test_emit_without_log_path_writes_to_stderr(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            alert = emit_alert(
                severity=AlertSeverity.INFO,
                code="heartbeat",
                message="ok",
                sink=OpsAlertSink(),
            )

        self.assertEqual(alert.code, "heartbeat")
        self.assertIn("[OPS:info] heartbeat ok", stderr.getvalue())

    def test_unknown_severity_rejected(self) -> None:
        with self.assertRaises(ValueError):
            emit_alert(severity="nope", code="x", message="y")


if __name__ == "__main__":
    unittest.main()
