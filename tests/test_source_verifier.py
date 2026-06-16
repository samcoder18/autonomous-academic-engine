from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, date, datetime

from telegram_console.source_verifier import SourceVerifier
from telegram_console.sources.connectors._base import BaseConnector
from telegram_console.sources.models import (
    AccessProvenance,
    Claim,
    FetchResult,
    Source,
    SourceKind,
    VerificationStatus,
)
from telegram_console.sources.registry import ConnectorRegistry


def _make_connector(name: str, response: FetchResult, supported=(SourceKind.STATUTE,)):
    class _C(BaseConnector):
        pass

    c = _C()
    c.name = name
    c.supported_kinds = supported
    c.fetch = lambda query, *, hint=None: response  # type: ignore[method-assign]
    return c


def _fixed_now() -> datetime:
    return datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


def _live_source(
    *,
    kind: SourceKind = SourceKind.STATUTE,
    issued_on: date | None = date(2020, 1, 1),
    amended_on: date | None = None,
) -> Source:
    url = "https://official.example.test/source"
    return Source(
        identifier="s:1",
        kind=kind,
        title="t",
        canonical_url=url,
        issued_on=issued_on,
        amended_on=amended_on,
        content_hash=Source.content_hash_for("official body"),
        provenance=AccessProvenance(
            connector="fake",
            retrieved_at=datetime(2026, 4, 1, tzinfo=UTC),
            canonical_url=url,
            http_status=200,
            notes="live",
        ),
    )


