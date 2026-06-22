"""Last-resort connector.

Wraps whatever web-search is available at runtime (Codex ``--search``
or an external engine). Results are always labelled ``web-secondary``
and **must not** be accepted as primary authority by the verifier.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from ..models import FetchResult, Source, SourceKind
from ._base import BaseConnector, require_ok


class WebFallbackConnector(BaseConnector):
    name = "web_fallback"
    supported_kinds = (SourceKind.WEB_SECONDARY,)
    env_flag = "SOURCES_WEB_FALLBACK_ENABLE"

    _STUBS: dict[str, Source] = {}

    def _default_stub_loader(self, query, hint):
        return self._STUBS.get(query.lower())

    def _live_fetch(self, query, *, hint):
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        response = require_ok(self._http.get(url), connector=self.name)
        src = Source(
            identifier=f"web:{abs(hash(query)) % (10**10)}",
            kind=SourceKind.WEB_SECONDARY,
            title=f"web-fallback — {query}",
            canonical_url=url,
            content_hash=Source.content_hash_for(response.body),
            metadata={"query": query, "mode": "web-secondary"},
        )
        return FetchResult(
            source=self._stamp_provenance(src, http_status=response.status, note="live"),
            raw_body=response.body,
        )
