from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request
import hashlib
import json
import mimetypes
import re
import tomllib

from .workspace import WorkConfig, WorkspaceConfig, WorkspaceConfigError, load_work_config, load_workspace_config


DEFAULT_FALLBACK_PROFILES = {
    "thesis": "thesis-v1",
    "article": "ru-law-article-v1",
}
SUPPORTED_WORKFLOW_LANES = {"thesis", "article"}


@dataclass(frozen=True)
class StandardSourceSpec:
    source_id: str
    label: str
    url: str
    source_date: str | None
    filename: str | None


@dataclass(frozen=True)
class StandardProfileSpec:
    profile_id: str
    workflow_lane: str | None
    unit_kind: str
    status: str
    normalized_path: Path
    raw_dir: Path
    official_only: bool
    conflict_flag: bool
    notes: tuple[str, ...]
    operative_source_id: str | None
    sources: tuple[StandardSourceSpec, ...]


@dataclass(frozen=True)
class StandardsRegistry:
    root_dir: Path
    registry_path: Path | None
    fallback_profiles: dict[str, str]
    profiles: dict[str, StandardProfileSpec]
    synthetic: bool = False


@dataclass(frozen=True)
class StandardProfileResolution:
    lane: str | None
    requested_profile_id: str
    resolved_profile_id: str
    fallback_profile_id: str | None
    normalized_path: Path
    raw_dir: Path
    raw_manifest_path: Path
    raw_status: str
    last_refresh_at: str | None
    official_only: bool
    conflict_flag: bool
    profile_status: str
    notes: tuple[str, ...]
    synthetic_registry: bool
    spec: StandardProfileSpec


@dataclass(frozen=True)
class StandardsSyncResult:
    operation: str
    profile_id: str
    resolution: StandardProfileResolution
    downloaded_count: int
    reused_count: int
    failed_count: int
    manifest_path: Path


def load_standards_registry(root_dir: str | Path) -> StandardsRegistry:
    root = Path(root_dir).expanduser().resolve()
    registry_path = root / "meta" / "standards" / "registry.toml"
    if not registry_path.exists():
        return _build_synthetic_registry(root)

    payload = _read_toml(registry_path)
    fallback_profiles = dict(DEFAULT_FALLBACK_PROFILES)
    raw_fallbacks = payload.get("fallback_profiles")
    if raw_fallbacks is not None:
        if not isinstance(raw_fallbacks, dict):
            raise WorkspaceConfigError(f"Секция [fallback_profiles] в {registry_path} должна быть таблицей.")
        for lane, profile_id in raw_fallbacks.items():
            lane_text = _optional_text(lane)
            profile_text = _optional_text(profile_id)
            if lane_text is None or profile_text is None:
                raise WorkspaceConfigError(f"Некорректная запись в [fallback_profiles] файла {registry_path}.")
            fallback_profiles[lane_text] = profile_text

    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise WorkspaceConfigError(f"В {registry_path} должна быть непустая секция [profiles].")

    profiles: dict[str, StandardProfileSpec] = {}
    for profile_id, raw_profile in raw_profiles.items():
        if not isinstance(raw_profile, dict):
            raise WorkspaceConfigError(f"Профиль `{profile_id}` в {registry_path} должен быть таблицей.")
        profiles[str(profile_id)] = _parse_profile_spec(root, registry_path, str(profile_id), raw_profile)

    return StandardsRegistry(
        root_dir=root,
        registry_path=registry_path,
        fallback_profiles=fallback_profiles,
        profiles=profiles,
        synthetic=False,
    )


