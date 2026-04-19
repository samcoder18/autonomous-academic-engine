from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any
import tomllib


SUPPORTED_LANES = ("thesis", "article")

THESIS_ACTION_PATTERNS = {
    "full-cycle": ("chapters/*.md", "sources/*.md", "manuscript/sections/*.md", "reviews/*.md"),
    "source-pack": ("sources/*.md",),
    "verify": ("chapters/*.md", "sources/*.md", "manuscript/sections/*.md", "reviews/*.md"),
    "write-section": ("manuscript/sections/*.md",),
    "review-section": ("manuscript/sections/*.md",),
    "style-pass": ("manuscript/sections/*.md",),
}

ARTICLE_ACTION_PATTERNS = {
    "article": ("briefs/*.md",),
    "article-brief": ("briefs/*.md",),
    "review": ("drafts/*.md", "final/*.md"),
    "repair": ("drafts/*.md", "final/*.md", "reviews/*.md"),
    "finalize": ("drafts/*.md", "final/*.md"),
}


class WorkspaceConfigError(RuntimeError):
    """Raised when the workspace or work configuration is invalid."""


@dataclass(frozen=True)
class LanePaths:
    root_dir: Path
    output_runs_dir: Path
    output_docx_dir: Path


@dataclass(frozen=True)
class ThesisBundleConfig:
    paths: LanePaths
    chapters_dir: Path
    sources_dir: Path
    ledgers_dir: Path
    manuscript_dir: Path
    manuscript_sections_dir: Path
    reviews_dir: Path
    sync_dir: Path
    full_draft_path: Path
    export_docx_path: Path
    section_order: tuple[Path, ...]


@dataclass(frozen=True)
class ArticleBundleConfig:
    paths: LanePaths
    briefs_dir: Path
    evidence_dir: Path
    claim_maps_dir: Path
    drafts_dir: Path
    reviews_dir: Path
    final_dir: Path


@dataclass(frozen=True)
class WorkConfig:
    slug: str
    title: str
    topic: str
    artifact_type: str
    language: str
    work_dir: Path
    work_canon_path: Path
    active_lanes: tuple[str, ...]
    thesis_profile: str | None
    article_profile: str | None
    thesis: ThesisBundleConfig | None
    article: ArticleBundleConfig | None

    def supports(self, lane: str) -> bool:
        return lane in self.active_lanes


@dataclass(frozen=True)
class WorkspaceConfig:
    root_dir: Path
    default_work: str
    supported_lanes: tuple[str, ...]
    default_profiles: dict[str, str]
    runs_root: Path
    docx_root: Path
    works: dict[str, Path]

    @property
    def workspace_file(self) -> Path:
        return self.root_dir / "workspace.toml"

    def has_work(self, slug: str) -> bool:
        return slug in self.works


@dataclass(frozen=True)
class WorkSelection:
    work: WorkConfig
    source: str


@dataclass(frozen=True)
class TargetResolution:
    raw_target: str
    normalized_path: str
    absolute_path: Path
    resolution_mode: str
    work_id: str
    work_source: str
    used_legacy_root_mapping: bool
    warning_code: str | None = None
    warning_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_target": self.raw_target,
            "normalized_path": self.normalized_path,
            "absolute_path": str(self.absolute_path),
            "resolution_mode": self.resolution_mode,
            "work_id": self.work_id,
            "work_source": self.work_source,
            "used_legacy_root_mapping": self.used_legacy_root_mapping,
            "warning_code": self.warning_code,
            "warning_message": self.warning_message,
        }


@dataclass(frozen=True)
class LegacyTargetMatch:
    prefix: str
    resolved_path: Path


@dataclass(frozen=True)
class LegacyTargetEntry:
    prefix: str
    resolved_path: Path
    lane: str
    field_name: str
    path_kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "prefix": self.prefix,
            "resolved_path": str(self.resolved_path),
            "lane": self.lane,
            "field_name": self.field_name,
            "path_kind": self.path_kind,
        }


