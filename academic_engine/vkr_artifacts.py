"""VKR / thesis frontmatter artifact generator.

Reads structured VKR metadata from a TOML file (``works/<slug>/thesis/
metadata.toml``) and renders the frontmatter artifacts:

- ``title-page.md`` — титульный лист;
- ``task-sheet.md`` — задание на ВКР;
- ``abstract-ru.md`` — аннотация на русском;
- ``abstract-en.md`` — abstract (English);
- ``keywords.md`` — ключевые слова (ru+en).

Missing required fields produce ``vkr-frontmatter`` blockers so that the
repair kernel can downgrade submission-ready status. This module is
deterministic: given the same metadata it produces byte-identical
output, which makes diffing and regression tests easy.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .repair_kernel import Blocker

_REQUIRED_TOP = (
    "title",
    "university",
    "author",
    "supervisor",
    "program",
    "year",
    "city",
)

_REQUIRED_AUTHOR = ("full_name",)
_REQUIRED_SUPERVISOR = ("full_name",)
_REQUIRED_PROGRAM = ("code", "name")


@dataclass(frozen=True)
class VkrMetadata:
    """Normalized VKR metadata."""

    title: str
    university: str
    faculty: str | None
    department: str | None
    program_code: str
    program_name: str
    author_full_name: str
    author_group: str | None
    supervisor_full_name: str
    supervisor_degree: str | None
    supervisor_position: str | None
    year: int
    city: str
    abstract_ru: str
    abstract_en: str
    keywords_ru: tuple[str, ...]
    keywords_en: tuple[str, ...]
    research_tasks: tuple[str, ...] = ()
    defense_date: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "university": self.university,
            "faculty": self.faculty,
            "department": self.department,
            "program_code": self.program_code,
            "program_name": self.program_name,
            "author_full_name": self.author_full_name,
            "supervisor_full_name": self.supervisor_full_name,
            "year": self.year,
            "city": self.city,
            "keywords_ru": list(self.keywords_ru),
            "keywords_en": list(self.keywords_en),
            "research_tasks": list(self.research_tasks),
        }


@dataclass(frozen=True)
class ArtifactIssue:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_blocker(self) -> Blocker:
        return Blocker(
            category="vkr-frontmatter",
            code=self.code,
            message=self.message,
            repairable=True,
            blocks_statuses=("submission-ready",),
            details=dict(self.details),
        )


@dataclass(frozen=True)
class ArtifactBundle:
    metadata: VkrMetadata | None
    issues: tuple[ArtifactIssue, ...]
    rendered: dict[str, str] = field(default_factory=dict)

    @property
    def has_blockers(self) -> bool:
        return bool(self.issues)

    def blockers(self) -> list[Blocker]:
        return [issue.to_blocker() for issue in self.issues]


# ---------------------------------------------------------------------------


def load_metadata(path: Path) -> tuple[VkrMetadata | None, list[ArtifactIssue]]:
    """Load and validate metadata from TOML."""
    if not path.exists():
        return None, [
            ArtifactIssue(
                code="metadata-missing",
                message=(
                    f"VKR metadata file not found at {path}. "
                    "Create it with author/supervisor/program/keywords/abstract fields."
                ),
                details={"path": str(path)},
            )
        ]
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, [
            ArtifactIssue(
                code="metadata-unreadable",
                message=f"Failed to parse VKR metadata: {exc}",
                details={"path": str(path)},
            )
        ]

    issues: list[ArtifactIssue] = []
    for field_name in _REQUIRED_TOP:
        if field_name not in raw or raw[field_name] in (None, "", []):
            issues.append(
                ArtifactIssue(
                    code=f"metadata-missing-{field_name}",
                    message=f"Required metadata field '{field_name}' is missing or empty.",
                )
            )

    author = raw.get("author") or {}
    if not isinstance(author, dict):
        issues.append(ArtifactIssue(code="metadata-author-invalid", message="`author` must be a table."))
        author = {}
    for sub in _REQUIRED_AUTHOR:
        if not author.get(sub):
            issues.append(
                ArtifactIssue(
                    code=f"metadata-author-missing-{sub}",
                    message=f"Required `author.{sub}` is missing.",
                )
            )

    supervisor = raw.get("supervisor") or {}
    if not isinstance(supervisor, dict):
        issues.append(ArtifactIssue(code="metadata-supervisor-invalid", message="`supervisor` must be a table."))
        supervisor = {}
    for sub in _REQUIRED_SUPERVISOR:
        if not supervisor.get(sub):
            issues.append(
                ArtifactIssue(
                    code=f"metadata-supervisor-missing-{sub}",
                    message=f"Required `supervisor.{sub}` is missing.",
                )
            )

    program = raw.get("program") or {}
    if not isinstance(program, dict):
        issues.append(ArtifactIssue(code="metadata-program-invalid", message="`program` must be a table."))
        program = {}
    for sub in _REQUIRED_PROGRAM:
        if not program.get(sub):
            issues.append(
                ArtifactIssue(
                    code=f"metadata-program-missing-{sub}",
                    message=f"Required `program.{sub}` is missing.",
                )
            )

    abstract = raw.get("abstract") or {}
    if not isinstance(abstract, dict):
        abstract = {}
    abstract_ru = str(abstract.get("ru") or "").strip()
    abstract_en = str(abstract.get("en") or "").strip()
    if len(abstract_ru) < 200:
        issues.append(
            ArtifactIssue(
                code="abstract-ru-too-short",
                message=(f"Russian abstract must be at least 200 characters; got {len(abstract_ru)}."),
            )
        )
    if len(abstract_en) < 200:
        issues.append(
            ArtifactIssue(
                code="abstract-en-too-short",
                message=(f"English abstract must be at least 200 characters; got {len(abstract_en)}."),
            )
        )

    keywords = raw.get("keywords") or {}
    if not isinstance(keywords, dict):
        keywords = {}
    keywords_ru = tuple(str(item).strip() for item in keywords.get("ru", []) if str(item).strip())
    keywords_en = tuple(str(item).strip() for item in keywords.get("en", []) if str(item).strip())
    if len(keywords_ru) < 3:
        issues.append(
            ArtifactIssue(
                code="keywords-ru-insufficient",
                message=f"Need at least 3 Russian keywords; got {len(keywords_ru)}.",
            )
        )
    if len(keywords_en) < 3:
        issues.append(
            ArtifactIssue(
                code="keywords-en-insufficient",
                message=f"Need at least 3 English keywords; got {len(keywords_en)}.",
            )
        )

    if issues:
        return None, issues

    metadata = VkrMetadata(
        title=str(raw["title"]).strip(),
        university=str(raw["university"]).strip(),
        faculty=(str(raw.get("faculty") or "").strip() or None),
        department=(str(raw.get("department") or "").strip() or None),
        program_code=str(program["code"]).strip(),
        program_name=str(program["name"]).strip(),
        author_full_name=str(author["full_name"]).strip(),
        author_group=(str(author.get("group") or "").strip() or None),
        supervisor_full_name=str(supervisor["full_name"]).strip(),
        supervisor_degree=(str(supervisor.get("degree") or "").strip() or None),
        supervisor_position=(str(supervisor.get("position") or "").strip() or None),
        year=int(raw["year"]),
        city=str(raw["city"]).strip(),
        abstract_ru=abstract_ru,
        abstract_en=abstract_en,
        keywords_ru=keywords_ru,
        keywords_en=keywords_en,
        research_tasks=tuple(str(task).strip() for task in raw.get("research_tasks", []) if str(task).strip()),
        defense_date=(str(raw.get("defense_date") or "").strip() or None),
        raw=raw,
    )
    return metadata, []


# ---------------------------------------------------------------------------
# Rendering.


def render_title_page(metadata: VkrMetadata) -> str:
    parts = [
        f"# {metadata.university}",
        "",
    ]
    if metadata.faculty:
        parts.append(f"{metadata.faculty}")
    if metadata.department:
        parts.append(f"Кафедра: {metadata.department}")
    parts.extend(["", "## Выпускная квалификационная работа", "", f"**{metadata.title}**", ""])
    parts.extend(
        [
            f"Направление подготовки: {metadata.program_code} — {metadata.program_name}",
            "",
            f"Автор: {metadata.author_full_name}"
            + (f", группа {metadata.author_group}" if metadata.author_group else ""),
            "",
            "Научный руководитель: "
            + ", ".join(
                part
                for part in (
                    metadata.supervisor_degree,
                    metadata.supervisor_position,
                    metadata.supervisor_full_name,
                )
                if part
            ),
            "",
            f"{metadata.city}, {metadata.year}",
        ]
    )
    return "\n".join(parts).rstrip() + "\n"


def render_abstract(metadata: VkrMetadata, *, language: str) -> str:
    if language == "ru":
        body = metadata.abstract_ru
        keywords = metadata.keywords_ru
        heading = "# Аннотация"
        kw_label = "Ключевые слова"
    else:
        body = metadata.abstract_en
        keywords = metadata.keywords_en
        heading = "# Abstract"
        kw_label = "Keywords"
    return f"{heading}\n\n{body}\n\n**{kw_label}:** {', '.join(keywords)}.\n"


def render_keywords(metadata: VkrMetadata) -> str:
    return (
        "# Ключевые слова / Keywords\n\n"
        f"**RU:** {', '.join(metadata.keywords_ru)}.\n\n"
        f"**EN:** {', '.join(metadata.keywords_en)}.\n"
    )


def render_task_sheet(metadata: VkrMetadata) -> str:
    tasks_section = ""
    if metadata.research_tasks:
        tasks_list = "\n".join(f"{index}. {task}" for index, task in enumerate(metadata.research_tasks, start=1))
        tasks_section = f"\n## Задачи исследования\n\n{tasks_list}\n"
    defense_line = f"- Дата защиты: {metadata.defense_date}\n" if metadata.defense_date else ""
    return (
        f"# Задание на выпускную квалификационную работу\n"
        f"\n"
        f"- Автор: {metadata.author_full_name}\n"
        f"- Научный руководитель: {metadata.supervisor_full_name}\n"
        f"- Направление подготовки: {metadata.program_code} — {metadata.program_name}\n"
        f"- Университет: {metadata.university}\n"
        f"- Тема работы: {metadata.title}\n"
        f"{defense_line}"
        f"- Сформировано: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"{tasks_section}"
    )


def render_bundle(metadata: VkrMetadata) -> dict[str, str]:
    return {
        "title-page.md": render_title_page(metadata),
        "abstract-ru.md": render_abstract(metadata, language="ru"),
        "abstract-en.md": render_abstract(metadata, language="en"),
        "keywords.md": render_keywords(metadata),
        "task-sheet.md": render_task_sheet(metadata),
    }


def build_bundle(metadata_path: Path) -> ArtifactBundle:
    metadata, issues = load_metadata(metadata_path)
    if metadata is None:
        return ArtifactBundle(metadata=None, issues=tuple(issues))
    rendered = render_bundle(metadata)
    return ArtifactBundle(metadata=metadata, issues=tuple(issues), rendered=rendered)


def write_bundle(
    bundle: ArtifactBundle,
    *,
    destination: Path,
    overwrite: bool = True,
) -> list[Path]:
    """Persist rendered artifacts to ``destination``.

    Returns list of written paths. No files are written when the bundle
    has blockers.
    """
    if bundle.has_blockers or bundle.metadata is None:
        return []
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, content in bundle.rendered.items():
        target = destination / filename
        if target.exists() and not overwrite:
            continue
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written
