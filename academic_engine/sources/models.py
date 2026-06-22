"""Canonical data model for workspace source graph.

Everything that flows between connectors, the verifier, the repair
kernel and the evidence cartographer goes through these dataclasses.

No I/O, no network — pure data so it is easy to test and serialise.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


class SourceKind(enum.StrEnum):
    """Narrow, workspace-specific taxonomy.

    The kind drives verification policy: ``statute`` / ``case`` /
    ``regulator-guidance`` must reach a primary source, ``scholarly``
    and ``monograph`` only need a confidence-weighted citation.
    """

    STATUTE = "statute"
    CASE = "case"
    REGULATOR_GUIDANCE = "regulator-guidance"
    STATISTICS = "statistics"
    SCHOLARLY = "scholarly"
    MONOGRAPH = "monograph"
    WEB_SECONDARY = "web-secondary"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, value: str | SourceKind | None) -> SourceKind:
        if value is None:
            return cls.UNKNOWN
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


class VerificationStatus(enum.StrEnum):
    """Output of the source verifier for a given claim."""

    CURRENT = "current"
    STALE = "stale"
    OBSOLETE = "obsolete"
    PRIMARY_MISSING = "primary-missing"
    UNVERIFIABLE = "unverifiable"
    TEST_ONLY = "test-only"
    UNKNOWN = "unknown"


# Kinds where a missing primary or an obsolete citation must block
# ``submission-ready`` via a repair_kernel blocker.
PRIMARY_REQUIRED_KINDS: tuple[SourceKind, ...] = (
    SourceKind.STATUTE,
    SourceKind.CASE,
    SourceKind.REGULATOR_GUIDANCE,
)


@dataclass(frozen=True)
class AccessProvenance:
    """How we obtained a source: which connector, when, with what HTTP status."""

    connector: str
    retrieved_at: datetime
    canonical_url: str
    http_status: int | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["retrieved_at"] = self.retrieved_at.isoformat()
        return payload


@dataclass(frozen=True)
class Source:
    """Canonical source record.

    ``identifier`` must be stable across runs (use e.g. ``pravo-gov-ru:572-FZ``
    or ``sudact:vs-rf:12345``). This is what the verifier uses for dedup
    and cache lookups.
    """

    identifier: str
    kind: SourceKind
    title: str
    authors: tuple[str, ...] = ()
    canonical_url: str = ""
    issued_on: date | None = None
    effective_on: date | None = None
    amended_on: date | None = None
    edition_label: str = ""
    content_hash: str = ""
    language: str = "ru"
    provenance: AccessProvenance | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "identifier": self.identifier,
            "kind": self.kind.value,
            "title": self.title,
            "authors": list(self.authors),
            "canonical_url": self.canonical_url,
            "language": self.language,
            "edition_label": self.edition_label,
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
        }
        if self.issued_on:
            payload["issued_on"] = self.issued_on.isoformat()
        if self.effective_on:
            payload["effective_on"] = self.effective_on.isoformat()
        if self.amended_on:
            payload["amended_on"] = self.amended_on.isoformat()
        if self.provenance:
            payload["provenance"] = self.provenance.to_dict()
        return payload

    @staticmethod
    def content_hash_for(body: str) -> str:
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FetchResult:
    """Outcome of calling a connector."""

    source: Source | None
    raw_body: str = ""
    cached: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.source is not None and not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict() if self.source else None,
            "cached": self.cached,
            "error": self.error,
            "raw_body_len": len(self.raw_body),
        }


@dataclass(frozen=True)
class Claim:
    """Normalised claim coming out of the evidence cartographer.

    The verifier turns ``(claim, source)`` pairs into
    :class:`VerificationRecord` with a deterministic status.
    """

    claim_id: str
    text: str
    expected_kind: SourceKind
    cited_source_identifier: str | None = None
    cited_as_of: date | None = None
    importance: str = "normal"
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "claim_id": self.claim_id,
            "text": self.text,
            "expected_kind": self.expected_kind.value,
            "importance": self.importance,
            "context": self.context,
        }
        if self.cited_source_identifier:
            payload["cited_source_identifier"] = self.cited_source_identifier
        if self.cited_as_of:
            payload["cited_as_of"] = self.cited_as_of.isoformat()
        return payload


@dataclass(frozen=True)
class VerificationRecord:
    """Per-claim verdict produced by the verifier."""

    claim_id: str
    status: VerificationStatus
    source_identifier: str | None
    checked_at: datetime
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "claim_id": self.claim_id,
            "status": self.status.value,
            "checked_at": self.checked_at.isoformat(),
            "reason": self.reason,
            "details": dict(self.details),
        }
        if self.source_identifier:
            payload["source_identifier"] = self.source_identifier
        return payload

    @property
    def is_blocking_primary(self) -> bool:
        """True if the record must create a ``primary-support`` blocker."""
        return self.status in (VerificationStatus.PRIMARY_MISSING, VerificationStatus.OBSOLETE)

    @property
    def is_blocking(self) -> bool:
        """True when the verification verdict cannot support final readiness."""
        return self.status in (
            VerificationStatus.PRIMARY_MISSING,
            VerificationStatus.OBSOLETE,
            VerificationStatus.UNVERIFIABLE,
            VerificationStatus.TEST_ONLY,
        )
