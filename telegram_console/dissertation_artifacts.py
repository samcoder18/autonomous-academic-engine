"""Dissertation-specific artifact generator.

Builds a lightweight dissertation bundle from ``thesis/dissertation/metadata.toml``.

The current candidate-first contour renders:

- ``author-abstract.md`` — dissertation author abstract;
- ``defense-checklist.md`` — pre-defense checklist with required packet items.

Missing required metadata yields deterministic blockers so that dissertation
one-shot finalization can honestly downgrade readiness.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .repair_kernel import Blocker

_REQUIRED_TOP = ("title", "university", "year", "city", "author", "supervisor", "program", "dissertation")
_REQUIRED_AUTHOR = ("full_name",)
_REQUIRED_SUPERVISOR = ("full_name",)
_REQUIRED_PROGRAM = ("code", "name")
_REQUIRED_DISSERTATION = (
    "degree",
    "specialty_code",
    "specialty_name",
    "novelty_summary",
    "contribution_summary",
    "methodology_summary",
)


@dataclass(frozen=True)
class DissertationMetadata:
    title: str
    university: str
    city: str
    year: int
    program_code: str
    program_name: str
    author_full_name: str
    supervisor_full_name: str
    degree: str
    specialty_code: str
    specialty_name: str
    novelty_summary: str
    contribution_summary: str
    methodology_summary: str
    author_abstract_ru: str
    defense_council: str | None
    leading_organization: str | None
    defense_date: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DissertationArtifactIssue:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_blocker(self) -> Blocker:
        return Blocker(
            category="dissertation-artifacts",
            code=self.code,
            message=self.message,
            repairable=True,
            blocks_statuses=("submission-ready",),
            details=dict(self.details),
        )


@dataclass(frozen=True)
class DissertationArtifactBundle:
    metadata: DissertationMetadata | None
    issues: tuple[DissertationArtifactIssue, ...]
    rendered: dict[str, str] = field(default_factory=dict)

    @property
    def has_blockers(self) -> bool:
        return bool(self.issues)

    def blockers(self) -> list[Blocker]:
        return [item.to_blocker() for item in self.issues]


def load_metadata(path: Path) -> tuple[DissertationMetadata | None, list[DissertationArtifactIssue]]:
    if not path.exists():
        return None, [
            DissertationArtifactIssue(
                code="metadata-missing",
                message=(
                    f"Dissertation metadata file not found at {path}. "
                    "Create thesis/dissertation/metadata.toml with dissertation and author abstract fields."
                ),
                details={"path": str(path)},
            )
        ]

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, [
            DissertationArtifactIssue(
                code="metadata-unreadable",
                message=f"Failed to parse dissertation metadata: {exc}",
                details={"path": str(path)},
            )
        ]

    issues: list[DissertationArtifactIssue] = []
    for field_name in _REQUIRED_TOP:
        if field_name not in raw or raw[field_name] in (None, "", []):
            issues.append(
                DissertationArtifactIssue(
                    code=f"metadata-missing-{field_name}",
                    message=f"Required dissertation metadata field `{field_name}` is missing or empty.",
                )
            )

    author = raw.get("author") or {}
    if not isinstance(author, dict):
        issues.append(DissertationArtifactIssue(code="metadata-author-invalid", message="`author` must be a table."))
        author = {}
    for key in _REQUIRED_AUTHOR:
        if not author.get(key):
            issues.append(
                DissertationArtifactIssue(
                    code=f"metadata-author-missing-{key}",
                    message=f"Required `author.{key}` is missing.",
                )
            )

    supervisor = raw.get("supervisor") or {}
    if not isinstance(supervisor, dict):
        issues.append(
            DissertationArtifactIssue(code="metadata-supervisor-invalid", message="`supervisor` must be a table.")
        )
        supervisor = {}
    for key in _REQUIRED_SUPERVISOR:
        if not supervisor.get(key):
            issues.append(
                DissertationArtifactIssue(
                    code=f"metadata-supervisor-missing-{key}",
                    message=f"Required `supervisor.{key}` is missing.",
                )
            )

    program = raw.get("program") or {}
    if not isinstance(program, dict):
        issues.append(DissertationArtifactIssue(code="metadata-program-invalid", message="`program` must be a table."))
        program = {}
    for key in _REQUIRED_PROGRAM:
        if not program.get(key):
            issues.append(
                DissertationArtifactIssue(
                    code=f"metadata-program-missing-{key}",
                    message=f"Required `program.{key}` is missing.",
                )
            )

    dissertation = raw.get("dissertation") or {}
    if not isinstance(dissertation, dict):
        issues.append(
            DissertationArtifactIssue(code="metadata-dissertation-invalid", message="`dissertation` must be a table.")
        )
        dissertation = {}
    for key in _REQUIRED_DISSERTATION:
        if not dissertation.get(key):
            issues.append(
                DissertationArtifactIssue(
                    code=f"metadata-dissertation-missing-{key}",
                    message=f"Required `dissertation.{key}` is missing.",
                )
            )

    author_abstract = raw.get("author_abstract") or {}
    if not isinstance(author_abstract, dict):
        author_abstract = {}
    author_abstract_ru = str(author_abstract.get("ru") or "").strip()
    if len(author_abstract_ru) < 400:
        issues.append(
            DissertationArtifactIssue(
                code="author-abstract-too-short",
                message=f"Dissertation author abstract must be at least 400 characters; got {len(author_abstract_ru)}.",
            )
        )

    if issues:
        return None, issues

    defense = raw.get("defense") or {}
    if not isinstance(defense, dict):
        defense = {}

    metadata = DissertationMetadata(
        title=str(raw["title"]).strip(),
        university=str(raw["university"]).strip(),
        city=str(raw["city"]).strip(),
        year=int(raw["year"]),
        program_code=str(program["code"]).strip(),
        program_name=str(program["name"]).strip(),
        author_full_name=str(author["full_name"]).strip(),
        supervisor_full_name=str(supervisor["full_name"]).strip(),
        degree=str(dissertation["degree"]).strip(),
        specialty_code=str(dissertation["specialty_code"]).strip(),
        specialty_name=str(dissertation["specialty_name"]).strip(),
        novelty_summary=str(dissertation["novelty_summary"]).strip(),
        contribution_summary=str(dissertation["contribution_summary"]).strip(),
        methodology_summary=str(dissertation["methodology_summary"]).strip(),
        author_abstract_ru=author_abstract_ru,
        defense_council=(str(defense.get("council") or "").strip() or None),
        leading_organization=(str(defense.get("leading_organization") or "").strip() or None),
        defense_date=(str(defense.get("date") or "").strip() or None),
        raw=raw,
    )
    return metadata, []


def render_bundle(metadata: DissertationMetadata) -> dict[str, str]:
    author_abstract = f"""# Автореферат

