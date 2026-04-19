from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram_console.sources import (
    AccessProvenance,
    ConnectorRegistry,
    FetchResult,
    Source,
    SourceCache,
    SourceKind,
)
from telegram_console.sources.cache import default_ttl_for


class _FakeConnector:
    def __init__(
        self,
        name: str,
        supported: tuple[SourceKind, ...],
        responder,
    ) -> None:
        self.name = name
        self.supported_kinds = supported
        self._responder = responder

    def fetch(self, query: str, *, hint=None) -> FetchResult:
        return self._responder(query, hint)


class SourceModelTests(unittest.TestCase):
    def test_source_kind_coercion(self) -> None:
        self.assertEqual(SourceKind.coerce("statute"), SourceKind.STATUTE)
        self.assertEqual(SourceKind.coerce("nope"), SourceKind.UNKNOWN)
        self.assertEqual(SourceKind.coerce(None), SourceKind.UNKNOWN)

    def test_content_hash_stable(self) -> None:
        self.assertEqual(Source.content_hash_for("abc"), Source.content_hash_for("abc"))
        self.assertNotEqual(Source.content_hash_for("abc"), Source.content_hash_for("abd"))

    def test_source_to_dict_roundtrip(self) -> None:
        src = Source(
            identifier="pravo-gov-ru:572-FZ",
            kind=SourceKind.STATUTE,
            title="О единой биометрической системе",
            canonical_url="https://pravo.gov.ru/572-fz",
            issued_on=date(2020, 12, 29),
            content_hash="abc",
        )
        payload = src.to_dict()
        self.assertEqual(payload["kind"], "statute")
        self.assertEqual(payload["issued_on"], "2020-12-29")


class RegistryTests(unittest.TestCase):
    def test_register_and_dispatch_by_kind(self) -> None:
        src = Source(identifier="x", kind=SourceKind.STATUTE, title="t")

        def ok_responder(query, hint):
            return FetchResult(source=src, raw_body="ok")

        registry = ConnectorRegistry()
        registry.register(_FakeConnector("pravo", (SourceKind.STATUTE,), ok_responder))
        result = registry.dispatch(query="572-FZ", kind=SourceKind.STATUTE)
        self.assertTrue(result.ok)
        self.assertEqual(result.source.identifier, "x")

    def test_dispatch_fallback_order(self) -> None:
        fail_src = FetchResult(source=None, error="boom")
        win_src = FetchResult(source=Source(identifier="y", kind=SourceKind.STATUTE, title="t"), raw_body="ok")
        registry = ConnectorRegistry()
        registry.register(_FakeConnector("a", (SourceKind.STATUTE,), lambda q, h: fail_src))
        registry.register(_FakeConnector("b", (SourceKind.STATUTE,), lambda q, h: win_src))
        result = registry.dispatch(query="x", kind=SourceKind.STATUTE, preferred=("a",))
        self.assertTrue(result.ok)
        self.assertEqual(result.source.identifier, "y")

    def test_dispatch_catches_connector_exceptions(self) -> None:
        def boom(q, h):
            raise RuntimeError("connector crashed")

        win = FetchResult(source=Source(identifier="z", kind=SourceKind.STATUTE, title="t"), raw_body="ok")

        registry = ConnectorRegistry()
        registry.register(_FakeConnector("bad", (SourceKind.STATUTE,), boom))
        registry.register(_FakeConnector("good", (SourceKind.STATUTE,), lambda q, h: win))
        result = registry.dispatch(query="x", kind=SourceKind.STATUTE)
        self.assertTrue(result.ok)

    def test_dispatch_reports_tried_connectors(self) -> None:
        registry = ConnectorRegistry()
        registry.register(
            _FakeConnector(
                "only",
                (SourceKind.STATUTE,),
                lambda q, h: FetchResult(source=None, error="nope"),
            )
        )
        result = registry.dispatch(query="x", kind=SourceKind.STATUTE)
        self.assertFalse(result.ok)
        self.assertIn("only", result.error)

    def test_duplicate_registration_raises(self) -> None:
        registry = ConnectorRegistry()
        registry.register(_FakeConnector("dup", (SourceKind.STATUTE,), lambda q, h: FetchResult(source=None)))
        with self.assertRaises(ValueError):
            registry.register(_FakeConnector("dup", (SourceKind.STATUTE,), lambda q, h: FetchResult(source=None)))


class SourceCacheTests(unittest.TestCase):
    def test_roundtrip_and_ttl(self) -> None:
        with TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "sources.sqlite"
            with SourceCache(cache_path) as cache:
                src = Source(
                    identifier="pravo-gov-ru:572-FZ",
                    kind=SourceKind.STATUTE,
                    title="572-FZ",
                    canonical_url="https://pravo.gov.ru/572",
                    content_hash="h1",
                    provenance=AccessProvenance(
                        connector="pravo_gov_ru",
                        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
                        canonical_url="https://pravo.gov.ru/572",
                        http_status=200,
                    ),
                )
                cache.put(src, raw_body="BODY", now=datetime(2026, 1, 1, tzinfo=UTC))
                fetched = cache.get("pravo-gov-ru:572-FZ", now=datetime(2026, 1, 1, 12, tzinfo=UTC))
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.identifier, src.identifier)
                self.assertEqual(fetched.provenance.connector, "pravo_gov_ru")
                # TTL for dynamic source is 24h; query 48h later -> expired.
                later = cache.get("pravo-gov-ru:572-FZ", now=datetime(2026, 1, 3, tzinfo=UTC))
                self.assertIsNone(later)

    def test_stats_and_invalidate(self) -> None:
        with TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "sources.sqlite"
            with SourceCache(cache_path) as cache:
                src = Source(identifier="a", kind=SourceKind.MONOGRAPH, title="Book")
                cache.put(src, raw_body="text")
                self.assertIn("a", cache.all_identifiers())
                stats = cache.stats()
                self.assertEqual(stats["total"], 1)
                cache.invalidate("a")
                self.assertEqual(cache.all_identifiers(), [])

    def test_default_ttl_per_kind(self) -> None:
        self.assertEqual(default_ttl_for(SourceKind.STATUTE), timedelta(hours=24))
        self.assertEqual(default_ttl_for(SourceKind.MONOGRAPH), timedelta(days=30))


if __name__ == "__main__":
    unittest.main()