def load_workspace_config(root_dir: str | Path) -> WorkspaceConfig:
    root = Path(root_dir).expanduser().resolve()
    workspace_file = root / "workspace.toml"
    if not workspace_file.exists():
        raise WorkspaceConfigError(f"Не найден workspace.toml: {workspace_file}")

    payload = _read_toml(workspace_file)
    default_work = _required_text(payload, "default_work", workspace_file)
    supported_lanes = _normalize_lanes(payload.get("supported_lanes"), workspace_file, "supported_lanes")
    default_profiles = _normalize_profile_map(payload.get("default_profiles"), workspace_file)

    outputs = payload.get("outputs")
    if not isinstance(outputs, dict):
        raise WorkspaceConfigError(f"В {workspace_file} отсутствует секция [outputs].")
    runs_root = _resolve_workspace_path(root, _required_text(outputs, "runs_dir", workspace_file))
    docx_root = _resolve_workspace_path(root, _required_text(outputs, "docx_dir", workspace_file))

    works_section = payload.get("works")
    if not isinstance(works_section, dict) or not works_section:
        raise WorkspaceConfigError(f"В {workspace_file} должна быть непустая секция [works].")

    works: dict[str, Path] = {}
    for slug, raw_value in works_section.items():
        if isinstance(raw_value, str):
            works[str(slug)] = _resolve_workspace_path(root, raw_value)
            continue
        if isinstance(raw_value, dict):
            works[str(slug)] = _resolve_workspace_path(root, _required_text(raw_value, "path", workspace_file))
            continue
        raise WorkspaceConfigError(f"Некорректное описание work `{slug}` в {workspace_file}.")

    if default_work not in works:
        raise WorkspaceConfigError(
            f"default_work `{default_work}` не найден в секции [works] файла {workspace_file}."
        )

    return WorkspaceConfig(
        root_dir=root,
        default_work=default_work,
        supported_lanes=supported_lanes,
        default_profiles=default_profiles,
        runs_root=runs_root,
        docx_root=docx_root,
        works=works,
    )


def load_work_config(workspace: WorkspaceConfig, slug: str) -> WorkConfig:
    if slug not in workspace.works:
        raise WorkspaceConfigError(f"Не найден work `{slug}` в {workspace.workspace_file}.")

    work_dir = workspace.works[slug]
    work_file = work_dir / "work.toml"
    if not work_file.exists():
        raise WorkspaceConfigError(f"Не найден work.toml: {work_file}")

    payload = _read_toml(work_file)
    active_lanes = _normalize_lanes(payload.get("active_lanes"), work_file, "active_lanes")
    work_slug = _required_text(payload, "slug", work_file)
    if work_slug != slug:
        raise WorkspaceConfigError(
            f"Slug `{work_slug}` в {work_file} не совпадает с ключом `{slug}` в workspace.toml."
        )

    work_canon_path = _resolve_work_path(work_dir, _required_text(payload, "work_canon", work_file))

    standards = payload.get("standards")
    if standards is not None and not isinstance(standards, dict):
        raise WorkspaceConfigError(f"Секция [standards] в {work_file} должна быть таблицей.")
    standards = standards or {}

    thesis_config = _build_thesis_config(workspace, work_dir, slug, payload.get("thesis"), work_file)
    article_config = _build_article_config(workspace, work_dir, slug, payload.get("article"), work_file)

    return WorkConfig(
        slug=work_slug,
        title=_required_text(payload, "title", work_file),
        topic=_required_text(payload, "topic", work_file),
        artifact_type=_required_text(payload, "artifact_type", work_file),
        language=_required_text(payload, "language", work_file),
        work_dir=work_dir,
        work_canon_path=work_canon_path,
        active_lanes=active_lanes,
        thesis_profile=_optional_text(standards.get("thesis_profile")),
        article_profile=_optional_text(standards.get("article_profile")),
        thesis=thesis_config,
        article=article_config,
    )