class SourceVerifierTests(unittest.TestCase):
    def _registry_with(self, response: FetchResult, supported=(SourceKind.STATUTE,)) -> ConnectorRegistry:
        registry = ConnectorRegistry()
        registry.register(_make_connector("fake", response, supported=supported))
        return registry

    def test_current_when_source_present(self) -> None:
        src = _live_source()
        verifier = SourceVerifier(
            registry=self._registry_with(FetchResult(source=src, raw_body="ok")),
            now=_fixed_now,
        )
        claim = Claim(claim_id="c1", text="q", expected_kind=SourceKind.STATUTE)
        record = verifier.verify_claim(claim)
        self.assertEqual(record.status, VerificationStatus.CURRENT)
        self.assertEqual(record.source_identifier, "s:1")

    def test_primary_missing_when_connector_fails(self) -> None:
        verifier = SourceVerifier(
            registry=self._registry_with(FetchResult(source=None, error="timeout")),
            now=_fixed_now,
        )
        claim = Claim(claim_id="c1", text="q", expected_kind=SourceKind.STATUTE)
        record = verifier.verify_claim(claim)
        self.assertEqual(record.status, VerificationStatus.PRIMARY_MISSING)

    def test_primary_missing_when_only_web_secondary(self) -> None:
        web_src = Source(identifier="web:1", kind=SourceKind.WEB_SECONDARY, title="t")
        verifier = SourceVerifier(
            registry=self._registry_with(
                FetchResult(source=web_src, raw_body="x"),
                supported=(SourceKind.STATUTE, SourceKind.WEB_SECONDARY),
            ),
            now=_fixed_now,
        )
        claim = Claim(claim_id="c1", text="q", expected_kind=SourceKind.STATUTE)
        record = verifier.verify_claim(claim)
        self.assertEqual(record.status, VerificationStatus.PRIMARY_MISSING)

    def test_obsolete_when_cited_edition_is_superseded(self) -> None:
        src = _live_source(amended_on=date(2025, 3, 1))
        verifier = SourceVerifier(
            registry=self._registry_with(FetchResult(source=src, raw_body="ok")),
            now=_fixed_now,
        )
        claim = Claim(
            claim_id="c1",
            text="q",
            expected_kind=SourceKind.STATUTE,
            cited_as_of=date(2022, 1, 1),
        )
        record = verifier.verify_claim(claim)
        self.assertEqual(record.status, VerificationStatus.OBSOLETE)

    def test_unverifiable_when_missing_metadata_for_primary(self) -> None:
        src = _live_source(issued_on=None)
        verifier = SourceVerifier(
            registry=self._registry_with(FetchResult(source=src, raw_body="ok")),
            now=_fixed_now,
        )
        claim = Claim(claim_id="c1", text="q", expected_kind=SourceKind.STATUTE)
        record = verifier.verify_claim(claim)
        self.assertEqual(record.status, VerificationStatus.UNVERIFIABLE)

    def test_stub_provenance_is_test_only_and_blocking(self) -> None:
        src = _live_source()
        stub = replace(
            src,
            provenance=AccessProvenance(
                connector="fake",
                retrieved_at=datetime(2026, 4, 1, tzinfo=UTC),
                canonical_url=src.canonical_url,
                http_status=None,
                notes="stub-mode",
            ),
        )
        verifier = SourceVerifier(
            registry=self._registry_with(FetchResult(source=stub, raw_body="stub")),
            now=_fixed_now,
        )

        summary = verifier.verify_many([Claim(claim_id="stub", text="q", expected_kind=SourceKind.STATUTE)])

        self.assertEqual(summary.records[0].status, VerificationStatus.TEST_ONLY)
        self.assertTrue(summary.has_blocking)
        self.assertEqual(summary.blockers[0].code, "test-only-stub")

    def test_primary_live_readiness_requires_complete_provenance(self) -> None:
        valid = _live_source()
        cases = {
            "provenance": replace(valid, provenance=None),
            "canonical_url": replace(
                valid,
                provenance=AccessProvenance(
                    connector="fake",
                    retrieved_at=datetime(2026, 4, 1, tzinfo=UTC),
                    canonical_url="",
                    http_status=200,
                ),
            ),
            "http_status_2xx": replace(
                valid,
                provenance=AccessProvenance(
                    connector="fake",
                    retrieved_at=datetime(2026, 4, 1, tzinfo=UTC),
                    canonical_url=valid.canonical_url,
                    http_status=503,
                ),
            ),
            "retrieved_at": replace(
                valid,
                provenance=AccessProvenance(
                    connector="fake",
                    retrieved_at=None,  # type: ignore[arg-type]
                    canonical_url=valid.canonical_url,
                    http_status=200,
                ),
            ),
            "content_hash": replace(valid, content_hash=""),
        }

        for expected_gap, source in cases.items():
            with self.subTest(expected_gap=expected_gap):
                verifier = SourceVerifier(
                    registry=self._registry_with(FetchResult(source=source, raw_body="body")),
                    now=_fixed_now,
                )
                record = verifier.verify_claim(Claim(claim_id=expected_gap, text="q", expected_kind=SourceKind.STATUTE))
                self.assertEqual(record.status, VerificationStatus.UNVERIFIABLE)
                self.assertIn(expected_gap, record.details["missing_live_provenance"])

    def test_statistics_are_dynamic_and_require_live_provenance(self) -> None:
        source = Source(
            identifier="stats:1",
            kind=SourceKind.STATISTICS,
            title="Statistics",
            issued_on=date(2026, 1, 1),
        )
        verifier = SourceVerifier(
            registry=self._registry_with(
                FetchResult(source=source, raw_body="body"),
                supported=(SourceKind.STATISTICS,),
            ),
            now=_fixed_now,
        )
        record = verifier.verify_claim(Claim(claim_id="stats", text="q", expected_kind=SourceKind.STATISTICS))
        self.assertEqual(record.status, VerificationStatus.UNVERIFIABLE)

    def test_verify_many_produces_blockers(self) -> None:
        ok_src = _live_source()
        registry = ConnectorRegistry()
        registry.register(
            _make_connector(
                "ok",
                FetchResult(source=ok_src, raw_body="x"),
                supported=(SourceKind.STATUTE,),
            )
        )
        registry.register(
            _make_connector(
                "bad",
                FetchResult(source=None, error="nope"),
                supported=(SourceKind.CASE,),
            )
        )
        verifier = SourceVerifier(registry=registry, now=_fixed_now)
        summary = verifier.verify_many(
            [
                Claim(claim_id="ok", text="x", expected_kind=SourceKind.STATUTE),
                Claim(claim_id="bad", text="y", expected_kind=SourceKind.CASE),
            ]
        )
        self.assertTrue(summary.has_blocking)
        codes = [b.code for b in summary.blockers]
        self.assertIn("primary-missing-bad", codes)
        self.assertTrue(all(b.category == "primary-support" for b in summary.blockers))


if __name__ == "__main__":
    unittest.main()