def resolve_standard_profile(
    root_dir: str | Path,
    workspace: WorkspaceConfig,
    work: WorkConfig,
    *,
    lane: str,
    requested_profile_id: str | None,
) -> StandardProfileResolution:
    lane_text = lane.strip().lower()
    if lane_text not in SUPPORTED_WORKFLOW_LANES:
        raise WorkspaceConfigError(f"Некорректный workflow lane для standards resolver: {lane}")

    registry = load_standards_registry(root_dir)
    requested = (
        _optional_text(requested_profile_id)
        or (work.thesis_profile if lane_text == "thesis" else work.article_profile)
        or workspace.default_profiles.get(lane_text)
        or DEFAULT_FALLBACK_PROFILES[lane_text]
    )
    assert requested is not None

    spec = registry.profiles.get(requested)
    if spec and spec.workflow_lane != lane_text:
        raise WorkspaceConfigError(
            f"Профиль `{requested}` привязан к lane `{spec.workflow_lane or 'reference-only'}`, а не к `{lane_text}`."
        )
    if spec and spec.workflow_lane is None:
        raise WorkspaceConfigError(f"Профиль `{requested}` является reference-only и не может быть workflow binding.")

    fallback_profile_id: str | None = None
    if spec is None or not spec.normalized_path.exists():
        fallback_profile_id = _fallback_profile_id(registry, workspace, lane_text)
        fallback = registry.profiles.get(fallback_profile_id)
        if fallback is None:
            raise WorkspaceConfigError(f"Fallback profile `{fallback_profile_id}` не найден в реестре standards.")
        if not fallback.normalized_path.exists():
            raise WorkspaceConfigError(
                f"Fallback profile `{fallback_profile_id}` не может быть использован: отсутствует {fallback.normalized_path}"
            )
        spec = fallback

    raw_status, last_refresh_at = raw_status_for_profile(spec)
    return StandardProfileResolution(
        lane=lane_text,
        requested_profile_id=requested,
        resolved_profile_id=spec.profile_id,
        fallback_profile_id=fallback_profile_id,
        normalized_path=spec.normalized_path,
        raw_dir=spec.raw_dir,
        raw_manifest_path=spec.raw_dir / "manifest.json",
        raw_status=raw_status,
        last_refresh_at=last_refresh_at,
        official_only=spec.official_only,
        conflict_flag=spec.conflict_flag,
        profile_status=spec.status,
        notes=spec.notes,
        synthetic_registry=registry.synthetic,
        spec=spec,
    )


def resolve_status_profile(
    root_dir: str | Path,
    profile_id: str,
    workspace: WorkspaceConfig | None = None,
    work: WorkConfig | None = None,
) -> StandardProfileResolution:
    registry = load_standards_registry(root_dir)
    requested = profile_id.strip()
    spec = registry.profiles.get(requested)
    if spec is not None and spec.workflow_lane is None:
        raw_status, last_refresh_at = raw_status_for_profile(spec)
        return StandardProfileResolution(
            lane=None,
            requested_profile_id=requested,
            resolved_profile_id=spec.profile_id,
            fallback_profile_id=None,
            normalized_path=spec.normalized_path,
            raw_dir=spec.raw_dir,
            raw_manifest_path=spec.raw_dir / "manifest.json",
            raw_status=raw_status,
            last_refresh_at=last_refresh_at,
            official_only=spec.official_only,
            conflict_flag=spec.conflict_flag,
            profile_status=spec.status,
            notes=spec.notes,
            synthetic_registry=registry.synthetic,
            spec=spec,
        )

    lane = spec.workflow_lane if spec and spec.workflow_lane else "article"
    if workspace is None:
        workspace = _load_workspace_optional(root_dir)
    if work is None and workspace is not None:
        work = _load_default_work_optional(workspace)
    if workspace is None or work is None:
        raise WorkspaceConfigError("Невозможно определить workspace/work для standards status.")
    return resolve_standard_profile(root_dir, workspace, work, lane=lane, requested_profile_id=requested)


