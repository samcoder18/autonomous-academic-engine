"""Semantic Scholar public API — EN academic.

No API key needed for the public endpoint; rate-limited so the
dispatcher should pair it with a retry budget.
"""

from __future__ import annotations

import json
from urllib.parse import quote_plus

from ..models import FetchResult, Source, SourceKind
from ._base import BaseConnector, require_ok


class SemanticScholarConnector(BaseConnector):
    name = "semantic_scholar"
    supported_kinds = (SourceKind.SCHOLARLY,)
    env_flag = "SOURCES_SEMANTIC_SCHOLAR_ENABLE"

    _STUBS: dict[str, Source] = {}

    def _default_stub_loader(self, query, hint):
        return self._STUBS.get(query.lower())

    def _live_fetch(self, query, *, hint):
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search?"
            f"query={quote_plus(query)}&limit=3&fields=title,url,year,externalIds,authors.name"
        )
        response = require_ok(self._http.get(url), connector=self.name)
        try:
            payload = json.loads(response.body)
            papers = payload.get("data") or []
            first = papers[0] if papers else {}
        except (json.JSONDecodeError, IndexError, KeyError):
            first = {}
        title = first.get("title") or f"semantic-scholar — {query}"
        canonical_url = first.get("url") or url
        authors = tuple(author.get("name", "") for author in (first.get("authors") or []))
        src = Source(
            identifier=f"semantic-scholar:{first.get('paperId') or abs(hash(query)) % (10**10)}",
            kind=SourceKind.SCHOLARLY,
            title=title,
            authors=authors,
            canonical_url=canonical_url,
            content_hash=Source.content_hash_for(response.body),
            metadata={"query": query},
            language="en",
        )
        return FetchResult(
            source=self._stamp_provenance(src, http_status=response.status, note="live"),
            raw_body=response.body,
        )
