from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any
from urllib import error, parse, request


class TelegramApiError(RuntimeError):
    """Raised when the Telegram Bot API returns an error."""


class TelegramBotApi:
    def __init__(self, token: str):
        self.base_url = f"https://api.telegram.org/bot{token}/"

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._post_form("getUpdates", payload, timeout=timeout + 10)
        return result["result"]

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self._post_form("sendMessage", payload)["result"]

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self._post_form("editMessageText", payload)["result"]

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._post_form("answerCallbackQuery", payload)

    def send_document(
        self,
        chat_id: int,
        file_path: str | Path,
        *,
        caption: str | None = None,
    ) -> dict[str, Any]:
        path = Path(file_path).resolve()
        payload: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            payload["caption"] = caption
        return self._post_multipart("sendDocument", payload, "document", path)["result"]

    def _post_form(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        timeout: int = 30,
    ) -> dict[str, Any]:
        data = parse.urlencode(payload).encode("utf-8")
        req = request.Request(self.base_url + method, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        return self._perform(req, timeout=timeout)

    def _post_multipart(
        self,
        method: str,
        payload: dict[str, Any],
        file_field: str,
        file_path: Path,
    ) -> dict[str, Any]:
        boundary = f"----CodexTelegram{uuid.uuid4().hex}"
        data = self._encode_multipart(boundary, payload, file_field, file_path)
        req = request.Request(self.base_url + method, data=data, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        return self._perform(req, timeout=60)

    def _perform(self, req: request.Request, *, timeout: int) -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except TimeoutError as exc:
            raise TelegramApiError(f"Telegram API timeout: {exc}") from exc
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramApiError(f"Telegram API HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise TelegramApiError(f"Telegram API connection error: {exc}") from exc

        payload = json.loads(raw)
        if not payload.get("ok"):
            raise TelegramApiError(payload.get("description", "Unknown Telegram API error"))
        return payload

    def _encode_multipart(
        self,
        boundary: str,
        payload: dict[str, Any],
        file_field: str,
        file_path: Path,
    ) -> bytes:
        parts: list[bytes] = []
        boundary_bytes = boundary.encode("utf-8")

        for key, value in payload.items():
            parts.extend(
                [
                    b"--" + boundary_bytes + b"\r\n",
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )

        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        file_bytes = file_path.read_bytes()
        parts.extend(
            [
                b"--" + boundary_bytes + b"\r\n",
                (f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n').encode(),
                f"Content-Type: {mime_type}\r\n\r\n".encode(),
                file_bytes,
                b"\r\n",
                b"--" + boundary_bytes + b"--\r\n",
            ]
        )
        return b"".join(parts)