def sync_standard_profile(
    root_dir: str | Path,
    profile_id: str,
    *,
    force_refresh: bool,
) -> StandardsSyncResult:
    registry = load_standards_registry(root_dir)
    spec = registry.profiles.get(profile_id.strip())
    if spec is None:
        raise WorkspaceConfigError(f"Unknown standards profile: {profile_id}")

    spec.raw_dir.mkdir(parents=True, exist_ok=True)
    existing_manifest = _read_json(spec.raw_dir / "manifest.json")
    existing_entries = {
        str(item.get("source_id") or "").strip(): item
        for item in (existing_manifest or {}).get("sources", [])
        if isinstance(item, dict) and str(item.get("source_id") or "").strip()
    }

    manifest_sources: list[dict[str, Any]] = []
    downloaded_count = 0
    reused_count = 0
    failed_count = 0
    for source in spec.sources:
        existing_entry = existing_entries.get(source.source_id)
        if not force_refresh:
            reused_entry = _reuse_source_entry(spec.raw_dir, source, existing_entry)
            if reused_entry is not None:
                manifest_sources.append(reused_entry)
                reused_count += 1
                continue

        try:
            payload, final_url, content_type = fetch_url_bytes(source.url)
        except Exception as exc:
            manifest_sources.append(
                {
                    "source_id": source.source_id,
                    "label": source.label,
                    "url": source.url,
                    "final_url": None,
                    "content_type": None,
                    "fetched_at": _utc_now(),
                    "checksum_sha256": None,
                    "filename": None,
                    "size_bytes": 0,
                    "source_date": source.source_date,
                    "error": str(exc),
                }
            )
            failed_count += 1
            continue

        filename = source.filename or _derive_download_filename(source, final_url, content_type)
        target_path = spec.raw_dir / filename
        target_path.write_bytes(payload)
        manifest_sources.append(
            {
                "source_id": source.source_id,
                "label": source.label,
                "url": source.url,
                "final_url": final_url,
                "content_type": content_type,
                "fetched_at": _utc_now(),
                "checksum_sha256": hashlib.sha256(payload).hexdigest(),
                "filename": filename,
                "size_bytes": len(payload),
                "source_date": source.source_date,
                "error": None,
            }
        )
        downloaded_count += 1

    manifest_payload = {
        "profile_id": spec.profile_id,
        "synced_at": _utc_now(),
        "force_refresh": force_refresh,
        "sources": manifest_sources,
    }
    manifest_path = spec.raw_dir / "manifest.json"
    _write_json(manifest_path, manifest_payload)
    spec.normalized_path.parent.mkdir(parents=True, exist_ok=True)
    spec.normalized_path.write_text(_render_normalized_profile(registry.root_dir, spec, manifest_payload), encoding="utf-8")

    raw_status, last_refresh_at = raw_status_for_profile(spec)
    resolution = StandardProfileResolution(
        lane=spec.workflow_lane,
        requested_profile_id=spec.profile_id,
        resolved_profile_id=spec.profile_id,
        fallback_profile_id=None,
        normalized_path=spec.normalized_path,
        raw_dir=spec.raw_dir,
        raw_manifest_path=manifest_path,
        raw_status=raw_status,
        last_refresh_at=last_refresh_at,
        official_only=spec.official_only,
        conflict_flag=spec.conflict_flag,
        profile_status=spec.status,
        notes=spec.notes,
        synthetic_registry=registry.synthetic,
        spec=spec,
    )
    return StandardsSyncResult(
        operation="refresh" if force_refresh else "intake",
        profile_id=spec.profile_id,
        resolution=resolution,
        downloaded_count=downloaded_count,
        reused_count=reused_count,
        failed_count=failed_count,
        manifest_path=manifest_path,
    )


def fetch_url_bytes(url: str) -> tuple[bytes, str, str]:
    req = request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; Codex Standards Intake/1.0)",
            "Accept": "*/*",
        },
    )
    with request.urlopen(req, timeout=30) as response:
        data = response.read()
        final_url = response.geturl()
        content_type = response.headers.get_content_type() or _content_type_from_url(final_url)
    return data, final_url, content_type


def raw_status_for_profile(spec: StandardProfileSpec) -> tuple[str, str | None]:
    manifest_path = spec.raw_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest is not None:
        sources = manifest.get("sources", [])
        if isinstance(sources, list):
            for item in sources:
                if not isinstance(item, dict):
                    return "partial", _optional_text(manifest.get("synced_at"))
                filename = _optional_text(item.get("filename"))
                error = _optional_text(item.get("error"))
                if error is not None:
                    return "partial", _optional_text(manifest.get("synced_at"))
                if filename is not None and not (spec.raw_dir / filename).exists():
                    return "partial", _optional_text(manifest.get("synced_at"))
        return "available", _optional_text(manifest.get("synced_at"))
    if spec.raw_dir.exists() and any(path.name != "manifest.json" for path in spec.raw_dir.iterdir()):
        return "partial", None
    return "missing", None


