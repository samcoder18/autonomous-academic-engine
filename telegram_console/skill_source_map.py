from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib


SKILL_PATTERN = re.compile(r"`\$([a-z0-9-]+)`")


@dataclass(frozen=True)
class SkillSourceMapEntry:
    skill_name: str
    lane: str
    agent_path: str
    expected_external_skill_id: str
    expected_source_of_truth_path: str

    def resolved_agent_path(self, root_dir: Path) -> Path:
        return (root_dir / self.agent_path).resolve()


@dataclass(frozen=True)
class SkillSourceAuditIssue:
    code: str
    skill_name: str
    message: str


@dataclass(frozen=True)
class SkillSourceAuditReport:
    declared_skills: tuple[str, ...]
    entries: tuple[SkillSourceMapEntry, ...]
    issues: tuple[SkillSourceAuditIssue, ...]
    external_skill_files_checked: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class SkillSourceSyncItem:
    skill_name: str
    external_skill_id: str
    skill_file: str
    status: str
    changed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "skill_name": self.skill_name,
            "external_skill_id": self.external_skill_id,
            "skill_file": self.skill_file,
            "status": self.status,
            "changed": self.changed,
        }


@dataclass(frozen=True)
class SkillSourceSyncReport:
    external_skills_root: str
    write_applied: bool
    items: tuple[SkillSourceSyncItem, ...]
    missing_external_count: int
    update_candidate_count: int
    updated_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "skill-source-sync",
            "version": "v1",
            "external_skills_root": self.external_skills_root,
            "write_applied": self.write_applied,
            "missing_external_count": self.missing_external_count,
            "update_candidate_count": self.update_candidate_count,
            "updated_count": self.updated_count,
            "items": [item.to_dict() for item in self.items],
        }


def skills_declared_in_agents(root_dir: str | Path) -> tuple[str, ...]:
    root = Path(root_dir).expanduser().resolve()
    agents_path = root / "AGENTS.md"
    text = agents_path.read_text(encoding="utf-8")
    found: list[str] = []
    for skill_name in SKILL_PATTERN.findall(text):
        if skill_name not in found:
            found.append(skill_name)
    return tuple(found)


def load_skill_source_map(root_dir: str | Path) -> dict[str, SkillSourceMapEntry]:
    root = Path(root_dir).expanduser().resolve()
    manifest_path = root / "meta" / "skill-source-map.toml"
    with manifest_path.open("rb") as handle:
        payload = tomllib.load(handle)

    raw_skills = payload.get("skills")
    if not isinstance(raw_skills, dict):
        raise ValueError(f"Invalid skill source map: {manifest_path}")

    entries: dict[str, SkillSourceMapEntry] = {}
    for skill_name, raw_entry in raw_skills.items():
        if not isinstance(raw_entry, dict):
            raise ValueError(f"Invalid skill source map entry for {skill_name!r}: {manifest_path}")
        entries[str(skill_name)] = SkillSourceMapEntry(
            skill_name=str(skill_name),
            lane=_required_text(raw_entry, "lane", manifest_path, skill_name),
            agent_path=_required_text(raw_entry, "agent_path", manifest_path, skill_name),
            expected_external_skill_id=_required_text(
                raw_entry,
                "expected_external_skill_id",
                manifest_path,
                skill_name,
            ),
            expected_source_of_truth_path=_required_text(
                raw_entry,
                "expected_source_of_truth_path",
                manifest_path,
                skill_name,
            ),
        )
    return entries