def resolve_work_config(
    workspace: WorkspaceConfig,
    *,
    work_id: str | None = None,
    target: str | Path | None = None,
) -> WorkConfig:
    return resolve_work_selection(workspace, work_id=work_id, target=target).work


def resolve_work_selection(
    workspace: WorkspaceConfig,
    *,
    work_id: str | None = None,
    target: str | Path | None = None,
) -> WorkSelection:
    chosen = _optional_text(work_id)
    if chosen:
        return WorkSelection(work=load_work_config(workspace, chosen), source="explicit")

    detected = detect_work_id_for_target(workspace, target)
    if detected:
        return WorkSelection(work=load_work_config(workspace, detected), source="detected")

    return WorkSelection(work=load_work_config(workspace, workspace.default_work), source="default")


def list_work_ids(workspace: WorkspaceConfig) -> list[str]:
    return sorted(workspace.works)


def detect_work_id_for_target(workspace: WorkspaceConfig, target: str | Path | None) -> str | None:
    if target is None:
        return None
    raw = str(target).strip()
    if not raw:
        return None

    raw_path = Path(raw).expanduser()
    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.append(workspace.root_dir / raw_path)
        if raw_path.parts[:1] == ("works",) and len(raw_path.parts) >= 2:
            return raw_path.parts[1]

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            continue
        for slug, work_dir in workspace.works.items():
            try:
                resolved.relative_to(work_dir)
            except ValueError:
                continue
            return slug
    return None


def list_targets_for_action(work: WorkConfig, lane: str, action: str, workspace: WorkspaceConfig) -> list[str]:
    lane = lane.strip().lower()
    action = action.strip().lower()
    if lane == "thesis":
        if not work.thesis:
            raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает thesis lane.")
        patterns = THESIS_ACTION_PATTERNS.get(action)
        if not patterns:
            raise WorkspaceConfigError(f"Неизвестное thesis-действие: {action}")
        return _merge_targets(
            _collect_bundle_targets(workspace.root_dir, work.thesis.paths.root_dir, patterns),
            _collect_workspace_targets(workspace.root_dir, patterns),
        )

    if lane == "article":
        if not work.article:
            raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает article lane.")
        patterns = ARTICLE_ACTION_PATTERNS.get(action)
        if not patterns:
            raise WorkspaceConfigError(f"Неизвестное article-действие: {action}")
        legacy_patterns = tuple(f"articles/{pattern}" for pattern in patterns)
        return _merge_targets(
            _collect_bundle_targets(workspace.root_dir, work.article.paths.root_dir, patterns),
            _collect_workspace_targets(workspace.root_dir, legacy_patterns),
        )

    raise WorkspaceConfigError(f"Неизвестный lane: {lane}")


def normalize_target_for_action(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    lane: str,
    action: str,
    raw_target: str,
) -> str:
    normalized = resolve_target_for_action(
        workspace,
        work,
        lane,
        action,
        raw_target,
    ).normalized_path
    return normalized


def resolve_target_for_action(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    lane: str,
    action: str,
    raw_target: str,
    *,
    work_source: str = "explicit",
) -> TargetResolution:
    resolution = resolve_target_path(workspace, work, raw_target, work_source=work_source)
    allowed = set(list_targets_for_action(work, lane, action, workspace))
    if resolution.normalized_path not in allowed:
        raise WorkspaceConfigError(
            f"Этот файл не подходит для сценария `{lane} / {action}` в work `{work.slug}`:\n{resolution.normalized_path}"
        )
    return resolution


def normalize_target_path(workspace: WorkspaceConfig, work: WorkConfig, raw_target: str) -> str:
    return resolve_target_path(workspace, work, raw_target).normalized_path