def format_profile_resolution_lines(resolution: StandardProfileResolution) -> list[str]:
    lines = [
        f"Requested profile: {resolution.requested_profile_id}",
        f"Resolved profile: {resolution.resolved_profile_id}",
        f"Normalized file: {resolution.normalized_path}",
        f"Raw directory: {resolution.raw_dir}",
        f"Raw manifest: {resolution.raw_manifest_path}",
        f"Raw status: {resolution.raw_status}",
        f"Official-only: {'yes' if resolution.official_only else 'no'}",
        f"Conflict flag: {'yes' if resolution.conflict_flag else 'no'}",
        f"Profile status: {resolution.profile_status}",
        f"Last refresh: {resolution.last_refresh_at or 'not yet'}",
    ]
    if resolution.fallback_profile_id:
        lines.insert(2, f"Fallback profile: {resolution.fallback_profile_id}")
    if resolution.notes:
        lines.append("Notes:")
        lines.extend(f"- {note}" for note in resolution.notes)
    return lines


def format_registry_overview_lines(root_dir: str | Path) -> list[str]:
    registry = load_standards_registry(root_dir)
    lines = [
        f"Registry path: {registry.registry_path or 'synthetic legacy registry'}",
        f"Synthetic registry: {'yes' if registry.synthetic else 'no'}",
        "Profiles:",
    ]
    for profile_id in sorted(registry.profiles):
        spec = registry.profiles[profile_id]
        raw_status, last_refresh_at = raw_status_for_profile(spec)
        lane = spec.workflow_lane or "reference-only"
        lines.append(
            f"- {profile_id} [{lane}] status={spec.status} raw={raw_status} refreshed={last_refresh_at or 'not yet'}"
        )
    return lines


def _parse_profile_spec(
    root_dir: Path,
    registry_path: Path,
    profile_id: str,
    raw_profile: dict[str, Any],
) -> StandardProfileSpec:
    workflow_lane = _optional_text(raw_profile.get("workflow_lane"))
    if workflow_lane is not None and workflow_lane not in SUPPORTED_WORKFLOW_LANES:
        raise WorkspaceConfigError(
            f"Профиль `{profile_id}` в {registry_path} имеет некорректный workflow_lane `{workflow_lane}`."
        )
    raw_sources = raw_profile.get("sources", [])
    if not isinstance(raw_sources, list):
        raise WorkspaceConfigError(f"Поле sources профиля `{profile_id}` в {registry_path} должно быть списком.")

    sources: list[StandardSourceSpec] = []
    for index, raw_source in enumerate(raw_sources, start=1):
        if not isinstance(raw_source, dict):
            raise WorkspaceConfigError(f"Источник #{index} профиля `{profile_id}` в {registry_path} должен быть таблицей.")
        source_id = _optional_text(raw_source.get("id")) or _slugify_url(_required_text(raw_source, "url", registry_path))
        sources.append(
            StandardSourceSpec(
                source_id=source_id,
                label=_optional_text(raw_source.get("label")) or source_id,
                url=_required_text(raw_source, "url", registry_path),
                source_date=_optional_text(raw_source.get("date")),
                filename=_optional_text(raw_source.get("filename")),
            )
        )

    return StandardProfileSpec(
        profile_id=profile_id,
        workflow_lane=workflow_lane,
        unit_kind=_required_text(raw_profile, "unit_kind", registry_path),
        status=_required_text(raw_profile, "status", registry_path),
        normalized_path=_resolve_workspace_path(root_dir, _required_text(raw_profile, "normalized_path", registry_path)),
        raw_dir=_resolve_workspace_path(root_dir, _required_text(raw_profile, "raw_dir", registry_path)),
        official_only=bool(raw_profile.get("official_only", True)),
        conflict_flag=bool(raw_profile.get("conflict_flag", False)),
        notes=_normalize_notes(raw_profile.get("notes")),
        operative_source_id=_optional_text(raw_profile.get("operative_source_id")),
        sources=tuple(sources),
    )