## 1. Базовые сведения

- Тема: {metadata.title}
- Соискатель: {metadata.author_full_name}
- Научный руководитель: {metadata.supervisor_full_name}
- Ученая степень: {metadata.degree}
- Специальность: {metadata.specialty_code} — {metadata.specialty_name}
- Организация: {metadata.university}
- Год: {metadata.year}
- Город: {metadata.city}

## 2. Научная новизна

{metadata.novelty_summary}

## 3. Авторский вклад

{metadata.contribution_summary}

## 4. Методологическая рамка

{metadata.methodology_summary}

## 5. Краткое содержание

{metadata.author_abstract_ru}
"""

    defense_checklist = f"""# Dissertation Defense Checklist

## 1. Core Metadata

- Тема: {metadata.title}
- Соискатель: {metadata.author_full_name}
- Научный руководитель: {metadata.supervisor_full_name}
- Степень: {metadata.degree}
- Специальность: {metadata.specialty_code} — {metadata.specialty_name}
- Программа: {metadata.program_code} — {metadata.program_name}
- Диссовет: {metadata.defense_council or "TODO"}
- Ведущая организация: {metadata.leading_organization or "TODO"}
- Дата защиты: {metadata.defense_date or "TODO"}

## 2. Required Dissertation Packet

- [ ] Автореферат собран и проверен
- [ ] Historiography map заполнена
- [ ] Novelty and contribution map заполнена
- [ ] Dissertation claim map заполнена
- [ ] Counterargument review проведен
- [ ] Publication evidence sheet заполнен
- [ ] One-shot dissertation report без незакрытых blocker'ов

## 3. Operator Notes

- Проверить соответствие formal dissertation profile и raw official requirements.
- Зафиксировать ограничения выводов и research gaps до финальной сборки.
"""

    return {
        "author-abstract.md": author_abstract.rstrip() + "\n",
        "defense-checklist.md": defense_checklist.rstrip() + "\n",
    }


def build_bundle(metadata_path: Path) -> DissertationArtifactBundle:
    metadata, issues = load_metadata(metadata_path)
    if metadata is None:
        return DissertationArtifactBundle(metadata=None, issues=tuple(issues), rendered={})
    return DissertationArtifactBundle(metadata=metadata, issues=(), rendered=render_bundle(metadata))


def write_bundle(bundle: DissertationArtifactBundle, *, destination: Path) -> list[Path]:
    if bundle.has_blockers:
        return []
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in bundle.rendered.items():
        path = destination / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
