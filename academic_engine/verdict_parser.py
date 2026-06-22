"""Structured verdict parser.

Reads a fenced ``` ```verdict ... ``` ``` block from any artifact text,
validates it against :mod:`meta.schemas.verdict.schema.json` (embedded in
this module to avoid runtime filesystem dependency), and returns a
:class:`StructuredVerdict`.

Design goals:

- **Additive**: falls through to legacy regex-based extractors when a
  fenced block is absent or malformed. This keeps the existing test
  suite green and lets agents migrate at their own pace.
- **Deterministic**: a malformed block yields a ``verdict-format-invalid``
  blocker so that the next repair iteration is asked to emit a valid
  block rather than silently swallowing the failure.
- **Dependency-free**: no PyYAML / jsonschema packages; we use the
  standard ``json`` module and hand-rolled validation against the same
  taxonomy defined in :mod:`academic_engine.repair_kernel`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .repair_kernel import Blocker

VERDICT_VERSION = "1"

ALLOWED_LANES = ("thesis", "article")

ALLOWED_KINDS = (
    "submission-evaluator",
    "argument-critic",
    "citation-checker",
    "counterargument-critic",
    "style-editor",
    "structure-architect",
    "source-verifier",
    "evidence-cartographer",
    "finalizer",
    "repair-orchestrator",
    "originality-checker",
    "gost-linter",
    "docx-conformance",
)

ALLOWED_STATUSES = (
    "submission-ready",
    "strong-draft",
    "strong-draft-with-blockers",
    "ready-with-caveats",
    "blocked-primary-support",
    "blocked-runtime",
    "blocked-standards",
    "updated",
    "reviewed",
    "needs-repair",
)

ALLOWED_BLOCKER_CATEGORIES = (
    "artifact",
    "citation",
    "codex",
    "docx-conformance",
    "dynamic-material",
    "external",
    "gost-bibliography",
    "logic",
    "originality",
    "primary-support",
    "process",
    "review",
    "runtime",
    "standards",
    "standards-consistency",
    "verdict",
    "verification",
)

_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_FENCE_PATTERN = re.compile(
    r"```[ \t]*verdict[ \t]*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class StructuredVerdict:
    """Parsed, validated verdict block."""

    lane: str
    kind: str
    status: str
    summary: str = ""
    target: str | None = None
    blockers: tuple[Blocker, ...] = ()
    notes: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "verdict_version": VERDICT_VERSION,
            "lane": self.lane,
            "kind": self.kind,
            "status": self.status,
        }
        if self.target:
            payload["target"] = self.target
        if self.summary:
            payload["summary"] = self.summary
        if self.blockers:
            payload["blockers"] = [item.to_dict() for item in self.blockers]
        if self.notes:
            payload["notes"] = list(self.notes)
        if self.metrics:
            payload["metrics"] = dict(self.metrics)
        if self.source:
            payload["source"] = self.source
        return payload


@dataclass(frozen=True)
class VerdictParseError:
    """Returned when a fenced block is present but invalid.

    We do not raise: the caller turns this into a ``verdict`` blocker and
    lets the repair loop ask the agent to fix the format.
    """

    source: str
    code: str
    message: str
    raw_body: str = ""

    def to_blocker(self) -> Blocker:
        return Blocker(
            category="verdict",
            code=f"verdict-{self.code}",
            message=self.message,
            repairable=True,
            details={"source": self.source, "raw": self.raw_body[:1024]},
        )


def find_verdict_blocks(text: str) -> list[str]:
    """Return raw body strings of every fenced verdict block in ``text``."""
    if not text:
        return []
    return [match.group("body") for match in _FENCE_PATTERN.finditer(text)]


def parse_verdict(
    body: str,
    *,
    source: str = "",
) -> StructuredVerdict | VerdictParseError:
    """Parse and validate a single fenced verdict body."""
    cleaned = (body or "").strip()
    if not cleaned:
        return VerdictParseError(source=source, code="empty-body", message="Verdict block is empty.")

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return VerdictParseError(
            source=source,
            code="json-decode-error",
            message=f"Verdict block is not valid JSON: {exc.msg} (line {exc.lineno}).",
            raw_body=cleaned,
        )

    if not isinstance(payload, dict):
        return VerdictParseError(
            source=source,
            code="not-object",
            message="Verdict block must be a JSON object.",
            raw_body=cleaned,
        )
    return _validate_payload(payload, source=source, raw_body=cleaned)


def extract_structured_verdicts(
    artifact_texts: dict[str, str],
) -> tuple[tuple[StructuredVerdict, ...], tuple[VerdictParseError, ...]]:
    """Extract every valid verdict from ``artifact_texts``.

    Parameters
    ----------
    artifact_texts:
        Mapping ``artifact_name -> text`` (e.g. ``{"output": "...", "review": "..."}``).

    Returns
    -------
    (verdicts, errors)
        ``verdicts`` — successfully parsed blocks (in discovery order).
        ``errors`` — malformed blocks; each should be turned into a
        ``verdict-format-invalid`` blocker by the caller.
    """
    verdicts: list[StructuredVerdict] = []
    errors: list[VerdictParseError] = []
    for source_name, text in artifact_texts.items():
        for body in find_verdict_blocks(text):
            result = parse_verdict(body, source=source_name)
            if isinstance(result, StructuredVerdict):
                verdicts.append(result)
            else:
                errors.append(result)
    return tuple(verdicts), tuple(errors)


def highest_severity_status(
    verdicts: tuple[StructuredVerdict, ...],
    *,
    severity: dict[str, int],
) -> str | None:
    """Pick the most-severe status across ``verdicts`` using ``severity`` ranks.

    Higher rank wins; ties keep the first encountered.
    """
    best: tuple[int, str] | None = None
    for verdict in verdicts:
        rank = severity.get(verdict.status)
        if rank is None:
            continue
        if best is None or rank > best[0]:
            best = (rank, verdict.status)
    return best[1] if best else None


def _validate_payload(
    payload: dict[str, Any],
    *,
    source: str,
    raw_body: str,
) -> StructuredVerdict | VerdictParseError:
    def _err(code: str, message: str) -> VerdictParseError:
        return VerdictParseError(source=source, code=code, message=message, raw_body=raw_body)

    version = payload.get("verdict_version")
    if version != VERDICT_VERSION:
        return _err(
            "version-mismatch",
            f"verdict_version must be '{VERDICT_VERSION}', got {version!r}.",
        )

    lane = payload.get("lane")
    if lane not in ALLOWED_LANES:
        return _err("lane-invalid", f"lane must be one of {ALLOWED_LANES}, got {lane!r}.")

    kind = payload.get("kind")
    if kind not in ALLOWED_KINDS:
        return _err("kind-invalid", f"kind {kind!r} is not in the allowed vocabulary.")

    status = payload.get("status")
    if status not in ALLOWED_STATUSES:
        return _err("status-invalid", f"status {status!r} is not in the allowed vocabulary.")

    summary_raw = payload.get("summary", "")
    if summary_raw is not None and not isinstance(summary_raw, str):
        return _err("summary-type", "summary must be a string.")

    target = payload.get("target")
    if target is not None and not isinstance(target, str):
        return _err("target-type", "target must be a string.")

    notes_raw = payload.get("notes", [])
    if notes_raw is None:
        notes_raw = []
    if not isinstance(notes_raw, list) or not all(isinstance(item, str) for item in notes_raw):
        return _err("notes-type", "notes must be a list of strings.")

    metrics_raw = payload.get("metrics", {}) or {}
    if not isinstance(metrics_raw, dict):
        return _err("metrics-type", "metrics must be an object.")

    blockers_raw = payload.get("blockers", []) or []
    if not isinstance(blockers_raw, list):
        return _err("blockers-type", "blockers must be a list.")

    blockers: list[Blocker] = []
    for index, raw in enumerate(blockers_raw):
        if not isinstance(raw, dict):
            return _err("blocker-type", f"blockers[{index}] must be an object.")
        converted = _coerce_blocker(raw, index=index, source=source)
        if isinstance(converted, VerdictParseError):
            return converted
        blockers.append(converted)

    known_keys = {
        "verdict_version",
        "lane",
        "kind",
        "status",
        "target",
        "summary",
        "blockers",
        "notes",
        "metrics",
    }
    unknown = sorted(set(payload.keys()) - known_keys)
    if unknown:
        return _err("unknown-field", f"Unknown field(s) in verdict: {unknown}.")

    return StructuredVerdict(
        lane=lane,
        kind=kind,
        status=status,
        summary=(summary_raw or "").strip(),
        target=target.strip() if isinstance(target, str) and target.strip() else None,
        blockers=tuple(blockers),
        notes=tuple(item.strip() for item in notes_raw if item.strip()),
        metrics=dict(metrics_raw),
        source=source,
    )


def _coerce_blocker(
    raw: dict[str, Any],
    *,
    index: int,
    source: str,
) -> Blocker | VerdictParseError:
    def _err(code: str, message: str) -> VerdictParseError:
        return VerdictParseError(source=source, code=code, message=message)

    category = raw.get("category")
    if not isinstance(category, str) or category not in ALLOWED_BLOCKER_CATEGORIES:
        return _err("blocker-category", f"blockers[{index}].category is invalid: {category!r}.")
    code = raw.get("code")
    if not isinstance(code, str) or not _CODE_PATTERN.match(code):
        return _err("blocker-code", f"blockers[{index}].code must match [a-z0-9][a-z0-9-]*, got {code!r}.")
    message = raw.get("message")
    if not isinstance(message, str) or not message.strip():
        return _err("blocker-message", f"blockers[{index}].message must be a non-empty string.")
    repairable = raw.get("repairable", True)
    if not isinstance(repairable, bool):
        return _err("blocker-repairable", f"blockers[{index}].repairable must be boolean.")

    blocks_statuses_raw = raw.get("blocks_statuses", []) or []
    if not isinstance(blocks_statuses_raw, list) or not all(isinstance(item, str) for item in blocks_statuses_raw):
        return _err(
            "blocker-blocks-statuses",
            f"blockers[{index}].blocks_statuses must be a list of strings.",
        )

    details = raw.get("details", {}) or {}
    if not isinstance(details, dict):
        return _err("blocker-details", f"blockers[{index}].details must be an object.")

    allowed_keys = {"category", "code", "message", "repairable", "blocks_statuses", "details"}
    unknown = sorted(set(raw.keys()) - allowed_keys)
    if unknown:
        return _err("blocker-unknown-field", f"blockers[{index}] has unknown field(s): {unknown}.")

    return Blocker(
        category=category,
        code=code,
        message=message.strip(),
        repairable=repairable,
        blocks_statuses=tuple(item.strip() for item in blocks_statuses_raw if item.strip()),
        details=dict(details),
    )