def _build_synthetic_registry(root_dir: Path) -> StandardsRegistry:
    normalized_dir = root_dir / "meta" / "standards" / "normalized"
    fallback_profiles = dict(DEFAULT_FALLBACK_PROFILES)
    workspace_path = root_dir / "workspace.toml"
    if workspace_path.exists():
        payload = _read_toml(workspace_path)
        raw_defaults = payload.get("default_profiles")
        if isinstance(raw_defaults, dict):
            for lane, profile_id in raw_defaults.items():
                lane_text = _optional_text(lane)
                profile_text = _optional_text(profile_id)
                if lane_text and profile_text:
                    fallback_profiles[lane_text] = profile_text

    profiles: dict[str, StandardProfileSpec] = {}
    if normalized_dir.exists():
        for path in sorted(normalized_dir.glob("*.md")):
            if path.name == "README.md":
                continue
            profile_id = path.stem
            workflow_lane = None
            if profile_id == fallback_profiles.get("thesis"):
                workflow_lane = "thesis"
            elif profile_id == fallback_profiles.get("article"):
                workflow_lane = "article"
            profiles[profile_id] = StandardProfileSpec(
                profile_id=profile_id,
                workflow_lane=workflow_lane,
                unit_kind="legacy",
                status="provisional",
                normalized_path=path.resolve(),
                raw_dir=(root_dir / "meta" / "standards" / "raw" / profile_id).resolve(),
                official_only=True,
                conflict_flag=False,
                notes=("Synthetic registry entry derived from legacy normalized profiles.",),
                operative_source_id=None,
                sources=(),
            )

    return StandardsRegistry(
        root_dir=root_dir,
        registry_path=None,
        fallback_profiles=fallback_profiles,
        profiles=profiles,
        synthetic=True,
    )