def resolve_target_path(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    raw_target: str,
    *,
    work_source: str = "explicit",
) -> TargetResolution:
    raw = raw_target.strip()
    if not raw:
        raise WorkspaceConfigError("Путь к target не может быть пустым.")

    raw_path = Path(raw).expanduser()
    candidates: list[tuple[str, Path, bool]] = []
    if raw_path.is_absolute():
        candidates.append(("absolute", raw_path, False))
    else:
        legacy_match = _match_legacy_target(work, raw)
        if legacy_match is not None:
            candidates.append(("legacy-root", legacy_match.resolved_path, True))
        candidates.append(("workspace-relative", workspace.root_dir / raw_path, False))
        candidates.append(("work-relative", work.work_dir / raw_path, False))

    seen: set[tuple[str, str]] = set()
    for resolution_mode, candidate, used_legacy_root_mapping in candidates:
        marker = (resolution_mode, str(candidate))
        if marker in seen:
            continue
        seen.add(marker)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            continue
        try:
            normalized = resolved.relative_to(workspace.root_dir).as_posix()
        except ValueError as exc:
            raise WorkspaceConfigError(f"Путь находится вне workspace:\n{raw_target}") from exc
        warning_code = None
        warning_message = None
        if used_legacy_root_mapping:
            warning_code = "legacy-root-target"
            warning_message = (
                f"Legacy target path `{raw}` resolved to `{normalized}`. "
                "Prefer the canonical `works/<slug>/...` path."
            )
        return TargetResolution(
            raw_target=raw,
            normalized_path=normalized,
            absolute_path=resolved,
            resolution_mode=resolution_mode,
            work_id=work.slug,
            work_source=work_source,
            used_legacy_root_mapping=used_legacy_root_mapping,
            warning_code=warning_code,
            warning_message=warning_message,
        )

    raise WorkspaceConfigError(f"Не найден файл: {raw_target}")


def derive_review_path(workspace: WorkspaceConfig, work: WorkConfig, target_rel: str) -> Path | None:
    if not work.thesis:
        return None
    target_path = workspace.root_dir / target_rel
    try:
        target_path.relative_to(work.thesis.manuscript_sections_dir)
    except ValueError:
        return None
    return work.thesis.reviews_dir / f"{target_path.stem}-review.md"


def derive_sync_path(work: WorkConfig, preset: str, target_rel: str) -> Path | None:
    if not work.thesis:
        return None
    base_name = Path(target_rel).stem
    return work.thesis.sync_dir / f"{{date}}-{preset}-{base_name}.md"


def article_bundle_paths(work: WorkConfig, slug: str) -> dict[str, Path]:
    if not work.article:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает article lane.")
    clean_slug = slug.strip()
    if not clean_slug:
        raise WorkspaceConfigError("Slug article bundle не может быть пустым.")
    return {
        "brief": work.article.briefs_dir / f"{clean_slug}.md",
        "evidence_pack": work.article.evidence_dir / f"{clean_slug}.md",
        "claim_map": work.article.claim_maps_dir / f"{clean_slug}.md",
        "draft": work.article.drafts_dir / f"{clean_slug}.md",
        "review": work.article.reviews_dir / f"{clean_slug}.md",
        "final_markdown": work.article.final_dir / f"{clean_slug}.md",
        "checklist": work.article.final_dir / f"{clean_slug}-checklist.md",
        "docx": work.article.paths.output_docx_dir / f"{clean_slug}.docx",
    }


def discover_article_slugs(work: WorkConfig) -> list[str]:
    if not work.article:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает article lane.")
    slugs: set[str] = set()
    folders = (
        work.article.briefs_dir,
        work.article.evidence_dir,
        work.article.claim_maps_dir,
        work.article.drafts_dir,
        work.article.reviews_dir,
        work.article.final_dir,
        work.article.paths.output_docx_dir,
        work.article.paths.root_dir / "runs",
    )
    for folder in folders:
        if not folder.exists():
            continue
        for path in folder.glob("*"):
            if path.name.startswith(".") or path.name == "README.md":
                continue
            if path.suffix == ".json" and path.name.endswith(".bundle.json"):
                slugs.add(path.name[: -len(".bundle.json")])
                continue
            if path.suffix == ".docx":
                slugs.add(path.stem)
                continue
            if path.suffix != ".md":
                continue
            stem = path.stem
            if stem.endswith("-checklist"):
                stem = stem[: -len("-checklist")]
            slugs.add(stem)
    return sorted(slugs)


