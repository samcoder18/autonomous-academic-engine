"""vak.minobrnauki.gov.ru — перечень ВАК и требования к диссертациям."""

from __future__ import annotations

from ..models import FetchResult, Source, SourceKind
from ._base import BaseConnector, require_ok


class VakGovConnector(BaseConnector):
    name = "vak_gov"
    supported_kinds = (SourceKind.REGULATOR_GUIDANCE,)
    env_flag = "SOURCES_VAK_ENABLE"

    _STUBS = {
        "перечень вак": Source(
            identifier="vak-gov:journal-list",
            kind=SourceKind.REGULATOR_GUIDANCE,
            title="Перечень рецензируемых научных изданий (ВАК)",
            canonical_url="https://vak.minobrnauki.gov.ru/main#tab=_tab:editions",
            language="ru",
            metadata={"source": "минобрнауки"},
        ),
    }

    def _default_stub_loader(self, query, hint):
        return self._STUBS.get(query.lower())

    def _live_fetch(self, query, *, hint):
        url = "https://vak.minobrnauki.gov.ru/main"
        response = require_ok(self._http.get(url), connector=self.name)
        src = Source(
            identifier="vak-gov:main",
            kind=SourceKind.REGULATOR_GUIDANCE,
            title="ВАК — главная страница",
            canonical_url=url,
            content_hash=Source.content_hash_for(response.body),
        )
        return FetchResult(
            source=self._stamp_provenance(src, http_status=response.status, note="live"),
            raw_body=response.body,
        )
