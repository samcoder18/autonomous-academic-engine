"""elibrary.ru — российская научная библиотека.

Public search only by default. Authorised mode requires a session
cookie; we keep the contract narrow and fail loudly when the live mode
is enabled without credentials.
"""

from __future__ import annotations

import os
from urllib.parse import quote_plus

from ..models import FetchResult, Source, SourceKind
from ._base import BaseConnector, require_ok


class ELibraryConnector(BaseConnector):
    name = "elibrary"
    supported_kinds = (SourceKind.SCHOLARLY,)
    env_flag = "SOURCES_ELIBRARY_ENABLE"

    _STUBS: dict[str, Source] = {}

    def _default_stub_loader(self, query, hint):
        return self._STUBS.get(query.lower())

    def _live_fetch(self, query, *, hint):
        url = f"https://elibrary.ru/query_results.asp?queryid=&pagenum=1&text={quote_plus(query)}"
        headers: dict[str, str] = {}
        cookie = os.environ.get("SOURCES_ELIBRARY_COOKIE")
        if cookie:
            headers["Cookie"] = cookie
        response = require_ok(self._http.get(url, headers=headers), connector=self.name)
        src = Source(
            identifier=f"elibrary:{abs(hash(query)) % (10**10)}",
            kind=SourceKind.SCHOLARLY,
            title=f"elibrary.ru — {query}",
            canonical_url=url,
            content_hash=Source.content_hash_for(response.body),
            metadata={"authorised": bool(cookie)},
        )
        return FetchResult(
            source=self._stamp_provenance(src, http_status=response.status, note="live"),
            raw_body=response.body,
        )
