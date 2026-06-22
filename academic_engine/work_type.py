"""Work-type profiles: article, VKR, magistr, dissertation.

Unifies the expectations for different academic deliverables into a
single registry so that:

- ``one_shot_thesis`` can apply type-specific gates (e.g. dissertation
  requires an extended list of sources, magistr thesis requires 70+
  pages, etc.);
- ``repair_kernel`` can blame the right type when a structural section
  is missing;
- future work types (dissertation-doctor, habilitationsschrift) can be
  plugged in without touching the gate engine.

This module is intentionally declarative: the profiles do not contain
logic, only expectations. The gates that consume them live in
``one_shot.py`` and ``orchestrator_thesis.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .repair_kernel import Blocker


@dataclass(frozen=True)
class WorkTypeProfile:
    """Formal expectations for a given deliverable."""

    identifier: str
    title: str
    lane: str
    artifact_family: str
    required_lanes: tuple[str, ...]
    required_frontmatter: tuple[str, ...]
    required_sections: tuple[str, ...]
    required_artifact_groups: tuple[str, ...]
    required_review_artifacts: tuple[str, ...]
    minimum_entries: int
    minimum_chapters: int
    minimum_primary_share: float
    maximum_originality_similarity: float
    min_chars: int | None = None
    max_chars: int | None = None
    requires_author_abstract: bool = False
    requires_publication_evidence: bool = False
    requires_counterargument_pass: bool = False
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "title": self.title,
            "lane": self.lane,
            "artifact_family": self.artifact_family,
            "required_lanes": list(self.required_lanes),
            "required_frontmatter": list(self.required_frontmatter),
            "required_sections": list(self.required_sections),
            "required_artifact_groups": list(self.required_artifact_groups),
            "required_review_artifacts": list(self.required_review_artifacts),
            "minimum_entries": self.minimum_entries,
            "minimum_chapters": self.minimum_chapters,
            "minimum_primary_share": self.minimum_primary_share,
            "maximum_originality_similarity": self.maximum_originality_similarity,
            "min_chars": self.min_chars,
            "max_chars": self.max_chars,
            "requires_author_abstract": self.requires_author_abstract,
            "requires_publication_evidence": self.requires_publication_evidence,
            "requires_counterargument_pass": self.requires_counterargument_pass,
            "notes": list(self.notes),
        }


_ARTICLE = WorkTypeProfile(
    identifier="article",
    title="Научная статья",
    lane="article",
    artifact_family="article",
    required_lanes=("article",),
    required_frontmatter=(),
    required_sections=("введение", "основная часть", "заключение", "список использованных источников"),
    required_artifact_groups=(),
    required_review_artifacts=(),
    minimum_entries=10,
    minimum_chapters=0,
    minimum_primary_share=0.4,
    maximum_originality_similarity=0.20,
    notes=("Для article lane фронтматтер не требуется, только чек-лист и DOCX.",),
)


_VKR_BACHELOR = WorkTypeProfile(
    identifier="vkr-bachelor",
    title="ВКР бакалавра",
    lane="thesis",
    artifact_family="vkr",
    required_lanes=("thesis",),
    required_frontmatter=(
        "title-page.md",
        "abstract-ru.md",
        "abstract-en.md",
        "keywords.md",
        "task-sheet.md",
    ),
    required_sections=(
        "введение",
        "глава 1",
        "глава 2",
        "глава 3",
        "заключение",
        "список использованных источников",
    ),
    required_artifact_groups=(),
    required_review_artifacts=(),
    minimum_entries=30,
    minimum_chapters=3,
    minimum_primary_share=0.5,
    maximum_originality_similarity=0.35,
)


_VKR_SPECIALIST = WorkTypeProfile(
    identifier="vkr-specialist",
    title="ВКР специалиста",
    lane="thesis",
    artifact_family="vkr",
    required_lanes=("thesis",),
    required_frontmatter=(
        "title-page.md",
        "abstract-ru.md",
        "abstract-en.md",
        "keywords.md",
        "task-sheet.md",
    ),
    required_sections=(
        "введение",
        "глава 1",
        "глава 2",
        "глава 3",
        "заключение",
        "список использованных источников",
    ),
    required_artifact_groups=(),
    required_review_artifacts=(),
    minimum_entries=40,
    minimum_chapters=3,
    minimum_primary_share=0.55,
    maximum_originality_similarity=0.30,
)


_MASTER_THESIS = WorkTypeProfile(
    identifier="master-thesis",
    title="Магистерская диссертация",
    lane="thesis",
    artifact_family="vkr",
    required_lanes=("thesis",),
    required_frontmatter=(
        "title-page.md",
        "abstract-ru.md",
        "abstract-en.md",
        "keywords.md",
        "task-sheet.md",
    ),
    required_sections=(
        "введение",
        "глава 1",
        "глава 2",
        "глава 3",
        "заключение",
        "список использованных источников",
    ),
    required_artifact_groups=(),
    required_review_artifacts=(),
    minimum_entries=60,
    minimum_chapters=3,
    minimum_primary_share=0.6,
    maximum_originality_similarity=0.25,
)


_DISSERTATION_CANDIDATE = WorkTypeProfile(
    identifier="dissertation-candidate",
    title="Кандидатская диссертация",
    lane="thesis",
    artifact_family="dissertation",
    required_lanes=("thesis",),
    required_frontmatter=(),
    required_sections=(
        "введение",
        "глава 1",
        "глава 2",
        "глава 3",
        "заключение",
        "список использованных источников",
    ),
    required_artifact_groups=(
        "historiography-map",
        "novelty-contribution-map",
        "dissertation-claim-map",
        "chapter-contracts",
        "author-abstract",
        "publication-evidence",
        "publication-claim-matrix",
    ),
    required_review_artifacts=("dissertation-review", "counterargument-review"),
    minimum_entries=120,
    minimum_chapters=3,
    minimum_primary_share=0.65,
    maximum_originality_similarity=0.20,
    min_chars=220000,
    max_chars=540000,
    requires_author_abstract=True,
    requires_publication_evidence=True,
    requires_counterargument_pass=True,
    notes=(
        "Для кандидатской требуется автореферат — отдельный артефакт.",
        "Публикации в ВАК-списке должны быть задокументированы до защиты.",
        "Length range is a local planning heuristic based on 1800 characters per page.",
    ),
)


_DISSERTATION_DOCTOR = WorkTypeProfile(
    identifier="dissertation-doctor",
    title="Докторская диссертация",
    lane="thesis",
    artifact_family="dissertation",
    required_lanes=("thesis",),
    required_frontmatter=(),
    required_sections=(
        "введение",
        "глава 1",
        "глава 2",
        "глава 3",
        "глава 4",
        "заключение",
        "список использованных источников",
    ),
    required_artifact_groups=(
        "historiography-map",
        "novelty-contribution-map",
        "dissertation-claim-map",
        "chapter-contracts",
        "author-abstract",
        "publication-evidence",
        "defense-packet",
    ),
    required_review_artifacts=("dissertation-review", "counterargument-review"),
    minimum_entries=250,
    minimum_chapters=4,
    minimum_primary_share=0.7,
    maximum_originality_similarity=0.15,
    min_chars=420000,
    max_chars=900000,
    requires_author_abstract=True,
    requires_publication_evidence=True,
    requires_counterargument_pass=True,
    notes=(
        "Докторская требует автореферат, список работ ВАК/WoS/Scopus и отзыв ведущей организации.",
        "Length range is a local planning heuristic based on 1800 characters per page.",
    ),
)


_PROFILES: dict[str, WorkTypeProfile] = {
    profile.identifier: profile
    for profile in (
        _ARTICLE,
        _VKR_BACHELOR,
        _VKR_SPECIALIST,
        _MASTER_THESIS,
        _DISSERTATION_CANDIDATE,
        _DISSERTATION_DOCTOR,
    )
}

_LEGACY_ALIASES = {
    "vkr": "vkr-bachelor",
    "thesis": "vkr-bachelor",
    "magistr": "master-thesis",
    "dissertation": "dissertation-candidate",
}


def available_profiles() -> list[WorkTypeProfile]:
    return list(_PROFILES.values())


def resolve_profile(identifier: str | None) -> WorkTypeProfile | None:
    if not identifier:
        return None
    key = identifier.strip().casefold()
    if key in _PROFILES:
        return _PROFILES[key]
    if key in _LEGACY_ALIASES:
        return _PROFILES[_LEGACY_ALIASES[key]]
    return None


# ---------------------------------------------------------------------------
# Structural validation against a manuscript.


@dataclass(frozen=True)
class StructureIssue:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_blocker(self) -> Blocker:
        return Blocker(
            category="work-type-structure",
            code=self.code,
            message=self.message,
            repairable=True,
            blocks_statuses=("submission-ready",),
            details=dict(self.details),
        )


_HEADING_PATTERN = re.compile(r"^(#+)\s+(.+?)\s*$", re.MULTILINE)
_ENTRY_PATTERN = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_CHAPTER_HEADING_PATTERN = re.compile(r"^(#+)\s+.*глава\s+\d+", re.IGNORECASE | re.MULTILINE)


def validate_structure(
    manuscript_markdown: str,
    profile: WorkTypeProfile,
) -> list[StructureIssue]:
    """Verify that the manuscript contains the profile's required sections."""
    headings = [
        re.sub(r"\s+", " ", title).strip().casefold() for _level, title in _HEADING_PATTERN.findall(manuscript_markdown)
    ]

    issues: list[StructureIssue] = []
    for required in profile.required_sections:
        normalized_required = required.casefold()
        if not any(normalized_required in heading for heading in headings):
            issues.append(
                StructureIssue(
                    code="required-section-missing",
                    message=f"Required section `{required}` not found in manuscript.",
                    details={"section": required},
                )
            )

    entry_count = len(_ENTRY_PATTERN.findall(manuscript_markdown))
    if entry_count < profile.minimum_entries:
        issues.append(
            StructureIssue(
                code="bibliography-insufficient-entries",
                message=(
                    f"Found {entry_count} bibliography entries, "
                    f"{profile.title} requires at least {profile.minimum_entries}."
                ),
                details={
                    "expected_min": profile.minimum_entries,
                    "actual": entry_count,
                },
            )
        )
    chapter_count = len(_CHAPTER_HEADING_PATTERN.findall(manuscript_markdown))
    if profile.minimum_chapters and chapter_count < profile.minimum_chapters:
        issues.append(
            StructureIssue(
                code="chapter-count-insufficient",
                message=(
                    f"Found {chapter_count} chapter heading(s), "
                    f"{profile.title} requires at least {profile.minimum_chapters}."
                ),
                details={"expected_min": profile.minimum_chapters, "actual": chapter_count},
            )
        )
    return issues


def validate_to_blockers(
    manuscript_markdown: str,
    profile: WorkTypeProfile,
) -> list[Blocker]:
    return [issue.to_blocker() for issue in validate_structure(manuscript_markdown, profile)]
