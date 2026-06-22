"""cbr.ru — нормативы и информационные письма Банка России.

Critical for ФЗ-572 because the ЦБ regulates операторов ЕБС via
separate положения и информационные письма, которые часто не
дублируются на pravo.gov.ru.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus

from ..models import FetchResult, Source, SourceKind
from ._base import BaseConnector, require_ok


class CbrRuConnector(BaseConnector):
    name = "cbr_ru"
    supported_kinds = (SourceKind.REGULATOR_GUIDANCE, SourceKind.STATUTE)
    env_flag = "SOURCES_CBR_ENABLE"

    _STUBS = {
        "положение 719-п": Source(
            identifier="cbr-ru:polozhenie-719-p",
            kind=SourceKind.REGULATOR_GUIDANCE,
            title="Положение Банка России от 17.04.2019 № 683-П (ред. от …) — операционная устойчивость",
            canonical_url="https://www.cbr.ru/Content/Document/File/94083/683-P.pdf",
            issued_on=date(2019, 4, 17),
            edition_label="действующая редакция",
            language="ru",
            metadata={"regulator": "Банк России"},
        ),
    }

    def _default_stub_loader(self, query, hint):
        return self._STUBS.get(query.lower())

    def _live_fetch(self, query, *, hint):
        url = f"https://www.cbr.ru/search/?text={quote_plus(query)}"
        response = require_ok(self._http.get(url), connector=self.name)
        src = Source(
            identifier=f"cbr-ru:{abs(hash(query)) % (10**10)}",
            kind=hint or SourceKind.REGULATOR_GUIDANCE,
            title=f"cbr.ru — {query}",
            canonical_url=url,
            content_hash=Source.content_hash_for(response.body),
            metadata={"query": query},
        )
        return FetchResult(
            source=self._stamp_provenance(src, http_status=response.status, note="live"),
            raw_body=response.body,
        )
