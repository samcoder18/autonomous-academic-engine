from __future__ import annotations

from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from html import escape
from pathlib import Path
import smtplib
import ssl


DOCX_MIME_TYPE = (
    "application",
    "vnd.openxmlformats-officedocument.wordprocessingml.document",
)


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    from_email: str
    to_email: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    security: str = "starttls"
    from_name: str = "Академический штурман"
    timeout_seconds: int = 30


class EmailDeliveryError(RuntimeError):
    """Raised when email delivery fails."""


class SmtpDocxSender:
    def __init__(self, settings: SmtpSettings):
        self.settings = settings

    @property
    def recipient_email(self) -> str:
        return self.settings.to_email

    def build_export_message(self, file_path: str | Path, artifact_kind: str) -> EmailMessage:
        path = Path(file_path)
        if not path.exists():
            raise EmailDeliveryError(f"Не нашла DOCX для отправки: {path}")

        message = EmailMessage()
        message["Subject"] = f"Готовый DOCX: {path.name}"
        message["From"] = Address(
            display_name=self.settings.from_name,
            username=self.settings.from_email.partition("@")[0],
            domain=self.settings.from_email.partition("@")[2],
        )
        message["To"] = self.settings.to_email
        message.set_content(self._build_text_body(path.name, artifact_kind))
        message.add_alternative(self._build_html_body(path.name, artifact_kind), subtype="html")
        message.add_attachment(
            path.read_bytes(),
            maintype=DOCX_MIME_TYPE[0],
            subtype=DOCX_MIME_TYPE[1],
            filename=path.name,
        )
        return message

    def send_export(self, file_path: str | Path, artifact_kind: str) -> None:
        message = self.build_export_message(file_path, artifact_kind)
        try:
            with self._open_client() as client:
                if self.settings.username and self.settings.password:
                    client.login(self.settings.username, self.settings.password)
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError(f"Не получилось отправить письмо: {exc}") from exc

    def _open_client(self) -> smtplib.SMTP:
        if self.settings.security == "ssl":
            return smtplib.SMTP_SSL(
                self.settings.host,
                self.settings.port,
                timeout=self.settings.timeout_seconds,
            )

        client = smtplib.SMTP(
            self.settings.host,
            self.settings.port,
            timeout=self.settings.timeout_seconds,
        )
        client.ehlo()
        if self.settings.security == "starttls":
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        return client

    def _build_text_body(self, file_name: str, artifact_kind: str) -> str:
        return "\n".join(
            [
                "Здравствуйте!",
                "",
                "Готовый DOCX уже подготовлен и приложен к этому письму.",
                "",
                f"Тип результата: {artifact_kind}",
                f"Файл: {file_name}",
                "",
                "Пусть финальная версия принесет спокойную вычитку и уверенную подачу.",
                "",
                "С теплом,",
                self.settings.from_name,
            ]
        )

    def _build_html_body(self, file_name: str, artifact_kind: str) -> str:
        from_name = escape(self.settings.from_name)
        safe_file_name = escape(file_name)
        safe_artifact_kind = escape(artifact_kind)
        return f"""\
<html>
  <body style="margin:0;padding:24px;background:#f3ede4;font-family:Georgia,'Times New Roman',serif;color:#33261f;">
    <div style="max-width:640px;margin:0 auto;background:#fffaf3;border:1px solid #e3d5c5;border-radius:18px;padding:28px 32px;">
      <p style="margin:0 0 12px;font-size:12px;letter-spacing:0.16em;text-transform:uppercase;color:#8c6e57;">Академический workflow</p>
      <h1 style="margin:0 0 18px;font-size:30px;line-height:1.2;color:#2a1e18;">Здравствуйте!</h1>
      <p style="margin:0 0 14px;font-size:17px;line-height:1.7;">Готовый DOCX уже подготовлен и приложен к этому письму.</p>
      <div style="margin:18px 0;padding:16px 18px;background:#f8efe4;border-radius:14px;">
        <p style="margin:0 0 8px;font-size:16px;line-height:1.6;"><strong>Тип результата:</strong> {safe_artifact_kind}</p>
        <p style="margin:0;font-size:16px;line-height:1.6;"><strong>Файл:</strong> {safe_file_name}</p>
      </div>
      <p style="margin:0 0 14px;font-size:17px;line-height:1.7;">Пусть финальная версия принесет спокойную вычитку и уверенную подачу.</p>
      <p style="margin:22px 0 0;font-size:17px;line-height:1.7;">С теплом,<br>{from_name}</p>
    </div>
  </body>
</html>
"""
