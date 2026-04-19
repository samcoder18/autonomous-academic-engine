"""Canonical source model, connector registry and SQLite-backed cache.

This package replaces ad-hoc free-text web searches with a typed
``Source`` graph that the source-verifier skill can query
deterministically.

Entry points:

- :mod:`telegram_console.sources.models` — :class:`Source`,
  :class:`SourceKind`, :class:`FetchResult`, :class:`VerificationRecord`,
  :class:`AccessProvenance`.
- :mod:`telegram_console.sources.registry` — connector registry and
  dispatch helper.
- :mod:`telegram_console.sources.cache` — SQLite-backed cache with TTL
  and content-hash awareness.
- :mod:`telegram_console.sources.throttle` — per-connector retry budgets.
"""

from .cache import SourceCache
from .models import (
    AccessProvenance,
    FetchResult,
    Source,
    SourceKind,
    VerificationRecord,
    VerificationStatus,
)
from .registry import ConnectorRegistry, SourceConnector

__all__ = [
    "AccessProvenance",
    "ConnectorRegistry",
    "FetchResult",
    "Source",
    "SourceCache",
    "SourceConnector",
    "SourceKind",
    "VerificationRecord",
    "VerificationStatus",
]
