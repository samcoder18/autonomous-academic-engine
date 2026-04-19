"""Minimal pluggable HTTP client for source connectors.

Design choices:

- stdlib ``urllib`` + strict timeout;
- ``User-Agent`` identifies the workspace so gov portals can throttle us
  responsibly;
- an injected ``transport`` callable so tests can short-circuit the
  network entirely — this is the default in CI.

No HTML parsing here: each connector owns its own parser.
"""

from __future__ import annotations

import gzip
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib import error, request

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "legal-academic-workspace/1.0 (+https://example.org/legal-academic-workspace; contact=workspace-admin@example.org)"
)
DEFAULT_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


Transport = Callable[[str, dict[str, Any]], HttpResponse]


def _urllib_transport(url: str, options: dict[str, Any]) -> HttpResponse:
    headers = options.get("headers") or {}
    timeout = options.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    req = request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT, **headers})
    try:
        with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 — controlled URLs from connectors
            raw = response.read()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            if response_headers.get("content-encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            charset = response.headers.get_content_charset() or "utf-8"
            return HttpResponse(
                status=response.status,
                body=raw.decode(charset, errors="replace"),
                headers=response_headers,
            )
    except error.HTTPError as exc:
        return HttpResponse(status=exc.code, body=exc.read().decode("utf-8", errors="replace"), headers={})
    except error.URLError as exc:
        logger.warning("http transport url error: %s %s", url, exc)
        return HttpResponse(status=0, body=str(exc), headers={})


class HttpClient:
    """Lean client that can be swapped out in tests via ``transport``."""

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport or _urllib_transport
        self._default_timeout = default_timeout

    def get(self, url: str, *, headers: dict[str, str] | None = None, timeout: float | None = None) -> HttpResponse:
        options: dict[str, Any] = {
            "headers": headers or {},
            "timeout": timeout or self._default_timeout,
        }
        try:
            return self._transport(url, options)
        except Exception as exc:  # noqa: BLE001 — transport errors are surfaced as HttpResponse
            logger.warning("http transport failed: %s %s", url, exc)
            return HttpResponse(status=0, body=str(exc))
