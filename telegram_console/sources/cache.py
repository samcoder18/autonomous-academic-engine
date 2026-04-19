"""SQLite-backed cache for fetched sources.

Why SQLite, not the filesystem directly:

- atomic writes;
- indexed ``canonical_url`` lookups;
- built into Python stdlib (no new deps);
- easy to vacuum / introspect from the CLI.

TTL policy:

- default 24h for dynamic kinds (``statute``, ``case``,
  ``regulator-guidance``);
- 30 days for historical / static (``monograph``, ``scholarly``,
  ``statistics``);
- callers can override per call.

Content hash awareness:

- we store ``content_hash`` for every fetch so the verifier can detect
  a redaction change and invalidate downstream claims automatically.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import AccessProvenance, Source, SourceKind

_DEFAULT_TTL_DYNAMIC = timedelta(hours=24)
_DEFAULT_TTL_STATIC = timedelta(days=30)

_DYNAMIC_KINDS = {
    SourceKind.STATUTE,
    SourceKind.CASE,
    SourceKind.REGULATOR_GUIDANCE,
    SourceKind.STATISTICS,
}


def default_ttl_for(kind: SourceKind) -> timedelta:
    return _DEFAULT_TTL_DYNAMIC if kind in _DYNAMIC_KINDS else _DEFAULT_TTL_STATIC


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    identifier TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    raw_body TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sources_canonical_url ON sources(canonical_url);
CREATE INDEX IF NOT EXISTS ix_sources_kind ON sources(kind);
"""


class SourceCache:
    """Thin wrapper over sqlite3.

    Not thread-safe out of the box; wrap in a lock if the daemon starts
    multiplexing. For the current single-threaded runtime this is fine.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SourceCache:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------

    def get(self, identifier: str, *, now: datetime | None = None) -> Source | None:
        row = self._conn.execute(
            "SELECT payload_json, expires_at FROM sources WHERE identifier = ?",
            (identifier,),
        ).fetchone()
        if not row:
            return None
        payload_json, expires_at = row
        if _is_expired(expires_at, now=now):
            return None
        return _decode_source(json.loads(payload_json))

    def put(
        self,
        source: Source,
        raw_body: str,
        *,
        ttl: timedelta | None = None,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(UTC)
        effective_ttl = ttl or default_ttl_for(source.kind)
        expires_at = current + effective_ttl
        payload = json.dumps(source.to_dict(), ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO sources "
            "(identifier, kind, title, canonical_url, content_hash, payload_json, raw_body, fetched_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source.identifier,
                source.kind.value,
                source.title,
                source.canonical_url,
                source.content_hash,
                payload,
                raw_body,
                current.isoformat(),
                expires_at.isoformat(),
            ),
        )
        self._conn.commit()

    def invalidate(self, identifier: str) -> None:
        self._conn.execute("DELETE FROM sources WHERE identifier = ?", (identifier,))
        self._conn.commit()

    def all_identifiers(self) -> list[str]:
        rows = self._conn.execute("SELECT identifier FROM sources ORDER BY identifier").fetchall()
        return [row[0] for row in rows]

    def stats(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(UTC)
        rows = self._conn.execute("SELECT kind, expires_at FROM sources").fetchall()
        fresh = 0
        expired = 0
        by_kind: dict[str, int] = {}
        for kind, expires_at in rows:
            by_kind[kind] = by_kind.get(kind, 0) + 1
            if _is_expired(expires_at, now=current):
                expired += 1
            else:
                fresh += 1
        return {"total": len(rows), "fresh": fresh, "expired": expired, "by_kind": by_kind}


def _is_expired(expires_at_iso: str, *, now: datetime | None) -> bool:
    expires = datetime.fromisoformat(expires_at_iso)
    current = now or datetime.now(UTC)
    return current >= expires


def _decode_source(payload: dict[str, Any]) -> Source:
    provenance_raw = payload.get("provenance")
    provenance: AccessProvenance | None = None
    if isinstance(provenance_raw, dict):
        retrieved_at = provenance_raw.get("retrieved_at")
        if isinstance(retrieved_at, str):
            retrieved_dt = datetime.fromisoformat(retrieved_at)
        else:
            retrieved_dt = datetime.now(UTC)
        provenance = AccessProvenance(
            connector=str(provenance_raw.get("connector", "")),
            retrieved_at=retrieved_dt,
            canonical_url=str(provenance_raw.get("canonical_url", "")),
            http_status=provenance_raw.get("http_status"),
            notes=str(provenance_raw.get("notes", "")),
        )
    return Source(
        identifier=str(payload["identifier"]),
        kind=SourceKind.coerce(payload.get("kind")),
        title=str(payload.get("title", "")),
        authors=tuple(payload.get("authors") or ()),
        canonical_url=str(payload.get("canonical_url", "")),
        issued_on=_parse_date(payload.get("issued_on")),
        effective_on=_parse_date(payload.get("effective_on")),
        amended_on=_parse_date(payload.get("amended_on")),
        edition_label=str(payload.get("edition_label", "")),
        content_hash=str(payload.get("content_hash", "")),
        language=str(payload.get("language", "ru")),
        provenance=provenance,
        metadata=dict(payload.get("metadata") or {}),
    )


def _parse_date(value: Any) -> Any:
    if not value:
        return None
    from datetime import date

    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
