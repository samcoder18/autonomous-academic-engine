"""Deterministic source verifier.

Transforms ``Claim -> VerificationRecord`` by comparing the expected
source to what the connector registry actually returned. The verifier
never calls an LLM; it is a pure gate that the repair kernel can trust.

Verification axes:

- **primary-missing**: expected primary source (``statute`` / ``case`` /
  ``regulator-guidance``) could not be fetched, or only a
  ``web-secondary`` surrogate was returned.
- **obsolete**: the canonical ``effective_on`` / ``amended_on`` date is
  after the date cited in the claim — the claim quotes a superseded
  edition.
- **stale**: connector data is older than its TTL; triggers a re-fetch
  recommendation but does not hard-block submission-ready.
- **unverifiable**: nothing to compare against (no canonical date, no
  edition); flagged for manual review.
- **current**: happy path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .repair_kernel import Blocker
from .sources.models import (
    PRIMARY_REQUIRED_KINDS,
    Claim,
    FetchResult,
    Source,
    SourceKind,
    VerificationRecord,
    VerificationStatus,
)
from .sources.registry import ConnectorRegistry


@dataclass(frozen=True)
class VerificationSummary:
    records: tuple[VerificationRecord, ...]
    blockers: tuple[Blocker, ...]

    @property
    def has_blocking(self) -> bool:
        return any(record.is_blocking_primary for record in self.records)

    def to_dict(self) -> dict[str, object]:
        return {
            "records": [record.to_dict() for record in self.records],
            "blockers": [blocker.to_dict() for blocker in self.blockers],
        }


class SourceVerifier:
    def __init__(
        self,
        *,
        registry: ConnectorRegistry,
        now: Callable[[], datetime] | None = None,
        stale_after: timedelta | None = None,
    ) -> None:
        self._registry = registry
        self._now = now or (lambda: datetime.now(UTC))
        self._stale_after = stale_after or timedelta(days=60)

    def verify_claim(self, claim: Claim) -> VerificationRecord:
        result = self._registry.dispatch(query=claim.text, kind=claim.expected_kind)
        return self._classify(claim, result)

    def verify_many(self, claims: Iterable[Claim]) -> VerificationSummary:
        records = [self.verify_claim(claim) for claim in claims]
        blockers = list(_records_to_blockers(records))
        return VerificationSummary(records=tuple(records), blockers=tuple(blockers))

    # ------------------------------------------------------------------

    def _classify(self, claim: Claim, result: FetchResult) -> VerificationRecord:
        checked_at = self._now()
        if not result.ok or result.source is None:
            status = (
                VerificationStatus.PRIMARY_MISSING
                if claim.expected_kind in PRIMARY_REQUIRED_KINDS
                else VerificationStatus.UNVERIFIABLE
            )
            return VerificationRecord(
                claim_id=claim.claim_id,
                status=status,
                source_identifier=None,
                checked_at=checked_at,
                reason=result.error or "connector returned no source",
                details={"expected_kind": claim.expected_kind.value},
            )

        source = result.source
        if claim.expected_kind in PRIMARY_REQUIRED_KINDS and source.kind == SourceKind.WEB_SECONDARY:
            return VerificationRecord(
                claim_id=claim.claim_id,
                status=VerificationStatus.PRIMARY_MISSING,
                source_identifier=source.identifier,
                checked_at=checked_at,
                reason="Only a web-secondary surrogate is available; primary source required.",
                details={"source_kind": source.kind.value},
            )

        if claim.cited_as_of and (source.amended_on or source.effective_on):
            redaction = source.amended_on or source.effective_on
            assert redaction is not None
            if redaction > claim.cited_as_of:
                return VerificationRecord(
                    claim_id=claim.claim_id,
                    status=VerificationStatus.OBSOLETE,
                    source_identifier=source.identifier,
                    checked_at=checked_at,
                    reason=(
                        f"Claim cites edition as of {claim.cited_as_of.isoformat()}, "
                        f"but current redaction is {redaction.isoformat()}."
                    ),
                    details={
                        "cited_as_of": claim.cited_as_of.isoformat(),
                        "current_redaction": redaction.isoformat(),
                    },
                )

        if _fetch_is_stale(source, now=checked_at, stale_after=self._stale_after):
            return VerificationRecord(
                claim_id=claim.claim_id,
                status=VerificationStatus.STALE,
                source_identifier=source.identifier,
                checked_at=checked_at,
                reason="Cached source older than stale-after threshold; refetch recommended.",
                details={"stale_after_s": int(self._stale_after.total_seconds())},
            )

        if claim.expected_kind in PRIMARY_REQUIRED_KINDS and not (
            source.issued_on or source.effective_on or source.edition_label
        ):
            return VerificationRecord(
                claim_id=claim.claim_id,
                status=VerificationStatus.UNVERIFIABLE,
                source_identifier=source.identifier,
                checked_at=checked_at,
                reason="Canonical edition/date metadata is missing; cannot confirm currency.",
                details={},
            )

        return VerificationRecord(
            claim_id=claim.claim_id,
            status=VerificationStatus.CURRENT,
            source_identifier=source.identifier,
            checked_at=checked_at,
            reason="Source available and no obsolescence detected.",
            details={"source_kind": source.kind.value},
        )


def _fetch_is_stale(source: Source, *, now: datetime, stale_after: timedelta) -> bool:
    if not source.provenance or source.provenance.notes == "stub-mode":
        return False
    retrieved = source.provenance.retrieved_at
    if retrieved.tzinfo is None:
        retrieved = retrieved.replace(tzinfo=UTC)
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    return (current - retrieved) > stale_after


def _records_to_blockers(records: Iterable[VerificationRecord]) -> Iterable[Blocker]:
    for record in records:
        if record.status == VerificationStatus.PRIMARY_MISSING:
            yield Blocker(
                category="primary-support",
                code=f"primary-missing-{record.claim_id}",
                message=(f"Claim {record.claim_id!r} lacks a primary source. Reason: {record.reason}"),
                repairable=True,
                blocks_statuses=("submission-ready",),
                details=record.to_dict(),
            )
        elif record.status == VerificationStatus.OBSOLETE:
            yield Blocker(
                category="dynamic-material",
                code=f"obsolete-citation-{record.claim_id}",
                message=(f"Claim {record.claim_id!r} cites a superseded edition. Reason: {record.reason}"),
                repairable=True,
                blocks_statuses=("submission-ready",),
                details=record.to_dict(),
            )
        elif record.status == VerificationStatus.UNVERIFIABLE:
            yield Blocker(
                category="verification",
                code=f"unverifiable-{record.claim_id}",
                message=(f"Claim {record.claim_id!r} could not be verified. Reason: {record.reason}"),
                repairable=True,
                details=record.to_dict(),
            )
