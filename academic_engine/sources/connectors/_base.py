"""Base connector class shared by every concrete source integration."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..http_client import HttpClient, HttpResponse
from ..models import AccessProvenance, FetchResult, Source, SourceKind


@dataclass
class StubResponse:
    """Deterministic canned reply for offline/CI runs."""

    source: Source
    raw_body: str = "STUBBED"


class BaseConnector:
    """Common machinery: stub/live switch, provenance stamping, error wrapping."""

    name: str = "base"
    supported_kinds: tuple[SourceKind, ...] = ()
    env_flag: str = ""

    def __init__(
        self,
        *,
        http: HttpClient | None = None,
        stub_loader: Callable[[str, SourceKind | None], StubResponse | None] | None = None,
    ) -> None:
        self._http = http or HttpClient()
        self._stub_loader = stub_loader or self._default_stub_loader

    @property
    def is_live(self) -> bool:
        if not self.env_flag:
            return False
        return os.environ.get(self.env_flag, "").lower() in ("1", "true", "yes")

    def fetch(self, query: str, *, hint: SourceKind | None = None) -> FetchResult:
        if not self.is_live:
            stub = self._stub_loader(query, hint)
            if stub is None:
                return FetchResult(source=None, error=f"{self.name}: no stub for query {query!r}")
            return FetchResult(
                source=self._stamp_provenance(stub.source, http_status=None, note="stub-mode"),
                raw_body=stub.raw_body,
                cached=False,
            )
        try:
            return self._live_fetch(query, hint=hint)
        except Exception as exc:  # noqa: BLE001 — surface every error as FetchResult
            return FetchResult(source=None, error=f"{self.name}: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # Subclasses override either or both of these:

    def _live_fetch(self, query: str, *, hint: SourceKind | None) -> FetchResult:
        return FetchResult(source=None, error=f"{self.name}: live mode not implemented yet")

    def _default_stub_loader(self, query: str, hint: SourceKind | None) -> StubResponse | None:
        return None

    # ------------------------------------------------------------------

    def _stamp_provenance(
        self,
        source: Source,
        *,
        http_status: int | None,
        note: str,
    ) -> Source:
        provenance = AccessProvenance(
            connector=self.name,
            retrieved_at=datetime.now(UTC),
            canonical_url=source.canonical_url,
            http_status=http_status,
            notes=note,
        )
        return Source(
            identifier=source.identifier,
            kind=source.kind,
            title=source.title,
            authors=source.authors,
            canonical_url=source.canonical_url,
            issued_on=source.issued_on,
            effective_on=source.effective_on,
            amended_on=source.amended_on,
            edition_label=source.edition_label,
            content_hash=source.content_hash or Source.content_hash_for(note),
            language=source.language,
            provenance=provenance,
            metadata=source.metadata,
        )


def response_error(response: HttpResponse, *, connector: str) -> str:
    if response.status == 0:
        return f"{connector}: transport failure — {response.body[:200]}"
    return f"{connector}: HTTP {response.status}"


def require_ok(response: HttpResponse, *, connector: str) -> HttpResponse:
    if not response.ok:
        raise RuntimeError(response_error(response, connector=connector))
    return response


def _dict_loader(mapping: dict[str, StubResponse]) -> Callable[[str, SourceKind | None], StubResponse | None]:
    """Helper that turns a simple mapping into a stub_loader."""

    def loader(query: str, hint: SourceKind | None) -> StubResponse | None:
        return mapping.get(query) or mapping.get(query.lower())

    return loader


def merge_metadata(*parts: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        if part:
            merged.update(part)
    return merged
