"""publication.pravo.gov.ru — федеральные акты.

Primary source for Russian statutes and subordinate acts. Live mode hits
the official publication portal; stub mode returns a small biometric-VKR
oriented fixture so CI stays green offline.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus

from ..models import FetchResult, Source, SourceKind
from ._base import BaseConnector, StubResponse, require_ok


class PravoGovRuConnector(BaseConnector):
    name = "pravo_gov_ru"
    supported_kinds = (SourceKind.STATUTE, SourceKind.REGULATOR_GUIDANCE)
    env_flag = "SOURCES_PRAVO_GOV_ENABLE"

    _STUBS = {
        "572-ФЗ": Source(
            identifier="pravo-gov-ru:572-FZ",
            kind=SourceKind.STATUTE,
            title=(
                "Федеральный закон от 29.12.2020 № 572-ФЗ «Об осуществлении идентификации "
                "и (или) аутентификации физических лиц с использованием биометрических персональных данных…»"
            ),
            canonical_url="http://publication.pravo.gov.ru/Document/View/0001202012290001",
            issued_on=date(2020, 12, 29),
            edition_label="первоначальная редакция",
            language="ru",
            metadata={"chamber": "federal", "type": "federal-law"},
        ),
        "152-ФЗ": Source(
            identifier="pravo-gov-ru:152-FZ",
            kind=SourceKind.STATUTE,
            title="Федеральный закон от 27.07.2006 № 152-ФЗ «О персональных данных»",
            canonical_url="http://publication.pravo.gov.ru/Document/View/0001200607270049",
            issued_on=date(2006, 7, 27),
            edition_label="действующая редакция",
            language="ru",
            metadata={"chamber": "federal", "type": "federal-law"},
        ),
    }

    def _default_stub_loader(self, query, hint):
        for key, src in self._STUBS.items():
            if key.lower() in query.lower():
                return StubResponse(source=src, raw_body=f"stub:pravo:{key}")
        return None

    def _live_fetch(self, query, *, hint):
        url = f"http://publication.pravo.gov.ru/SearchText?q={quote_plus(query)}"
        response = require_ok(self._http.get(url), connector=self.name)
        identifier = f"pravo-gov-ru:{abs(hash(query)) % (10**10)}"
        src = Source(
            identifier=identifier,
            kind=hint or SourceKind.STATUTE,
            title=f"pravo.gov.ru — {query}",
            canonical_url=url,
            content_hash=Source.content_hash_for(response.body),
            metadata={"query": query, "http_status": response.status},
        )
        return FetchResult(
            source=self._stamp_provenance(src, http_status=response.status, note="live"),
            raw_body=response.body,
        )