def relative_to_workspace(workspace: WorkspaceConfig, path: Path) -> str:
    return path.resolve().relative_to(workspace.root_dir).as_posix()


def work_summary_dict(workspace: WorkspaceConfig, work: WorkConfig) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "slug": work.slug,
        "title": work.title,
        "topic": work.topic,
        "artifact_type": work.artifact_type,
        "language": work.language,
        "work_dir": relative_to_workspace(workspace, work.work_dir),
        "work_canon": relative_to_workspace(workspace, work.work_canon_path),
        "active_lanes": list(work.active_lanes),
    }
    if work.thesis:
        summary["thesis"] = {
            "root_dir": relative_to_workspace(workspace, work.thesis.paths.root_dir),
            "chapters_dir": relative_to_workspace(workspace, work.thesis.chapters_dir),
            "sources_dir": relative_to_workspace(workspace, work.thesis.sources_dir),
            "ledgers_dir": relative_to_workspace(workspace, work.thesis.ledgers_dir),
            "manuscript_sections_dir": relative_to_workspace(workspace, work.thesis.manuscript_sections_dir),
            "reviews_dir": relative_to_workspace(workspace, work.thesis.reviews_dir),
            "sync_dir": relative_to_workspace(workspace, work.thesis.sync_dir),
            "full_draft_path": relative_to_workspace(workspace, work.thesis.full_draft_path),
            "export_docx_path": relative_to_workspace(workspace, work.thesis.export_docx_path),
            "section_order": [relative_to_workspace(workspace, item) for item in work.thesis.section_order],
            "output_runs_dir": relative_to_workspace(workspace, work.thesis.paths.output_runs_dir),
        }
    if work.article:
        summary["article"] = {
            "root_dir": relative_to_workspace(workspace, work.article.paths.root_dir),
            "briefs_dir": relative_to_workspace(workspace, work.article.briefs_dir),
            "evidence_dir": relative_to_workspace(workspace, work.article.evidence_dir),
            "claim_maps_dir": relative_to_workspace(workspace, work.article.claim_maps_dir),
            "drafts_dir": relative_to_workspace(workspace, work.article.drafts_dir),
            "reviews_dir": relative_to_workspace(workspace, work.article.reviews_dir),
            "final_dir": relative_to_workspace(workspace, work.article.final_dir),
            "output_docx_dir": relative_to_workspace(workspace, work.article.paths.output_docx_dir),
            "output_runs_dir": relative_to_workspace(workspace, work.article.paths.output_runs_dir),
        }
    return summary


