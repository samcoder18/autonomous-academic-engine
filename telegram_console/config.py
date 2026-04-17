from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from .email_delivery import SmtpSettings


@dataclass(frozen=True)
class TelegramConsoleConfig:
    root_dir: Path
    token: str
    allowed_chat_id: int
    codex_bin: str | None = None
    codex_model: str | None = None
    poll_timeout: int = 30
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_security: str = "starttls"
    smtp_from_email: str | None = None
    smtp_from_name: str = "Академический штурман"
    smtp_to_email: str | None = None
    smtp_timeout_seconds: int = 30

    @property
    def bot_home_dir(self) -> Path:
        return self.root_dir

    @property
    def smtp_settings(self) -> SmtpSettings | None:
        if not (self.smtp_host and self.smtp_from_email and self.smtp_to_email):
            return None
        if bool(self.smtp_username) != bool(self.smtp_password):
            return None
        return SmtpSettings(
            host=self.smtp_host,
            port=self.smtp_port,
            username=self.smtp_username,
            password=self.smtp_password,
            security=self.smtp_security,
            from_email=self.smtp_from_email,
            from_name=self.smtp_from_name,
            to_email=self.smtp_to_email,
            timeout_seconds=self.smtp_timeout_seconds,
        )

    @classmethod
    def from_env(cls, root_dir: str | Path | None = None) -> "TelegramConsoleConfig":
        root = Path(root_dir or Path(__file__).resolve().parents[1]).resolve()
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required.")

        chat_raw = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
        if not chat_raw:
            raise RuntimeError("TELEGRAM_ALLOWED_CHAT_ID is required.")

        try:
            allowed_chat_id = int(chat_raw)
        except ValueError as exc:
            raise RuntimeError("TELEGRAM_ALLOWED_CHAT_ID must be an integer.") from exc

        poll_timeout = os.getenv("TELEGRAM_POLL_TIMEOUT", "30").strip() or "30"
        try:
            timeout = int(poll_timeout)
        except ValueError as exc:
            raise RuntimeError("TELEGRAM_POLL_TIMEOUT must be an integer.") from exc

        smtp_security = (os.getenv("SMTP_SECURITY", "starttls").strip() or "starttls").lower()
        if smtp_security not in {"starttls", "ssl", "none"}:
            raise RuntimeError("SMTP_SECURITY must be one of: starttls, ssl, none.")

        return cls(
            root_dir=root,
            token=token,
            allowed_chat_id=allowed_chat_id,
            codex_bin=os.getenv("CODEX_BIN", "").strip() or None,
            codex_model=os.getenv("CODEX_MODEL", "").strip() or None,
            poll_timeout=max(1, timeout),
            smtp_host=os.getenv("SMTP_HOST", "").strip() or None,
            smtp_port=_parse_int_env("SMTP_PORT", 587),
            smtp_username=os.getenv("SMTP_USERNAME", "").strip() or None,
            smtp_password=os.getenv("SMTP_PASSWORD", "").strip() or None,
            smtp_security=smtp_security,
            smtp_from_email=os.getenv("SMTP_FROM_EMAIL", "").strip() or None,
            smtp_from_name=os.getenv("SMTP_FROM_NAME", "").strip() or "Академический штурман",
            smtp_to_email=os.getenv("SMTP_TO_EMAIL", "").strip() or None,
            smtp_timeout_seconds=max(1, _parse_int_env("SMTP_TIMEOUT_SECONDS", 30)),
        )


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip() or str(default)
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
