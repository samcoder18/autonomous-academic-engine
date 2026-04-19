from __future__ import annotations

import unittest
from unittest.mock import patch

from telegram_console.sources.connectors import default_registry
from telegram_console.sources.connectors._base import BaseConnector
from telegram_console.sources.connectors.pravo_gov_ru import PravoGovRuConnector
from telegram_console.sources.connectors.semantic_scholar import SemanticScholarConnector
from telegram_console.sources.http_client import HttpClient, HttpResponse
from telegram_console.sources.models import SourceKind


class _RecordingTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[str] = []

    def __call__(self, url, options):
        self.calls.append(url)
        return self.response


class StubModeTests(unittest.TestCase):
    def test_pravo_gov_ru_stub_has_572_fz(self) -> None:
        connector = PravoGovRuConnector()
        result = connector.fetch("упомянем 572-ФЗ в запросе")
        self.assertTrue(result.ok)
        assert result.source is not None
        self.assertEqual(result.source.identifier, "pravo-gov-ru:572-FZ")
        self.assertEqual(result.source.kind, SourceKind.STATUTE)
        self.assertEqual(result.source.provenance.connector, "pravo_gov_ru")
        self.assertEqual(result.source.provenance.notes, "stub-mode")

    def test_pravo_gov_ru_stub_miss_returns_error(self) -> None:
        connector = PravoGovRuConnector()
        result = connector.fetch("nothing here")
        self.assertFalse(result.ok)
        self.assertIn("no stub", result.error)

    def test_default_registry_registers_all(self) -> None:
        registry = default_registry()
        self.assertEqual(
            sorted(registry.names()),
            [
                "cbr_ru",
                "cyberleninka",
                "elibrary",
                "pravo_gov_ru",
                "semantic_scholar",
                "sudact_ru",
                "vak_gov",
                "web_fallback",
            ],
        )

    def test_registry_dispatch_uses_stubs(self) -> None:
        registry = default_registry()
        result = registry.dispatch(query="152-ФЗ", kind=SourceKind.STATUTE)
        self.assertTrue(result.ok)
        assert result.source is not None
        self.assertEqual(result.source.identifier, "pravo-gov-ru:152-FZ")


class LiveModeTests(unittest.TestCase):
    def test_pravo_gov_ru_live_call(self) -> None:
        transport = _RecordingTransport(HttpResponse(status=200, body="<html>572-ФЗ</html>"))
        http = HttpClient(transport=transport)
        connector = PravoGovRuConnector(http=http)
        with patch.dict("os.environ", {"SOURCES_PRAVO_GOV_ENABLE": "1"}):
            result = connector.fetch("nonexistent")
        self.assertTrue(result.ok)
        self.assertEqual(len(transport.calls), 1)
        self.assertIn("pravo.gov.ru", transport.calls[0])
        assert result.source is not None
        self.assertTrue(result.source.content_hash)
        self.assertEqual(result.source.provenance.notes, "live")

    def test_live_http_error_returned_as_fetch_error(self) -> None:
        transport = _RecordingTransport(HttpResponse(status=500, body=""))
        http = HttpClient(transport=transport)
        connector = PravoGovRuConnector(http=http)
        with patch.dict("os.environ", {"SOURCES_PRAVO_GOV_ENABLE": "1"}):
            result = connector.fetch("q")
        self.assertFalse(result.ok)
        self.assertIn("HTTP 500", result.error)

    def test_semantic_scholar_parses_json(self) -> None:
        payload = '{"data":[{"paperId":"abc","title":"T","url":"https://u","authors":[{"name":"A"}]}]}'
        transport = _RecordingTransport(HttpResponse(status=200, body=payload))
        http = HttpClient(transport=transport)
        connector = SemanticScholarConnector(http=http)
        with patch.dict("os.environ", {"SOURCES_SEMANTIC_SCHOLAR_ENABLE": "1"}):
            result = connector.fetch("test")
        self.assertTrue(result.ok)
        assert result.source is not None
        self.assertEqual(result.source.identifier, "semantic-scholar:abc")
        self.assertEqual(result.source.authors, ("A",))


class BaseConnectorErrorTests(unittest.TestCase):
    class _Boom(BaseConnector):
        name = "boom"
        supported_kinds = (SourceKind.STATUTE,)
        env_flag = "SOURCES_BOOM_ENABLE"

        def _live_fetch(self, query, *, hint):
            raise RuntimeError("nope")

    def test_live_exception_surfaced(self) -> None:
        with patch.dict("os.environ", {"SOURCES_BOOM_ENABLE": "1"}):
            result = self._Boom().fetch("x")
        self.assertFalse(result.ok)
        self.assertIn("boom:", result.error)
        self.assertIn("RuntimeError", result.error)


if __name__ == "__main__":
    unittest.main()
