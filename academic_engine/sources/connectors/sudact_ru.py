"""sudact.ru — агрегатор судебных актов.

Secondary but broadly accepted for case-law navigation. The verifier
must still confirm primary source (Верховный Суд РФ, Конституционный
Суд РФ, арбитражные инстанции) before accepting as ``current``.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from ..models import FetchResult, Source, SourceKind
from ._base import BaseConnector, require_ok


class SudactRuConnector(BaseConnector):
    name = "sudact_ru"
    supported_kinds = (SourceKind.CASE,)
    env_flag = "SOURCES_SUDACT_ENABLE"

    _STUBS: dict[str, Source] = {}

    def _default_stub_loader(self, query, hint):
        return self._STUBS.get(query.lower())

    def _live_fetch(self, query, *, hint):
        url = f"https://sudact.ru/regular/doc/?q={quote_plus(query)}"
        response = require_ok(self._http.get(url), connector=self.name)
        src = Source(
            identifier=f"sudact:{abs(hash(query)) % (10**10)}",
            kind=SourceKind.CASE,
            title=f"sudact.ru search — {query}",
            canonical_url=url,
            content_hash=Source.content_hash_for(response.body),
            metadata={"query": query},
        )
        return FetchResult(
            source=self._stamp_provenance(src, http_status=response.status, note="live"),
            raw_body=response.body,
        )