def _build_thesis_config(
    workspace: WorkspaceConfig,
    work_dir: Path,
    slug: str,
    payload: object,
    work_file: Path,
) -> ThesisBundleConfig | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise WorkspaceConfigError(f"Секция [thesis] в {work_file} должна быть таблицей.")

    root_dir = _resolve_work_path(work_dir, _required_text(payload, "root_dir", work_file))
    full_draft_path = _resolve_work_path(work_dir, _required_text(payload, "full_draft_path", work_file))
    section_items = payload.get("section_order")
    if not isinstance(section_items, list):
        raise WorkspaceConfigError(f"В {work_file} thesis.section_order должен быть списком.")

    docx_filename = _required_text(payload, "docx_filename", work_file)
    output_docx_dir = workspace.docx_root / slug

    lane_paths = LanePaths(
        root_dir=root_dir,
        output_runs_dir=workspace.runs_root / slug / "thesis",
        output_docx_dir=output_docx_dir,
    )
    return ThesisBundleConfig(
        paths=lane_paths,
        chapters_dir=_resolve_work_path(work_dir, _required_text(payload, "chapters_dir", work_file)),
        sources_dir=_resolve_work_path(work_dir, _required_text(payload, "sources_dir", work_file)),
        ledgers_dir=_resolve_work_path(work_dir, _optional_text(payload.get("ledgers_dir")) or "thesis/ledgers"),
        manuscript_dir=_resolve_work_path(work_dir, _required_text(payload, "manuscript_dir", work_file)),
        manuscript_sections_dir=_resolve_work_path(
            work_dir,
            _required_text(payload, "manuscript_sections_dir", work_file),
        ),
        reviews_dir=_resolve_work_path(work_dir, _required_text(payload, "reviews_dir", work_file)),
        sync_dir=_resolve_work_path(work_dir, _required_text(payload, "sync_dir", work_file)),
        full_draft_path=full_draft_path,
        export_docx_path=output_docx_dir / docx_filename,
        section_order=tuple(_resolve_work_path(work_dir, str(item)) for item in section_items),
    )


def _build_article_config(
    workspace: WorkspaceConfig,
    work_dir: Path,
    slug: str,
    payload: object,
    work_file: Path,
) -> ArticleBundleConfig | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise WorkspaceConfigError(f"Секция [article] в {work_file} должна быть таблицей.")

    docx_subdir = _required_text(payload, "docx_subdir", work_file)
    lane_paths = LanePaths(
        root_dir=_resolve_work_path(work_dir, _required_text(payload, "root_dir", work_file)),
        output_runs_dir=workspace.runs_root / slug / "article",
        output_docx_dir=workspace.docx_root / slug / docx_subdir,
    )
    return ArticleBundleConfig(
        paths=lane_paths,
        briefs_dir=_resolve_work_path(work_dir, _required_text(payload, "briefs_dir", work_file)),
        evidence_dir=_resolve_work_path(work_dir, _required_text(payload, "evidence_dir", work_file)),
        claim_maps_dir=_resolve_work_path(work_dir, _required_text(payload, "claim_maps_dir", work_file)),
        drafts_dir=_resolve_work_path(work_dir, _required_text(payload, "drafts_dir", work_file)),
        reviews_dir=_resolve_work_path(work_dir, _required_text(payload, "reviews_dir", work_file)),
        final_dir=_resolve_work_path(work_dir, _required_text(payload, "final_dir", work_file)),
    )


def _collect_bundle_targets(workspace_root: Path, bundle_root: Path, patterns: tuple[str, ...]) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(bundle_root.glob(pattern)):
            if path.name == "README.md" or path.name.startswith("."):
                continue
            rel = path.resolve().relative_to(workspace_root).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            targets.append(rel)
    return targets


def _collect_workspace_targets(workspace_root: Path, patterns: tuple[str, ...]) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(workspace_root.glob(pattern)):
            if path.name == "README.md" or path.name.startswith("."):
                continue
            rel = path.resolve().relative_to(workspace_root).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            targets.append(rel)
    return targets


