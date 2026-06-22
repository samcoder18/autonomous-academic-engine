"""Dedicated operational-alerts channel.

Decoupled from product notifications so that daemon-health events
(stale locks, missing secrets, stuck runs, failed connectors, dead
letters) do not compete with user-facing progress updates.

Configuration:

- ``OPS_ALERT_CHAT_ID`` — Telegram chat id to forward ops events to.
- ``OPS_ALERT_LOG_PATH`` — optional file path to tee alerts to; useful
  when the Telegram bot is down so that alerts still land on disk.

Delivery semantics:

- Best-effort: alert delivery failures are logged and swallowed. The
  process that raised the alert must never be blocked by a Telegram
  outage.
- Fail-closed on configuration: if neither ``OPS_ALERT_CHAT_ID`` nor
  ``OPS_ALERT_LOG_PATH`` is set, alerts are written to ``stderr`` so
  the operator still sees them during local runs.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AlertSeverity:
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


ALERT_SEVERITIES = (
    AlertSeverity.INFO,
    AlertSeverity.WARNING,
    AlertSeverity.ERROR,
    AlertSeverity.CRITICAL,
)


@dataclass(frozen=True)
class OpsAlert:
    """Structured ops event. Small enough to serialise, large enough to triage."""

    severity: str
    code: str
    message: str
    component: str = "workspace"
    work_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "component": self.component,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.work_id:
            payload["work_id"] = self.work_id
        if self.details:
            payload["details"] = dict(self.details)
        return payload

    def to_markdown(self) -> str:
        emoji = {
            AlertSeverity.INFO: "\u2139\ufe0f",
            AlertSeverity.WARNING: "\u26a0\ufe0f",
            AlertSeverity.ERROR: "\u274c",
            AlertSeverity.CRITICAL: "\U0001f6a8",
        }.get(self.severity, "")
        head = f"{emoji} *OPS {self.severity.upper()}* `{self.code}`"
        parts: list[str] = [head, f"component: `{self.component}`"]
        if self.work_id:
            parts.append(f"work: `{self.work_id}`")
        parts.append(self.message)
        if self.details:
            details_text = "\n".join(f"- {key}: `{value}`" for key, value in sorted(self.details.items()))
            parts.append(details_text)
        return "\n".join(parts)


class OpsAlertSink:
    """Pluggable sink. Default implementation honours env vars."""

    def __init__(
        self,
        *,
        chat_id: str | int | None = None,
        log_path: Path | None = None,
        sender: Callable[[str | int, str], None] | None = None,
    ) -> None:
        self._chat_id = chat_id
        self._log_path = log_path
        self._sender = sender

    def emit(self, alert: OpsAlert) -> None:
        serialised = json.dumps(alert.to_dict(), ensure_ascii=False, sort_keys=True)
        logger.log(_severity_to_log_level(alert.severity), "ops-alert %s", serialised)

        if self._log_path:
            try:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._log_path.open("a", encoding="utf-8") as fh:
                    fh.write(serialised + "\n")
            except OSError as exc:
                logger.warning("ops-alert log write failed: %s", exc)

        if self._chat_id and self._sender:
            try:
                self._sender(self._chat_id, alert.to_markdown())
            except Exception as exc:  # noqa: BLE001 — best-effort delivery
                logger.warning("ops-alert telegram delivery failed: %s", exc)

        if not self._chat_id and not self._log_path:
            print(f"[OPS:{alert.severity}] {alert.code} {alert.message}", file=sys.stderr)


_default_sink: OpsAlertSink | None = None


def configure_default_sink(sink: OpsAlertSink) -> None:
    global _default_sink
    _default_sink = sink


def default_sink() -> OpsAlertSink:
    global _default_sink
    if _default_sink is not None:
        return _default_sink
    chat_id_raw = os.environ.get("OPS_ALERT_CHAT_ID")
    chat_id: str | int | None
    if chat_id_raw:
        try:
            chat_id = int(chat_id_raw)
        except ValueError:
            chat_id = chat_id_raw
    else:
        chat_id = None

    log_path_raw = os.environ.get("OPS_ALERT_LOG_PATH")
    log_path = Path(log_path_raw).expanduser() if log_path_raw else None
    _default_sink = OpsAlertSink(chat_id=chat_id, log_path=log_path, sender=None)
    return _default_sink


def emit_alert(
    *,
    severity: str,
    code: str,
    message: str,
    component: str = "workspace",
    work_id: str | None = None,
    details: dict[str, Any] | None = None,
    sink: OpsAlertSink | None = None,
) -> OpsAlert:
    """Emit an alert through ``sink`` (falls back to the default sink).

    Returns the constructed :class:`OpsAlert` for callers that want to
    log or enrich it locally.
    """
    if severity not in ALERT_SEVERITIES:
        raise ValueError(f"Unknown severity: {severity!r}")
    alert = OpsAlert(
        severity=severity,
        code=code,
        message=message,
        component=component,
        work_id=work_id,
        details=details or {},
    )
    (sink or default_sink()).emit(alert)
    return alert


def _severity_to_log_level(severity: str) -> int:
    mapping = {
        AlertSeverity.INFO: logging.INFO,
        AlertSeverity.WARNING: logging.WARNING,
        AlertSeverity.ERROR: logging.ERROR,
        AlertSeverity.CRITICAL: logging.CRITICAL,
    }
    return mapping.get(severity, logging.INFO)
