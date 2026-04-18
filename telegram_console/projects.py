from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import re
import sys
import tempfile
import unicodedata

from .orchestrator import RunRecord, WorkflowError, WorkflowOrchestrator
from .runtime_status import RuntimeRecord, attachment_path, load_runtime_record
from .state import RuntimeStore
from .workspace import WorkConfig, WorkspaceConfig, WorkspaceConfigError, list_work_ids, load_work_config, load_workspace_config


PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SUPPORTED_CAPABILITIES = ("thesis", "article")
CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    "і": "i",
    "ї": "yi",
    "є": "ye",
    "ґ": "g",
    "ў": "u",
}


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    title: str
    root_dir: Path
    capabilities: tuple[str, ...]
    works: tuple[str, ...] = ()
    default_work: str | None = None
    available: bool = True
    problems: tuple[str, ...] = ()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True)
class ProjectRegistrationResult:
    project: ProjectRecord
    created: bool


class ProjectRegistry:
    def __init__(self, bot_home_dir: str | Path):
        self.bot_home_dir = Path(bot_home_dir).resolve()
        self.projects_file = self.bot_home_dir / "output" / "telegram" / "projects.json"
        self.projects_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[ProjectRecord]:
        self._bootstrap_if_missing()
        items = self._extract_items(self._read_payload())
        return [self._build_record(item, index) for index, item in enumerate(items, start=1)]

    def register_project(self, title: str, root_dir: str | Path) -> ProjectRegistrationResult:
        resolved_root = Path(root_dir).expanduser().resolve()
        title_clean = title.strip()
        if not title_clean:
            title_clean = resolved_root.name or "Проект"

        items = self._extract_items(self._read_payload())
        duplicate = self._find_item_by_root(items, resolved_root)
        if duplicate is not None:
            return ProjectRegistrationResult(
                project=self._build_record(duplicate["item"], duplicate["index"]),
                created=False,
            )

        capabilities, problems = self.inspect_root(resolved_root)
        if not capabilities:
            details = "\n".join(f"- {item}" for item in problems)
            raise WorkflowError(
                "\n".join(
                    [
                        "Этот проект пока нельзя добавить ⚠️",
                        f"Путь: {resolved_root}",
                        details or "- Не удалось определить supported capabilities.",
                    ]
                )
            )

        project_id = self._generate_project_id(
            title_clean,
            resolved_root.name,
            existing_ids=self._existing_ids(items),
        )
        workspace_info = self._inspect_workspace_root(resolved_root)
        item = {
            "id": project_id,
            "title": title_clean,
            "root_dir": str(resolved_root),
            "capabilities": list(capabilities),
            "works": list(workspace_info.get("works") or []),
            "default_work": workspace_info.get("default_work"),
        }
        items.append(item)
        self._write_payload(items)
        return ProjectRegistrationResult(
            project=self._build_record(item, len(items)),
            created=True,
        )

    def inspect_root(self, root_dir: str | Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
        resolved_root = Path(root_dir).expanduser().resolve()
        if not resolved_root.exists():
            return (), (f"Папка проекта не найдена: {resolved_root}",)

        info = self._inspect_workspace_root(resolved_root)
        capabilities = tuple(info.get("capabilities") or ())
        problems = tuple(info.get("problems") or ())
        if capabilities:
            return capabilities, ()
        return (), problems

    def _read_payload(self) -> object:
        if not self.projects_file.exists():
            return {"projects": []}
        try:
            return json.loads(self.projects_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"projects": []}

    def _bootstrap_if_missing(self) -> None:
        if self.projects_file.exists():
            return
        info = self._inspect_workspace_root(self.bot_home_dir)
        capabilities = list(info.get("capabilities") or [])
        if not capabilities:
            return
        payload = {
            "projects": [
                {
                    "id": "default",
                    "title": self.bot_home_dir.name or "Проект",
                    "root_dir": str(self.bot_home_dir),
                    "capabilities": capabilities,
                    "works": list(info.get("works") or []),
                    "default_work": info.get("default_work"),
                }
            ]
        }
        self.projects_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _extract_items(self, payload: object) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            items = payload.get("projects") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        return [item for item in items if isinstance(item, dict)]

    def _write_payload(self, items: list[dict[str, Any]]) -> None:
        payload = {"projects": items}
        self.projects_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(self.projects_file.parent),
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temp_name = handle.name
        Path(temp_name).replace(self.projects_file)

    def _find_item_by_root(
        self,
        items: list[dict[str, Any]],
        root_dir: Path,
    ) -> dict[str, Any] | None:
        for index, item in enumerate(items, start=1):
            raw_root = str(item.get("root_dir") or "").strip()
            if not raw_root:
                continue
            candidate = Path(raw_root).expanduser().resolve()
            if candidate == root_dir:
                return {"item": item, "index": index}
        return None

    def _existing_ids(self, items: list[dict[str, Any]]) -> set[str]:
        result: set[str] = set()
        for item in items:
            raw_id = str(item.get("id") or "").strip()
            if raw_id:
                result.add(raw_id)
        return result

    def _generate_project_id(self, title: str, fallback_name: str, existing_ids: set[str]) -> str:
        base = self._slugify_id_source(title) or self._slugify_id_source(fallback_name) or "project"
        base = base[:32].rstrip("-") or "project"
        if base not in existing_ids and PROJECT_ID_RE.fullmatch(base):
            return base

        index = 2
        while True:
            suffix = f"-{index}"
            trimmed = base[: 32 - len(suffix)].rstrip("-") or "project"[: 32 - len(suffix)].rstrip("-") or "p"
            candidate = f"{trimmed}{suffix}"
            if candidate not in existing_ids and PROJECT_ID_RE.fullmatch(candidate):
                return candidate
            index += 1

    def _slugify_id_source(self, value: str) -> str:
        transliterated: list[str] = []
        for char in value.casefold():
            if char in CYRILLIC_TO_LATIN:
                transliterated.append(CYRILLIC_TO_LATIN[char])
                continue
            normalized = unicodedata.normalize("NFKD", char)
            ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
            transliterated.append(ascii_only or char)
        slug = re.sub(r"[^a-z0-9]+", "-", "".join(transliterated).lower())
        slug = re.sub(r"-{2,}", "-", slug).strip("-")
        return slug

    def _build_record(self, item: object, index: int) -> ProjectRecord:
        problems: list[str] = []
        if not isinstance(item, dict):
            return ProjectRecord(
                id=f"invalid-{index}",
                title=f"Некорректная запись #{index}",
                root_dir=self.bot_home_dir,
                capabilities=("thesis",),
                works=(),
                default_work=None,
                available=False,
                problems=("Ожидался объект с полями id, title, root_dir и capabilities.",),
            )

        raw_id = str(item.get("id") or "").strip()
        project_id = raw_id or f"project-{index}"
        if not PROJECT_ID_RE.fullmatch(project_id):
            problems.append("Идентификатор должен быть коротким ASCII-ключом вида `law-thesis-1`.")
            project_id = f"invalid-{index}"

        title = str(item.get("title") or f"Проект {index}").strip() or f"Проект {index}"
        root_raw = str(item.get("root_dir") or "").strip()
        if not root_raw:
            problems.append("Не указан `root_dir`.")
            root_dir = self.bot_home_dir
        else:
            root_dir = Path(root_raw).expanduser().resolve()

        raw_capabilities = item.get("capabilities")
        capabilities = self._normalize_capabilities(raw_capabilities)
        if not capabilities:
            capabilities = ()

        raw_works = item.get("works")
        works = self._normalize_works(raw_works)
        default_work = str(item.get("default_work") or "").strip() or None

        if not root_dir.exists():
            problems.append(f"Папка проекта не найдена: {root_dir}")
        else:
            info = self._inspect_workspace_root(root_dir)
            actual_capabilities = tuple(info.get("capabilities") or ())
            actual_works = tuple(info.get("works") or ())
            actual_default_work = info.get("default_work")
            actual_problems = tuple(info.get("problems") or ())
            if actual_problems:
                problems.extend(actual_problems)
            if actual_capabilities:
                capabilities = actual_capabilities
            if actual_works:
                works = actual_works
            default_work = actual_default_work or default_work

        return ProjectRecord(
            id=project_id,
            title=title,
            root_dir=root_dir,
            capabilities=capabilities,
            works=works,
            default_work=default_work,
            available=not problems,
            problems=tuple(problems),
        )

    def _normalize_capabilities(self, raw_capabilities: object) -> tuple[str, ...]:
        if raw_capabilities is None:
            return ()
        if isinstance(raw_capabilities, str):
            values = [raw_capabilities]
        elif isinstance(raw_capabilities, list):
            values = [str(item) for item in raw_capabilities]
        else:
            return ()

        normalized: list[str] = []
        for value in values:
            capability = value.strip().lower()
            if capability in SUPPORTED_CAPABILITIES and capability not in normalized:
                normalized.append(capability)
        return tuple(normalized)

    def _normalize_works(self, raw_works: object) -> tuple[str, ...]:
        if raw_works is None:
            return ()
        if isinstance(raw_works, str):
            values = [raw_works]
        elif isinstance(raw_works, list):
            values = [str(item) for item in raw_works]
        else:
            return ()

        normalized: list[str] = []
        for value in values:
            text = value.strip()
            if text and text not in normalized:
                normalized.append(text)
        return tuple(normalized)

    def _inspect_workspace_root(self, root_dir: Path) -> dict[str, object]:
        problems: list[str] = []
        checks = [
            (root_dir / "workspace.toml", "Не найден `workspace.toml`."),
            (root_dir / "AGENTS.md", "Не найден `AGENTS.md`."),
            (root_dir / "scripts" / "codex_thesis.sh", "Не найден `scripts/codex_thesis.sh`."),
            (root_dir / "scripts" / "codex_academic.sh", "Не найден `scripts/codex_academic.sh`."),
        ]
        for path, message in checks:
            if not path.exists():
                problems.append(message)

        if problems:
            return {"capabilities": (), "works": (), "default_work": None, "problems": tuple(problems)}

        try:
            workspace = load_workspace_config(root_dir)
        except WorkspaceConfigError as exc:
            return {
                "capabilities": (),
                "works": (),
                "default_work": None,
                "problems": (str(exc),),
            }

        capabilities: list[str] = []
        work_ids: list[str] = []
        for work_id in list_work_ids(workspace):
            try:
                work = load_work_config(workspace, work_id)
            except WorkspaceConfigError as exc:
                problems.append(str(exc))
                continue
            work_ids.append(work.slug)
            for lane in work.active_lanes:
                if lane not in capabilities:
                    capabilities.append(lane)

        if not work_ids:
            problems.append("В workspace не найдено ни одного валидного work bundle.")

        return {
            "capabilities": tuple(capabilities),
            "works": tuple(work_ids),
            "default_work": workspace.default_work,
            "problems": tuple(dict.fromkeys(problems)),
        }


class ProjectService:
    def __init__(
        self,
        bot_home_dir: str | Path,
        *,
        codex_bin: str | None = None,
        codex_model: str | None = None,
        python_executable: str | None = None,
        store: RuntimeStore | None = None,
        registry: ProjectRegistry | None = None,
    ):
        self.bot_home_dir = Path(bot_home_dir).resolve()
        self.store = store or RuntimeStore(self.bot_home_dir)
        self.registry = registry or ProjectRegistry(self.bot_home_dir)
        self.codex_bin = codex_bin
        self.codex_model = codex_model
        self.python_executable = python_executable or sys.executable
        self._orchestrators: dict[str, WorkflowOrchestrator] = {}
        self._workspace_cache: dict[str, WorkspaceConfig] = {}
        self._work_cache: dict[tuple[str, str], WorkConfig] = {}

    @property
    def projects_file(self) -> Path:
        return self.registry.projects_file

    def list_projects(self) -> list[ProjectRecord]:
        return self.registry.load()

    def register_project(self, title: str, root_dir: str | Path) -> ProjectRegistrationResult:
        return self.registry.register_project(title, root_dir)

    def inspect_project_root(self, root_dir: str | Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return self.registry.inspect_root(root_dir)

    def get_project(self, project_id: str) -> ProjectRecord | None:
        for project in self.list_projects():
            if project.id == project_id:
                return project
        return None

    def get_active_project(self, capability: str | None = None) -> ProjectRecord | None:
        active_id = self.store.get_active_project_id()
        projects = self.list_projects()
        project_map = {project.id: project for project in projects}
        active = project_map.get(active_id) if active_id else None
        if active and active.available and (capability is None or active.supports(capability)):
            return active
        if active and active.available and capability and not active.supports(capability):
            return None

        candidates = [
            project
            for project in projects
            if project.available and (capability is None or project.supports(capability))
        ]
        if len(candidates) == 1 and not active_id:
            self.store.set_active_project_id(candidates[0].id)
            if candidates[0].default_work:
                self.store.set_active_work_id(candidates[0].id, candidates[0].default_work)
            return candidates[0]
        return None

    def set_active_project(self, project_id: str) -> ProjectRecord:
        project = self.get_project(project_id)
        if not project:
            raise WorkflowError(f"Не нашла проект с id `{project_id}`.")
        if not project.available:
            details = "\n".join(f"- {item}" for item in project.problems)
            raise WorkflowError(
                "\n".join(
                    [
                        f"Проект `{project.id}` пока недоступен ⚠️",
                        details or "Проверь запись в `output/telegram/projects.json`.",
                    ]
                )
            )
        self.store.set_active_project_id(project.id)
        active_work = self.store.get_active_work_id(project.id)
        if not active_work or active_work not in project.works:
            default_work = project.default_work or (project.works[0] if project.works else None)
            if default_work:
                self.store.set_active_work_id(project.id, default_work)
        return project

    def list_targets(self, project_id: str, lane: str, action: str) -> list[str]:
        project = self.require_project(project_id, capability=lane)
        work = self.get_active_work(project.id, required_lane=lane)
        return self._get_orchestrator(project).list_targets(lane, action, work_id=work.slug)

    def list_article_slugs(self, project_id: str) -> list[str]:
        project = self.require_project(project_id, capability="article")
        work = self.get_active_work(project.id, required_lane="article")
        return self._get_orchestrator(project).list_article_slugs(work_id=work.slug)

    def list_thesis_sections(self, project_id: str) -> list[str]:
        project = self.require_project(project_id, capability="thesis")
        work = self.get_active_work(project.id, required_lane="thesis")
        return self._get_orchestrator(project).list_thesis_sections(work_id=work.slug)

    def list_works(self, project_id: str) -> list[WorkConfig]:
        project = self.require_project(project_id)
        workspace = self._get_workspace(project)
        result: list[WorkConfig] = []
        for work_id in project.works:
            try:
                result.append(self._get_work(project, work_id))
            except WorkflowError:
                continue
        if not result:
            for work_id in list_work_ids(workspace):
                try:
                    result.append(self._get_work(project, work_id))
                except WorkflowError:
                    continue
        return result

    def get_work(self, project_id: str, work_id: str) -> WorkConfig | None:
        project = self.get_project(project_id)
        if not project or not project.available:
            return None
        try:
            return self._get_work(project, work_id)
        except WorkflowError:
            return None

    def get_active_work(self, project_id: str, required_lane: str | None = None) -> WorkConfig:
        project = self.require_project(project_id)
        active_work_id = self.store.get_active_work_id(project.id)
        if active_work_id:
            try:
                work = self._get_work(project, active_work_id)
            except WorkflowError:
                self.store.clear_active_work_id(project.id)
            else:
                if required_lane is None or work.supports(required_lane):
                    return work

        default_work_id = project.default_work or (project.works[0] if project.works else None)
        if default_work_id:
            try:
                work = self._get_work(project, default_work_id)
            except WorkflowError:
                pass
            else:
                if required_lane is None or work.supports(required_lane):
                    self.store.set_active_work_id(project.id, work.slug)
                    return work

        for work in self.list_works(project.id):
            if required_lane is None or work.supports(required_lane):
                self.store.set_active_work_id(project.id, work.slug)
                return work

        if required_lane:
            raise WorkflowError(f"В проекте `{project.title}` нет work bundle с lane `{required_lane}`.")
        raise WorkflowError(f"В проекте `{project.title}` не найдено доступных work bundle.")

    def set_active_work(self, project_id: str, work_id: str) -> WorkConfig:
        project = self.require_project(project_id)
        work = self._get_work(project, work_id)
        self.store.set_active_work_id(project.id, work.slug)
        return work

    def start_run(
        self,
        project_id: str,
        lane: str,
        action: str,
        target_or_topic: str,
        notes: str | None = None,
        search_override: bool | None = None,
        model_override: str | None = None,
    ) -> dict[str, object]:
        project = self.require_project(project_id, capability=lane)
        work = self.get_active_work(project.id, required_lane=lane)
        return self._get_orchestrator(project).start_run(
            lane,
            action,
            target_or_topic,
            notes=notes,
            search_override=search_override,
            model_override=model_override,
            work_id=work.slug,
        )

    def sync_active_run(self) -> list[RunRecord]:
        active = self.store.get_active_run()
        if not active:
            return []
        orchestrator = self._resolve_orchestrator_for_payload(active)
        if not orchestrator:
            return []
        return orchestrator.sync_active_run()

    def drain_notifications(self) -> list[RunRecord]:
        return [RunRecord(**item) for item in self.store.pop_notifications()]

    def list_recent_runs(self, project_id: str, lane: str = "all", limit: int = 8) -> list[RunRecord]:
        project = self.require_project(project_id)
        work = self.get_active_work(project.id)
        return self._get_orchestrator(project).list_recent_runs(lane, limit, work_id=work.slug)

    def get_artifact_status(self, project_id: str, subject: str) -> dict[str, object]:
        project = self.require_project(project_id)
        work = self.get_active_work(project.id)
        return self._get_orchestrator(project).get_artifact_status(subject, work_id=work.slug)

    def get_work_state(self, project_id: str) -> dict[str, object]:
        project = self.require_project(project_id)
        work = self.get_active_work(project.id)
        return self._get_orchestrator(project).get_work_state(work_id=work.slug)

    def export_docx(self, project_id: str, subject: str) -> dict[str, object]:
        project = self.require_project(project_id)
        work = self.get_active_work(project.id)
        return self._get_orchestrator(project).export_docx(subject, work_id=work.slug)

    def find_run_record(self, record_id: str, project_id: str | None = None) -> RunRecord | None:
        orchestrator = self._resolve_orchestrator_for_record(record_id, project_id)
        if not orchestrator:
            return None
        return orchestrator.find_run_record(record_id)

    def get_run_attachment(
        self,
        record_id: str,
        attachment: str,
        project_id: str | None = None,
    ) -> Path | None:
        orchestrator = self._resolve_orchestrator_for_record(record_id, project_id)
        if not orchestrator:
            return None
        return orchestrator.get_run_attachment(record_id, attachment)

    def list_runtime_records(
        self,
        *,
        project_id: str | None = None,
        kind: str = "all",
        limit: int = 8,
    ) -> list[RuntimeRecord]:
        kind_text = kind.strip().lower()
        if kind_text not in {"all", "workflow", "chat"}:
            raise WorkflowError(f"Неизвестный runtime kind: {kind}")

        projects = self._runtime_projects(project_id)
        allowed_ids = {project.id for project in projects}
        allowed_roots = {str(project.root_dir) for project in projects}
        records: list[RuntimeRecord] = []

        if kind_text in {"all", "workflow"}:
            for run_dir in self.store.list_run_dirs():
                record = load_runtime_record(run_dir, "workflow-run")
                if record and self._runtime_record_matches(record, allowed_ids, allowed_roots):
                    records.append(record)

        if kind_text in {"all", "chat"}:
            for task_dir in self.store.list_agent_task_dirs():
                record = load_runtime_record(task_dir, "chat-turn")
                if record and self._runtime_record_matches(record, allowed_ids, allowed_roots):
                    records.append(record)

        return sorted(records, key=lambda item: item.sort_key, reverse=True)[:limit]

    def find_runtime_record(
        self,
        record_id: str,
        *,
        project_id: str | None = None,
    ) -> RuntimeRecord | None:
        for record in self.list_runtime_records(project_id=project_id, kind="all", limit=200):
            if record.record_id == record_id:
                return record
        return None

    def get_runtime_attachment(
        self,
        record_id: str,
        attachment: str,
        *,
        project_id: str | None = None,
    ) -> Path | None:
        record = self.find_runtime_record(record_id, project_id=project_id)
        if not record:
            return None
        return attachment_path(record, attachment)

    def require_project(self, project_id: str, capability: str | None = None) -> ProjectRecord:
        project = self.get_project(project_id)
        if not project:
            raise WorkflowError(f"Не нашла проект с id `{project_id}`.")
        if not project.available:
            raise WorkflowError(f"Проект `{project_id}` сейчас недоступен.")
        if capability and not project.supports(capability):
            raise WorkflowError(
                f"Проект `{project.title}` не поддерживает сценарий `{capability}`."
            )
        return project

    def _resolve_orchestrator_for_record(
        self,
        record_id: str,
        project_id: str | None,
    ) -> WorkflowOrchestrator | None:
        hinted_project = self._project_id_from_record(record_id) or project_id
        if hinted_project:
            project = self.get_project(hinted_project)
            if project and project.available:
                return self._get_orchestrator(project)
        active = self.get_active_project()
        if active:
            return self._get_orchestrator(active)
        return None

    def _resolve_orchestrator_for_payload(self, payload: dict[str, object]) -> WorkflowOrchestrator | None:
        project_id = str(payload.get("project_id") or "").strip()
        if project_id:
            project = self.get_project(project_id)
            if project and project.available:
                return self._get_orchestrator(project)

        project_root_raw = str(payload.get("project_root") or "").strip()
        if project_root_raw:
            project_root = Path(project_root_raw).expanduser().resolve()
            for project in self.list_projects():
                if project.available and project.root_dir == project_root:
                    return self._get_orchestrator(project)
            shadow_project = ProjectRecord(
                id=project_id or "detached-project",
                title=str(payload.get("project_title") or project_root.name or "Проект"),
                root_dir=project_root,
                capabilities=("thesis", "article"),
                works=(),
                default_work=None,
            )
            return self._get_orchestrator(shadow_project)
        return None

    def _get_orchestrator(self, project: ProjectRecord) -> WorkflowOrchestrator:
        cached = self._orchestrators.get(project.id)
        if cached and cached.root_dir == project.root_dir:
            return cached
        orchestrator = WorkflowOrchestrator(
            project.root_dir,
            codex_bin=self.codex_bin,
            codex_model=self.codex_model,
            python_executable=self.python_executable,
            store=self.store,
            project_id=project.id,
            project_title=project.title,
        )
        self._orchestrators[project.id] = orchestrator
        return orchestrator

    def _get_workspace(self, project: ProjectRecord) -> WorkspaceConfig:
        cached = self._workspace_cache.get(project.id)
        if cached and cached.root_dir == project.root_dir:
            return cached
        try:
            workspace = load_workspace_config(project.root_dir)
        except WorkspaceConfigError as exc:
            raise WorkflowError(str(exc)) from exc
        self._workspace_cache[project.id] = workspace
        return workspace

    def _get_work(self, project: ProjectRecord, work_id: str) -> WorkConfig:
        cache_key = (project.id, work_id)
        cached = self._work_cache.get(cache_key)
        if cached and cached.work_dir.exists():
            return cached
        workspace = self._get_workspace(project)
        try:
            work = load_work_config(workspace, work_id)
        except WorkspaceConfigError as exc:
            raise WorkflowError(str(exc)) from exc
        self._work_cache[cache_key] = work
        return work

    def _project_id_from_record(self, record_id: str) -> str | None:
        if ":" not in record_id:
            return None
        project_id, _ = record_id.split(":", 1)
        return project_id or None

    def _runtime_projects(self, project_id: str | None) -> list[ProjectRecord]:
        if project_id:
            return [self.require_project(project_id)]
        return [project for project in self.list_projects() if project.available]

    def _runtime_record_matches(
        self,
        record: RuntimeRecord,
        allowed_ids: set[str],
        allowed_roots: set[str],
    ) -> bool:
        project_id = str(record.project_id or "").strip()
        if project_id:
            return project_id in allowed_ids
        project_root = str(record.project_root or "").strip()
        if project_root:
            return project_root in allowed_roots
        return False
