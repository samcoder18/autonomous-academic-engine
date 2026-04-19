"""cyberleninka.ru — открытые RU-академические статьи."""

from __future__ import annotations

from urllib.parse import quote_plus

from ..models import FetchResult, Source, SourceKind
from ._base import BaseConnector, require_ok


class CyberleninkaConnector(BaseConnector):
    name = "cyberleninka"
    supported_kinds = (SourceKind.SCHOLARLY,)
    env_flag = "SOURCES_CYBERLENINKA_ENABLE"

    _STUBS: dict[str, Source] = {}

    def _default_stub_loader(self, query, hint):
        return self._STUBS.get(query.lower())

    def _live_fetch(self, query, *, hint):
        url = f"https://cyberleninka.ru/search?q={quote_plus(query)}"
        response = require_ok(self._http.get(url), connector=self.name)
        src = Source(
            identifier=f"cyberleninka:{abs(hash(query)) % (10**10)}",
            kind=SourceKind.SCHOLARLY,
            title=f"cyberleninka — {query}",
            canonical_url=url,
            content_hash=Source.content_hash_for(response.body),
            language="ru",
            metadata={"query": query},
        )
        return FetchResult(
            source=self._stamp_provenance(src, http_status=response.status, note="live"),
            raw_body=response.body,
        )