def _reuse_source_entry(
    raw_dir: Path,
    source: StandardSourceSpec,
    existing_entry: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if existing_entry is not None:
        filename = _optional_text(existing_entry.get("filename"))
        if filename is not None and (raw_dir / filename).exists():
            return existing_entry

    fallback_filename = source.filename or _derive_download_filename(source, source.url, _content_type_from_url(source.url))
    candidate_path = raw_dir / fallback_filename
    if not candidate_path.exists():
        return None
    payload = candidate_path.read_bytes()
    return {
        "source_id": source.source_id,
        "label": source.label,
        "url": source.url,
        "final_url": source.url,
        "content_type": _guess_content_type(candidate_path),
        "fetched_at": _utc_now(),
        "checksum_sha256": hashlib.sha256(payload).hexdigest(),
        "filename": candidate_path.name,
        "size_bytes": len(payload),
        "source_date": source.source_date,
    }


def _render_normalized_profile(
    root_dir: Path,
    spec: StandardProfileSpec,
    manifest_payload: dict[str, Any],
) -> str:
    refreshed_at = _optional_text(manifest_payload.get("synced_at")) or "not yet"
    manifest_sources = {
        str(item.get("source_id") or "").strip(): item
        for item in manifest_payload.get("sources", [])
        if isinstance(item, dict)
    }
    operative_source = _pick_operative_source(spec)
    source_lines: list[str] = []
    for source in spec.sources:
        manifest_entry = manifest_sources.get(source.source_id, {})
        error = _optional_text(manifest_entry.get("error"))
        filename = _optional_text(manifest_entry.get("filename")) or (source.filename if error is None else None) or "not downloaded yet"
        final_url = _optional_text(manifest_entry.get("final_url")) or source.url
        source_lines.extend(
            [
                f"- `{source.source_id}`: {source.label}",
                f"  - URL: {source.url}",
                f"  - Final URL: {final_url}",
                f"  - Source date: {source.source_date or 'not specified'}",
                f"  - Local file: {relative_to_root(root_dir, spec.raw_dir / filename) if filename != 'not downloaded yet' else filename}",
            ]
        )
        if error is not None:
            source_lines.append(f"  - Refresh error: {error}")

    notes_block = "\n".join(f"- {note}" for note in spec.notes) if spec.notes else "- None."
    operative_text = operative_source.label if operative_source is not None else "No dated source precedence declared."
    conflict_text = (
        f"Newest declared source is operative: `{operative_source.source_id}` ({operative_text})."
        if spec.conflict_flag and operative_source is not None
        else "No conflict flag declared in the registry metadata."
    )
    return "\n".join(
        [
            f"# Profile: {spec.profile_id}",
            "",
            "## 1. Identity",
            "",
            f"- Profile ID: `{spec.profile_id}`",
            f"- Workflow lane: `{spec.workflow_lane or 'reference-only'}`",
            f"- Unit kind: `{spec.unit_kind}`",
            f"- Status: `{spec.status}`",
            f"- Official-only sources: `{'yes' if spec.official_only else 'no'}`",
            "",
            "## 2. Scope And Applicability",
            "",
            f"- Normalized file: `{relative_to_root(root_dir, spec.normalized_path)}`",
            f"- Raw directory: `{relative_to_root(root_dir, spec.raw_dir)}`",
            "- Applicability notes:",
            notes_block,
            "",
            "## 3. Official Sources",
            "",
            *(source_lines or ["- No sources declared in the registry."]),
            "",
            "## 4. Operative Precedence And Conflict Flag",
            "",
            f"- Conflict flag: `{'yes' if spec.conflict_flag else 'no'}`",
            f"- Operative precedence: {conflict_text}",
            "",
            "## 5. Refresh State",
            "",
            f"- Last refresh: `{refreshed_at}`",
            f"- Manifest: `{relative_to_root(root_dir, spec.raw_dir / 'manifest.json')}`",
            "",
            "## 6. Workflow Notes",
            "",
            f"- Thesis/article workflows may bind this profile only when lane compatibility is explicit. Current lane: `{spec.workflow_lane or 'reference-only'}`.",
            "- Stable mode default is preserved: the profile is not refreshed automatically during workflow runs.",
            "",
            "## 7. Finalization Impact",
            "",
            "- Finalizer may rely on this normalized profile only together with the corresponding raw bundle state.",
            "- Missing or partial raw material remains a blocker for claiming full formal compliance.",
            "- If conflict metadata is flagged, the newest declared source stays operative but the checklist must preserve the conflict note.",
            "",
        ]
    )


def relative_to_root(root_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root_dir).as_posix()
    except ValueError:
        return str(path.resolve())


def _pick_operative_source(spec: StandardProfileSpec) -> StandardSourceSpec | None:
    if spec.operative_source_id:
        for source in spec.sources:
            if source.source_id == spec.operative_source_id:
                return source
    dated = [source for source in spec.sources if source.source_date]
    if dated:
        return sorted(dated, key=lambda item: item.source_date or "")[-1]
    return spec.sources[-1] if spec.sources else None


def _derive_download_filename(source: StandardSourceSpec, final_url: str, content_type: str) -> str:
    explicit = _optional_text(source.filename)
    if explicit is not None:
        return explicit
    parsed = parse.urlparse(final_url)
    basename = Path(parsed.path).name
    suffix = Path(basename).suffix
    if basename and suffix:
        return _sanitize_filename(basename)
    ext = mimetypes.guess_extension(content_type) or Path(parse.urlparse(source.url).path).suffix or ".bin"
    return _sanitize_filename(f"{source.source_id}{ext}")


def _sanitize_filename(raw: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
    return clean or "source.bin"


def _content_type_from_url(url: str) -> str:
    path = parse.urlparse(url).path
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _guess_content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _fallback_profile_id(registry: StandardsRegistry, workspace: WorkspaceConfig, lane: str) -> str:
    return (
        registry.fallback_profiles.get(lane)
        or workspace.default_profiles.get(lane)
        or DEFAULT_FALLBACK_PROFILES[lane]
    )


def _load_workspace_optional(root_dir: str | Path) -> WorkspaceConfig | None:
    try:
        return load_workspace_config(root_dir)
    except WorkspaceConfigError:
        return None


def _load_default_work_optional(workspace: WorkspaceConfig) -> WorkConfig | None:
    if not workspace.default_work:
        return None
    try:
        return load_work_config(workspace, workspace.default_work)
    except WorkspaceConfigError:
        return None


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceConfigError(f"Не удалось разобрать TOML {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkspaceConfigError(f"Некорректный формат TOML в {path}.")
    return payload


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkspaceConfigError(f"Некорректный JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkspaceConfigError(f"Некорректный JSON-объект в {path}.")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _required_text(payload: dict[str, Any], key: str, path: Path) -> str:
    value = _optional_text(payload.get(key))
    if value is None:
        raise WorkspaceConfigError(f"В {path} отсутствует обязательное поле `{key}`.")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_notes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        result = []
        for item in value:
            text = _optional_text(item)
            if text is not None:
                result.append(text)
        return tuple(result)
    text = _optional_text(value)
    return (text,) if text is not None else ()


def _resolve_workspace_path(root_dir: Path, raw: str) -> Path:
    return (root_dir / raw).resolve()


def _slugify_url(url: str) -> str:
    parts = [part for part in re.split(r"[^a-z0-9]+", parse.urlparse(url).path.lower()) if part]
    return "-".join(parts[-4:]) or "source"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