def _merge_targets(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def _match_legacy_target(work: WorkConfig, raw_target: str) -> LegacyTargetMatch | None:
    normalized = raw_target.strip().lstrip("./")
    for entry in legacy_target_entries(work):
        prefix = entry.prefix
        destination = entry.resolved_path
        if normalized == prefix and destination.is_file():
            return LegacyTargetMatch(prefix=prefix, resolved_path=destination)
        if normalized.startswith(prefix):
            if destination.is_file():
                return LegacyTargetMatch(prefix=prefix, resolved_path=destination)
            suffix = normalized[len(prefix) :]
            return LegacyTargetMatch(prefix=prefix, resolved_path=destination / suffix)
    return None


def legacy_target_entries(work: WorkConfig) -> tuple[LegacyTargetEntry, ...]:
    entries: list[LegacyTargetEntry] = []
    if work.thesis:
        entries.extend(_derive_legacy_entries(work.thesis, lane="thesis", root_alias=None))
    if work.article:
        entries.extend(_derive_legacy_entries(work.article, lane="article", root_alias="articles"))
    deduped: list[LegacyTargetEntry] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.prefix in seen:
            continue
        seen.add(entry.prefix)
        deduped.append(entry)
    return tuple(deduped)


def legacy_target_prefixes(work: WorkConfig) -> tuple[str, ...]:
    return tuple(entry.prefix for entry in legacy_target_entries(work))


def _legacy_target_map(work: WorkConfig) -> dict[str, Path]:
    return {entry.prefix: entry.resolved_path for entry in legacy_target_entries(work)}


def _derive_legacy_entries(bundle: object, *, lane: str, root_alias: str | None) -> list[LegacyTargetEntry]:
    root_dir = getattr(getattr(bundle, "paths", None), "root_dir", None)
    if not isinstance(root_dir, Path):
        return []
    entries: list[LegacyTargetEntry] = []
    for field_info in fields(bundle):
        field_name = field_info.name
        if field_name in {"paths", "section_order", "export_docx_path"}:
            continue
        value = getattr(bundle, field_name)
        if not isinstance(value, Path):
            continue
        try:
            relative = value.resolve().relative_to(root_dir.resolve())
        except ValueError:
            continue
        path_kind = "dir" if field_name.endswith("_dir") else "file"
        prefix = _legacy_prefix_for_path(relative, root_alias=root_alias, is_dir=(path_kind == "dir"))
        if not prefix:
            continue
        entries.append(
            LegacyTargetEntry(
                prefix=prefix,
                resolved_path=value,
                lane=lane,
                field_name=field_name,
                path_kind=path_kind,
            )
        )
    return entries


def _legacy_prefix_for_path(relative: Path, *, root_alias: str | None, is_dir: bool) -> str | None:
    relative_text = relative.as_posix().strip(".")
    if not relative_text:
        prefix = root_alias or ""
    elif root_alias:
        prefix = f"{root_alias}/{relative_text}"
    else:
        prefix = relative_text
    prefix = prefix.strip("/")
    if not prefix:
        return None
    if is_dir:
        return prefix + "/"
    return prefix


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceConfigError(f"Не удалось разобрать TOML {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkspaceConfigError(f"Некорректный формат TOML в {path}.")
    return payload


def _required_text(payload: dict[str, Any], key: str, path: Path) -> str:
    value = payload.get(key)
    text = _optional_text(value)
    if text is None:
        raise WorkspaceConfigError(f"В {path} отсутствует обязательное поле `{key}`.")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_lanes(value: object, path: Path, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise WorkspaceConfigError(f"Поле `{field_name}` в {path} должно быть непустым списком.")
    result: list[str] = []
    for item in value:
        lane = _optional_text(item)
        if lane is None or lane not in SUPPORTED_LANES:
            raise WorkspaceConfigError(f"Некорректный lane `{item}` в {path}.")
        if lane not in result:
            result.append(lane)
    return tuple(result)


def _normalize_profile_map(value: object, path: Path) -> dict[str, str]:
    if not isinstance(value, dict):
        raise WorkspaceConfigError(f"Секция [default_profiles] в {path} должна быть таблицей.")
    result: dict[str, str] = {}
    for key, raw_value in value.items():
        lane = _optional_text(key)
        profile = _optional_text(raw_value)
        if lane is None or profile is None:
            raise WorkspaceConfigError(f"Некорректная секция [default_profiles] в {path}.")
        result[lane] = profile
    return result


def _resolve_workspace_path(root_dir: Path, raw: str) -> Path:
    return (root_dir / raw).resolve()


def _resolve_work_path(work_dir: Path, raw: str) -> Path:
    return (work_dir / raw).resolve()
