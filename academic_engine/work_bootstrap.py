"""Bootstrap new work bundles inside the workspace.

This module creates a new ``works/<slug>/`` bundle on disk and registers it in
``workspace.toml``. The logic is intentionally pure text/path manipulation so it
can be unit-tested without touching real Codex or local runtime state.

Supported artifact types:

- ``article``                 — article lane only
- ``vkr``, ``vkr-bachelor``,
  ``vkr-specialist``,
  ``master-thesis``           — thesis lane by default
- ``dissertation-candidate``,
  ``dissertation-doctor``     — thesis lane by default

The caller may override active lanes explicitly via ``lanes``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

THESIS_ARTIFACT_TYPES: frozenset[str] = frozenset(
    {
        "vkr",
        "vkr-bachelor",
        "vkr-specialist",
        "master-thesis",
        "dissertation-candidate",
        "dissertation-doctor",
    }
)

ARTICLE_ARTIFACT_TYPES: frozenset[str] = frozenset({"article"})

ALL_ARTIFACT_TYPES: frozenset[str] = THESIS_ARTIFACT_TYPES | ARTICLE_ARTIFACT_TYPES

DEFAULT_THESIS_PROFILE = "ru-vkr-university-default"
DEFAULT_DISSERTATION_CANDIDATE_PROFILE = "rf-dissertation-candidate"
DEFAULT_DISSERTATION_DOCTOR_PROFILE = "rf-dissertation-doctor"
DEFAULT_ARTICLE_PROFILE = "ru-law-article-v1"

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

DEFAULT_THESIS_SECTIONS: tuple[str, ...] = (
    "thesis/manuscript/sections/00-title.md",
    "thesis/manuscript/sections/01-introduction.md",
    "thesis/manuscript/sections/02-chapter-1.md",
    "thesis/manuscript/sections/03-chapter-2.md",
    "thesis/manuscript/sections/04-chapter-3.md",
    "thesis/manuscript/sections/05-conclusion.md",
    "thesis/manuscript/sections/06-bibliography.md",
)

DOCTOR_THESIS_SECTIONS: tuple[str, ...] = (
    "thesis/manuscript/sections/00-title.md",
    "thesis/manuscript/sections/01-introduction.md",
    "thesis/manuscript/sections/02-chapter-1.md",
    "thesis/manuscript/sections/03-chapter-2.md",
    "thesis/manuscript/sections/04-chapter-3.md",
    "thesis/manuscript/sections/05-chapter-4.md",
    "thesis/manuscript/sections/06-conclusion.md",
    "thesis/manuscript/sections/07-bibliography.md",
)


class WorkBootstrapError(ValueError):
    """Raised when a new work cannot be bootstrapped."""


@dataclass(frozen=True)
class WorkBootstrapRequest:
    slug: str
    title: str
    topic: str
    artifact_type: str
    language: str = "ru"
    lanes: tuple[str, ...] | None = None
    thesis_profile: str | None = None
    article_profile: str | None = None
    set_default: bool = False

    def resolved_lanes(self) -> tuple[str, ...]:
        if self.lanes is not None:
            return self.lanes
        if self.artifact_type in THESIS_ARTIFACT_TYPES:
            return ("thesis",)
        if self.artifact_type in ARTICLE_ARTIFACT_TYPES:
            return ("article",)
        raise WorkBootstrapError(
            f"Unknown artifact_type `{self.artifact_type}`. Expected one of: {sorted(ALL_ARTIFACT_TYPES)}"
        )


@dataclass(frozen=True)
class WorkBootstrapResult:
    slug: str
    work_dir: Path
    work_toml: Path
    work_canon: Path
    created_dirs: tuple[Path, ...]
    workspace_toml: Path
    set_default: bool
    default_work_after: str


def validate_slug(slug: str) -> None:
    if not slug:
        raise WorkBootstrapError("Slug must not be empty.")
    if not SLUG_PATTERN.match(slug):
        raise WorkBootstrapError(
            f"Invalid slug `{slug}`. Expected kebab-case ascii: "
            "lowercase letters, digits, hyphens, not starting/ending with a hyphen."
        )


def _escape_toml_string(value: str) -> str:
    # TOML basic strings support \\ and \" escape sequences.
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_toml_string(value: str) -> str:
    return f'"{_escape_toml_string(value)}"'


def _thesis_sections_for_artifact(artifact_type: str) -> tuple[str, ...]:
    if artifact_type == "dissertation-doctor":
        return DOCTOR_THESIS_SECTIONS
    return DEFAULT_THESIS_SECTIONS


def _thesis_section_placeholders_for_artifact(artifact_type: str) -> dict[str, str]:
    placeholders = {
        "thesis/manuscript/sections/00-title.md": "# Титульный лист\n",
        "thesis/manuscript/sections/01-introduction.md": "# Введение\n\n_Черновик введения._\n",
        "thesis/manuscript/sections/02-chapter-1.md": "# Глава 1\n\n_Черновик главы 1._\n",
        "thesis/manuscript/sections/03-chapter-2.md": "# Глава 2\n\n_Черновик главы 2._\n",
        "thesis/manuscript/sections/04-chapter-3.md": "# Глава 3\n\n_Черновик главы 3._\n",
        "thesis/manuscript/sections/05-conclusion.md": "# Заключение\n\n_Черновик заключения._\n",
        "thesis/manuscript/sections/06-bibliography.md": (
            "# Список использованных источников\n\n_Пока пусто — наполняется по мере верификации источников._\n"
        ),
    }
    if artifact_type == "dissertation-doctor":
        placeholders = {
            "thesis/manuscript/sections/00-title.md": "# Титульный лист\n",
            "thesis/manuscript/sections/01-introduction.md": "# Введение\n\n_Черновик введения._\n",
            "thesis/manuscript/sections/02-chapter-1.md": "# Глава 1\n\n_Черновик главы 1._\n",
            "thesis/manuscript/sections/03-chapter-2.md": "# Глава 2\n\n_Черновик главы 2._\n",
            "thesis/manuscript/sections/04-chapter-3.md": "# Глава 3\n\n_Черновик главы 3._\n",
            "thesis/manuscript/sections/05-chapter-4.md": "# Глава 4\n\n_Черновик главы 4._\n",
            "thesis/manuscript/sections/06-conclusion.md": "# Заключение\n\n_Черновик заключения._\n",
            "thesis/manuscript/sections/07-bibliography.md": (
                "# Список использованных источников\n\n_Пока пусто — наполняется по мере верификации источников._\n"
            ),
        }
    return placeholders


def render_work_toml(request: WorkBootstrapRequest) -> str:
    """Render canonical work.toml text for the new work.

    Layout follows existing ``works/<slug>/work.toml`` conventions so
    that ``load_work_config`` can resolve it without extra migrations.
    """

    validate_slug(request.slug)
    lanes = request.resolved_lanes()
    if not lanes:
        raise WorkBootstrapError("At least one lane must be active.")
    for lane in lanes:
        if lane not in {"thesis", "article"}:
            raise WorkBootstrapError(f"Unsupported lane `{lane}`.")

    lines: list[str] = []
    lines.append("version = 1")
    lines.append(f"slug = {_format_toml_string(request.slug)}")
    lines.append(f"title = {_format_toml_string(request.title)}")
    lines.append(f"topic = {_format_toml_string(request.topic)}")
    lines.append(f"artifact_type = {_format_toml_string(request.artifact_type)}")
    lines.append(f"language = {_format_toml_string(request.language)}")
    lane_literals = ", ".join(_format_toml_string(lane) for lane in lanes)
    lines.append(f"active_lanes = [{lane_literals}]")
    lines.append(f"work_canon = {_format_toml_string('work-canon.md')}")

    standards_lines: list[str] = []
    if "thesis" in lanes:
        profile = request.thesis_profile or _default_thesis_profile_for_artifact(request.artifact_type)
        standards_lines.append(f"thesis_profile = {_format_toml_string(profile)}")
    if "article" in lanes:
        profile = request.article_profile or DEFAULT_ARTICLE_PROFILE
        standards_lines.append(f"article_profile = {_format_toml_string(profile)}")
    if standards_lines:
        lines.append("")
        lines.append("[standards]")
        lines.extend(standards_lines)

    if "thesis" in lanes:
        lines.append("")
        lines.append("[thesis]")
        lines.append(f"root_dir = {_format_toml_string('thesis')}")
        lines.append(f"chapters_dir = {_format_toml_string('thesis/chapters')}")
        lines.append(f"sources_dir = {_format_toml_string('thesis/sources')}")
        lines.append(f"manuscript_dir = {_format_toml_string('thesis/manuscript')}")
        lines.append(f"manuscript_sections_dir = {_format_toml_string('thesis/manuscript/sections')}")
        lines.append(f"reviews_dir = {_format_toml_string('thesis/reviews')}")
        lines.append(f"sync_dir = {_format_toml_string('thesis/sync')}")
        lines.append(f"full_draft_path = {_format_toml_string('thesis/manuscript/full-draft.md')}")
        lines.append(f"docx_filename = {_format_toml_string(f'{request.slug}.docx')}")
        lines.append("section_order = [")
        for section in _thesis_sections_for_artifact(request.artifact_type):
            lines.append(f"  {_format_toml_string(section)},")
        lines.append("]")

    if "article" in lanes:
        lines.append("")
        lines.append("[article]")
        lines.append(f"root_dir = {_format_toml_string('articles')}")
        lines.append(f"briefs_dir = {_format_toml_string('articles/briefs')}")
        lines.append(f"evidence_dir = {_format_toml_string('articles/evidence')}")
        lines.append(f"claim_maps_dir = {_format_toml_string('articles/claim-maps')}")
        lines.append(f"drafts_dir = {_format_toml_string('articles/drafts')}")
        lines.append(f"reviews_dir = {_format_toml_string('articles/reviews')}")
        lines.append(f"final_dir = {_format_toml_string('articles/final')}")
        lines.append(f"docx_subdir = {_format_toml_string('articles')}")

    return "\n".join(lines) + "\n"


def render_work_canon(request: WorkBootstrapRequest) -> str:
    """Render a minimal work-canon.md skeleton."""

    return (
        f"# Канон работы — {request.title}\n\n"
        f"- **slug**: `{request.slug}`\n"
        f"- **topic**: {request.topic}\n"
        f"- **artifact_type**: `{request.artifact_type}`\n"
        f"- **language**: `{request.language}`\n\n"
        "## Скоуп и утверждённые решения\n\n"
        "Опишите здесь зафиксированные решения по теме, границам, "
        "структуре и подходу. Черновики в тексте работы должны "
        "соответствовать этому канону.\n\n"
        "## Источники, которые считаются первичными\n\n"
        "- (перечислите ключевые первоисточники по мере их верификации)\n\n"
        "## Открытые вопросы\n\n"
        "- (перечислите то, что ещё не решено и требует подтверждения "
        "руководителем или дополнительной проработки)\n"
    )


def _default_thesis_profile_for_artifact(artifact_type: str) -> str:
    if artifact_type == "dissertation-candidate":
        return DEFAULT_DISSERTATION_CANDIDATE_PROFILE
    if artifact_type == "dissertation-doctor":
        return DEFAULT_DISSERTATION_DOCTOR_PROFILE
    return DEFAULT_THESIS_PROFILE


def _planned_dirs_for_lanes(work_dir: Path, lanes: tuple[str, ...]) -> tuple[Path, ...]:
    planned: list[Path] = [work_dir]
    if "thesis" in lanes:
        planned.extend(
            [
                work_dir / "thesis",
                work_dir / "thesis" / "chapters",
                work_dir / "thesis" / "sources",
                work_dir / "thesis" / "manuscript",
                work_dir / "thesis" / "manuscript" / "sections",
                work_dir / "thesis" / "reviews",
                work_dir / "thesis" / "sync",
                work_dir / "thesis" / "ledgers",
            ]
        )
    if "article" in lanes:
        planned.extend(
            [
                work_dir / "articles",
                work_dir / "articles" / "briefs",
                work_dir / "articles" / "evidence",
                work_dir / "articles" / "claim-maps",
                work_dir / "articles" / "drafts",
                work_dir / "articles" / "reviews",
                work_dir / "articles" / "final",
            ]
        )
    return tuple(planned)


def _planned_dissertation_dirs(work_dir: Path) -> tuple[Path, ...]:
    dissertation_root = work_dir / "thesis" / "dissertation"
    return (
        dissertation_root,
        dissertation_root / "artifacts",
        dissertation_root / "maps",
        dissertation_root / "chapter-contracts",
        dissertation_root / "reviews",
        dissertation_root / "publications",
        dissertation_root / "defense",
    )


def _is_dissertation_artifact(artifact_type: str) -> bool:
    return artifact_type in {"dissertation-candidate", "dissertation-doctor"}


_DEFAULT_WORK_RE = re.compile(r'(?m)^default_work\s*=\s*".*"\s*$')
_WORKS_SECTION_HEADER_RE = re.compile(r"(?m)^\[works\]\s*$")


def register_work_in_workspace_toml(
    workspace_text: str,
    slug: str,
    rel_path: str,
    *,
    set_default: bool,
) -> str:
    """Return updated workspace.toml text with the new work registered.

    - Adds ``slug = "rel_path"`` under ``[works]`` if not already present.
    - Optionally replaces ``default_work = "..."`` with ``slug``.
    - Idempotent: if entry already exists with the same path, returns text
      unchanged (apart from optional default_work swap).
    """

    validate_slug(slug)
    if not _WORKS_SECTION_HEADER_RE.search(workspace_text):
        raise WorkBootstrapError("workspace.toml has no [works] section.")

    existing_entry_re = re.compile(rf'(?m)^{re.escape(slug)}\s*=\s*"(?P<path>[^"]*)"\s*$')
    existing = existing_entry_re.search(workspace_text)
    if existing:
        existing_path = existing.group("path")
        if existing_path != rel_path:
            raise WorkBootstrapError(
                f"workspace.toml already has `{slug}` pointing to `{existing_path}` (expected `{rel_path}`)."
            )
        updated = workspace_text
    else:
        # Insert line after [works] header. Preserve trailing layout.
        header_match = _WORKS_SECTION_HEADER_RE.search(workspace_text)
        assert header_match is not None  # guarded above
        insert_at = header_match.end()
        trailing_start = insert_at
        # Position at start of next line.
        if trailing_start < len(workspace_text) and workspace_text[trailing_start] == "\n":
            trailing_start += 1
        new_line = f"{slug} = {_format_toml_string(rel_path)}\n"
        updated = workspace_text[:trailing_start] + new_line + workspace_text[trailing_start:]

    if set_default:
        if not _DEFAULT_WORK_RE.search(updated):
            raise WorkBootstrapError("workspace.toml has no default_work entry.")
        updated = _DEFAULT_WORK_RE.sub(f"default_work = {_format_toml_string(slug)}", updated, count=1)

    return updated


def extract_default_work(workspace_text: str) -> str:
    match = re.search(r'(?m)^default_work\s*=\s*"(?P<slug>[^"]*)"\s*$', workspace_text)
    if not match:
        raise WorkBootstrapError("workspace.toml has no default_work entry.")
    return match.group("slug")


def bootstrap_work(
    workspace_root: Path,
    request: WorkBootstrapRequest,
) -> WorkBootstrapResult:
    """Create a new work bundle on disk and register it in workspace.toml.

    Raises ``WorkBootstrapError`` if the slug already exists or if any of the
    planned files already exists with conflicting content. The filesystem
    mutation is best-effort atomic within a single work dir: workspace.toml is
    only updated after work files are written successfully.
    """

    validate_slug(request.slug)
    workspace_root = workspace_root.resolve()
    workspace_toml = workspace_root / "workspace.toml"
    if not workspace_toml.exists():
        raise WorkBootstrapError(f"workspace.toml not found at {workspace_toml}")

    work_dir = workspace_root / "works" / request.slug
    rel_path = f"works/{request.slug}"
    if work_dir.exists() and any(work_dir.iterdir()):
        raise WorkBootstrapError(
            f"Target directory `{work_dir}` already exists and is not empty. Refusing to overwrite."
        )

    workspace_text_before = workspace_toml.read_text(encoding="utf-8")
    lanes = request.resolved_lanes()
    planned_dirs = _planned_dirs_for_lanes(work_dir, lanes)
    if "thesis" in lanes and _is_dissertation_artifact(request.artifact_type):
        planned_dirs = planned_dirs + _planned_dissertation_dirs(work_dir)
    for directory in planned_dirs:
        directory.mkdir(parents=True, exist_ok=True)

    work_toml_path = work_dir / "work.toml"
    work_canon_path = work_dir / "work-canon.md"
    work_toml_path.write_text(render_work_toml(request), encoding="utf-8")
    work_canon_path.write_text(render_work_canon(request), encoding="utf-8")

    if "thesis" in lanes:
        section_placeholders = _thesis_section_placeholders_for_artifact(request.artifact_type)
        for rel, body in section_placeholders.items():
            path = work_dir / rel
            if not path.exists():
                path.write_text(body, encoding="utf-8")

    if "thesis" in lanes and _is_dissertation_artifact(request.artifact_type):
        dissertation_placeholders = {
            "thesis/dissertation/metadata.toml": (
                'title = ""\n'
                'university = ""\n'
                "year = 2026\n"
                'city = ""\n\n'
                "[program]\n"
                'code = ""\n'
                'name = ""\n\n'
                "[author]\n"
                'full_name = ""\n\n'
                "[supervisor]\n"
                'full_name = ""\n\n'
                "[dissertation]\n"
                'degree = ""\n'
                'specialty_code = ""\n'
                'specialty_name = ""\n'
                'novelty_summary = ""\n'
                'contribution_summary = ""\n'
                'methodology_summary = ""\n\n'
                "[author_abstract]\n"
                'ru = ""\n\n'
                "[defense]\n"
                'council = ""\n'
                'leading_organization = ""\n'
                'date = ""\n'
            ),
            "thesis/dissertation/maps/historiography-map.md": (
                "# Historiography Map\n\n"
                "## 1. Поле спора\n\n- Главная исследовательская проблема:\n- Основные школы / подходы:\n\n"
                "## 2. Coverage\n\n- Что уже исследовано:\n- Что остается неразрешенным:\n"
            ),
            "thesis/dissertation/maps/novelty-contribution-map.md": (
                "# Novelty and Contribution Map\n\n"
                "## 1. Scientific Novelty\n\n- Тезис 1:\n- Тезис 2:\n\n"
                "## 2. Author Contribution\n\n- Вклад 1:\n- Вклад 2:\n\n"
                "## 3. Limits\n\n- Ограничение 1:\n"
            ),
            "thesis/dissertation/maps/dissertation-claim-map.md": (
                "# Dissertation Claim Map\n\n"
                "## 1. Claims\n\n"
                "### Claim 1\n\n- Claim ID:\n- Claim text:\n- Counterargument to address:\n- Limits or caveats:\n"
            ),
            "thesis/dissertation/reviews/counterargument-review.md": (
                "# Counterargument Review\n\n## 1. Strong Alternative Positions\n\n- Позиция:\n- Как ответить:\n"
            ),
            "thesis/dissertation/reviews/dissertation-review.md": (
                "# Dissertation Review Sheet\n\n"
                "## 1. Research Quality\n\n- Где текст обзорен вместо исследования:\n- Где завышена новизна:\n"
            ),
            "thesis/dissertation/publications/publication-evidence.md": (
                "# Publication Evidence Sheet\n\n"
                "## 1. Publication 1\n\n"
                "- Статус:\n- Выходные данные:\n- Связь с диссертацией:\n\n"
                "## 2. Coverage\n\n- ВАК / Scopus / WoS статус:\n- Какие тезисы диссертации покрывает публикация:\n"
            ),
            "thesis/dissertation/publications/publication-claim-matrix.md": (
                "# Publication Claim Matrix\n\n"
                "| Тезис | Глава | Публикация | Статус покрытия |\n"
                "| --- | --- | --- | --- |\n"
                "| Тезис 1 | Глава 1 | Публикация 1 | Полное / частичное / нет покрытия |\n"
            ),
            "thesis/dissertation/defense/defense-plan.md": (
                "# Defense Plan\n\n## 1. Совет и трек защиты\n\n- Диссертационный совет:\n- Планируемый срок:\n"
            ),
        }
        if request.artifact_type == "dissertation-doctor":
            dissertation_placeholders["thesis/dissertation/defense/leading-organization.md"] = (
                "# Leading Organization Brief\n\n"
                "## 1. Ведущая организация\n\n"
                "- Наименование:\n- Компетенция:\n- Связь с темой диссертации:\n"
            )
            dissertation_placeholders["thesis/dissertation/defense/opponents.md"] = (
                "# Opponents Sheet\n\n## 1. Оппонент 1\n\n- ФИО:\n- Специализация:\n- Связь с темой диссертации:\n"
            )
        chapter_contract_count = 4 if request.artifact_type == "dissertation-doctor" else 3
        for index in range(1, chapter_contract_count + 1):
            dissertation_placeholders[f"thesis/dissertation/chapter-contracts/{index:02d}-chapter-contract.md"] = (
                f"# Chapter {index} Research Contract\n\n"
                "## 1. Research Problem\n\n- Какой вопрос решает глава:\n\n"
                "## 2. Historiography Position\n\n- С кем спорит глава:\n\n"
                "## 3. Author Contribution\n\n- Что именно доказывает автор:\n"
            )
        for rel, body in dissertation_placeholders.items():
            path = work_dir / rel
            if not path.exists():
                path.write_text(body, encoding="utf-8")

    updated_workspace_text = register_work_in_workspace_toml(
        workspace_text_before,
        request.slug,
        rel_path,
        set_default=request.set_default,
    )
    workspace_toml.write_text(updated_workspace_text, encoding="utf-8")

    default_work_after = extract_default_work(updated_workspace_text)

    return WorkBootstrapResult(
        slug=request.slug,
        work_dir=work_dir,
        work_toml=work_toml_path,
        work_canon=work_canon_path,
        created_dirs=planned_dirs,
        workspace_toml=workspace_toml,
        set_default=request.set_default,
        default_work_after=default_work_after,
    )
