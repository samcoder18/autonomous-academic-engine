"""Concrete source connectors.

Every connector has two modes:

- **live**: issues an HTTPS call via :class:`HttpClient`. Enabled when
  the matching env var (e.g. ``SOURCES_PRAVO_GOV_ENABLE=1``) is set.
- **stub**: returns a deterministic fixture for offline/CI runs. This
  is the default so the whole workspace stays green without any
  outbound traffic.

The stub mode is **not** used for production submissions — the verifier
treats stub-sourced data with ``AccessProvenance(connector='...')``
and rejects claims whose source is stub-only for
``submission-ready`` gating.
"""

from .cbr_ru import CbrRuConnector
from .cyberleninka import CyberleninkaConnector
from .elibrary import ELibraryConnector
from .pravo_gov_ru import PravoGovRuConnector
from .semantic_scholar import SemanticScholarConnector
from .sudact_ru import SudactRuConnector
from .vak_gov import VakGovConnector
from .web_fallback import WebFallbackConnector

__all__ = [
    "CbrRuConnector",
    "CyberleninkaConnector",
    "ELibraryConnector",
    "PravoGovRuConnector",
    "SemanticScholarConnector",
    "SudactRuConnector",
    "VakGovConnector",
    "WebFallbackConnector",
]


def default_registry():
    """Return a :class:`ConnectorRegistry` populated with stub-mode connectors."""
    from ..registry import ConnectorRegistry

    registry = ConnectorRegistry()
    registry.register(PravoGovRuConnector())
    registry.register(SudactRuConnector())
    registry.register(CbrRuConnector())
    registry.register(VakGovConnector())
    registry.register(ELibraryConnector())
    registry.register(SemanticScholarConnector())
    registry.register(CyberleninkaConnector())
    registry.register(WebFallbackConnector())
    return registry
