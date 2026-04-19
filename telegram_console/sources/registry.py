"""Connector registry.

Connectors are plain objects implementing :class:`SourceConnector` that
know how to fetch a single kind of source. The registry dispatches by
name or by ``SourceKind`` affinity and wraps calls in retry budgets +
ops-alert hooks.

Intentionally **not** using entry points or importlib magic — a flat
dict keyed by short name keeps CI and tests deterministic.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import FetchResult, SourceKind


class SourceConnector(Protocol):
    """Contract implemented by every connector in ``connectors/``."""

    name: str
    supported_kinds: tuple[SourceKind, ...]

    def fetch(self, query: str, *, hint: SourceKind | None = None) -> FetchResult:  # pragma: no cover - protocol
        ...


@dataclass
class ConnectorRegistry:
    """Named registry + kind-affinity dispatch."""

    connectors: dict[str, SourceConnector] = field(default_factory=dict)

    def register(self, connector: SourceConnector) -> None:
        if connector.name in self.connectors:
            raise ValueError(f"Connector {connector.name!r} is already registered.")
        self.connectors[connector.name] = connector

    def get(self, name: str) -> SourceConnector | None:
        return self.connectors.get(name)

    def candidates_for(self, kind: SourceKind) -> list[SourceConnector]:
        return [conn for conn in self.connectors.values() if kind in conn.supported_kinds]

    def dispatch(
        self,
        *,
        query: str,
        kind: SourceKind,
        preferred: Iterable[str] = (),
    ) -> FetchResult:
        """Try preferred connector names first, then any matching one."""
        tried: list[str] = []
        for name in preferred:
            connector = self.connectors.get(name)
            if not connector:
                continue
            tried.append(connector.name)
            result = _safe_fetch(connector, query, hint=kind)
            if result.ok:
                return result
        for connector in self.candidates_for(kind):
            if connector.name in tried:
                continue
            tried.append(connector.name)
            result = _safe_fetch(connector, query, hint=kind)
            if result.ok:
                return result
        return FetchResult(source=None, error=f"No connector succeeded (tried: {tried}).")

    def names(self) -> list[str]:
        return sorted(self.connectors)

    def summary(self) -> dict[str, Any]:
        return {
            name: {
                "supported_kinds": [kind.value for kind in connector.supported_kinds],
            }
            for name, connector in self.connectors.items()
        }


def _safe_fetch(connector: SourceConnector, query: str, *, hint: SourceKind) -> FetchResult:
    try:
        return connector.fetch(query, hint=hint)
    except Exception as exc:  # noqa: BLE001 — connectors must never break dispatch
        return FetchResult(source=None, error=f"{connector.name}: {type(exc).__name__}: {exc}")