def audit_skill_source_map(
    root_dir: str | Path,
    *,
    external_skills_root: str | Path | None = None,
) -> SkillSourceAuditReport:
    root = Path(root_dir).expanduser().resolve()
    declared_skills = skills_declared_in_agents(root)
    entries = load_skill_source_map(root)
    issues: list[SkillSourceAuditIssue] = []
    checked_external_files: list[str] = []

    for skill_name in declared_skills:
        if skill_name not in entries:
            issues.append(
                SkillSourceAuditIssue(
                    code="missing-manifest-entry",
                    skill_name=skill_name,
                    message=f"Skill `{skill_name}` is declared in AGENTS.md but missing from meta/skill-source-map.toml.",
                )
            )

    for skill_name, entry in entries.items():
        agent_path = entry.resolved_agent_path(root)
        if not agent_path.exists():
            issues.append(
                SkillSourceAuditIssue(
                    code="missing-agent-path",
                    skill_name=skill_name,
                    message=f"Skill `{skill_name}` points to missing agent path `{entry.agent_path}`.",
                )
            )

    if external_skills_root is not None:
        external_root = Path(external_skills_root).expanduser().resolve()
        for entry in entries.values():
            skill_file = external_root / entry.expected_external_skill_id / "SKILL.md"
            if not skill_file.exists():
                continue
            checked_external_files.append(str(skill_file))
            text = skill_file.read_text(encoding="utf-8")
            absolute_source_path = str((root / entry.agent_path).resolve())
            has_source_header = "Source of truth" in text
            has_expected_path = (
                entry.expected_source_of_truth_path in text
                or entry.agent_path in text
                or absolute_source_path in text
            )
            if not has_source_header or not has_expected_path:
                issues.append(
                    SkillSourceAuditIssue(
                        code="external-source-of-truth-missing",
                        skill_name=entry.skill_name,
                        message=(
                            f"External skill `{entry.expected_external_skill_id}` exists but does not expose "
                            "the expected Source of truth mapping."
                        ),
                    )
                )

    return SkillSourceAuditReport(
        declared_skills=declared_skills,
        entries=tuple(entries.values()),
        issues=tuple(issues),
        external_skill_files_checked=tuple(checked_external_files),
    )


def sync_external_skill_sources(
    root_dir: str | Path,
    external_skills_root: str | Path,
    *,
    write: bool = False,
) -> SkillSourceSyncReport:
    root = Path(root_dir).expanduser().resolve()
    external_root = Path(external_skills_root).expanduser().resolve()
    entries = load_skill_source_map(root)
    items: list[SkillSourceSyncItem] = []
    missing_external_count = 0
    update_candidate_count = 0
    updated_count = 0

    for entry in entries.values():
        skill_file = external_root / entry.expected_external_skill_id / "SKILL.md"
        if not skill_file.exists():
            missing_external_count += 1
            items.append(
                SkillSourceSyncItem(
                    skill_name=entry.skill_name,
                    external_skill_id=entry.expected_external_skill_id,
                    skill_file=str(skill_file),
                    status="missing-external-skill",
                    changed=False,
                )
            )
            continue

        original_text = skill_file.read_text(encoding="utf-8")
        updated_text = _upsert_source_of_truth_section(
            original_text,
            _render_source_of_truth_section(root, entry),
        )
        changed = updated_text != original_text
        status = "already-synced"
        if changed:
            update_candidate_count += 1
            status = "would-update"
            if write:
                skill_file.write_text(_ensure_trailing_newline(updated_text), encoding="utf-8")
                updated_count += 1
                status = "updated"

        items.append(
            SkillSourceSyncItem(
                skill_name=entry.skill_name,
                external_skill_id=entry.expected_external_skill_id,
                skill_file=str(skill_file),
                status=status,
                changed=changed,
            )
        )

    return SkillSourceSyncReport(
        external_skills_root=str(external_root),
        write_applied=write,
        items=tuple(items),
        missing_external_count=missing_external_count,
        update_candidate_count=update_candidate_count,
        updated_count=updated_count,
    )


def _required_text(payload: dict[str, object], key: str, manifest_path: Path, skill_name: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Invalid skill source map entry `{skill_name}` in {manifest_path}: missing `{key}`."
        )
    return value.strip()


def _render_source_of_truth_section(root_dir: Path, entry: SkillSourceMapEntry) -> str:
    absolute_agent_path = (root_dir / entry.agent_path).resolve()
    absolute_manifest_path = (root_dir / "meta" / "skill-source-map.toml").resolve()
    return (
        "## Source of truth\n\n"
        f"- Repo role doc: [{entry.agent_path}]({absolute_agent_path})\n"
        f"- Repo skill map: [meta/skill-source-map.toml]({absolute_manifest_path})\n"
        "- Update this external skill in the same PR or maintenance pass as the repo-side role doc.\n"
    )


def _upsert_source_of_truth_section(text: str, section: str) -> str:
    normalized = text.rstrip()
    pattern = re.compile(r"(?ms)^## Source of truth\s*\n.*?(?=^##\s|\Z)")
    replacement = section.rstrip()
    if pattern.search(normalized):
        return pattern.sub(replacement + "\n\n", normalized, count=1).rstrip() + "\n"
    if not normalized:
        return replacement + "\n"
    return normalized + "\n\n" + replacement + "\n"


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"
