from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .autonomous_runner import stop_autonomous_run
from .orchestrator import WorkflowOrchestrator
from .work_bootstrap import WorkBootstrapRequest, bootstrap_work
from .workspace import load_workspace_config, resolve_work_config


@dataclass(frozen=True)
class CreateWorkRequest:
    slug: str
    title: str
    artifact_type: str
    topic: str | None = None
    language: str = "ru"
    lanes: tuple[str, ...] | None = None
    thesis_profile: str | None = None
    article_profile: str | None = None
    set_default: bool = False


@dataclass(frozen=True)
class StartWorkflowRequest:
    lane: str
    action: str
    target_or_topic: str
    notes: str | None = None
    search_override: bool | None = None
    model_override: str | None = None
    profile_override: str | None = None
    work_id: str | None = None


@dataclass(frozen=True)
class ExportRequest:
    subject: str
    work_id: str | None = None


@dataclass(frozen=True)
class StopJobRequest:
    work_id: str | None = None
    reason: str = "operator-stop"


class EngineService:
    """Stable internal service facade over the academic engine core."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        orchestrator_factory: Callable[[Path], Any] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self._orchestrator_factory = orchestrator_factory or WorkflowOrchestrator

    def create_work(self, request: CreateWorkRequest) -> dict[str, Any]:
        topic = request.title if request.topic is None else request.topic
        result = bootstrap_work(
            self.root_dir,
            WorkBootstrapRequest(
                slug=request.slug,
                title=request.title,
                topic=topic,
                artifact_type=request.artifact_type,
                language=request.language,
                lanes=request.lanes,
                thesis_profile=request.thesis_profile,
                article_profile=request.article_profile,
                set_default=request.set_default,
            ),
        )
        return {
            "kind": "work-init",
            "version": "v1",
            "slug": result.slug,
            "work_dir": str(result.work_dir),
            "work_toml": str(result.work_toml),
            "work_canon": str(result.work_canon),
            "workspace_toml": str(result.workspace_toml),
            "set_default": result.set_default,
            "default_work": result.default_work_after,
            "created_dirs": [str(directory) for directory in result.created_dirs],
        }

    def get_work_status(self, work_id: str | None = None) -> dict[str, Any]:
        return self._orchestrator().get_work_state(work_id=work_id)

    def start_workflow(self, request: StartWorkflowRequest) -> dict[str, Any]:
        return self._orchestrator().start_run(
            request.lane,
            request.action,
            request.target_or_topic,
            notes=request.notes,
            search_override=request.search_override,
            model_override=request.model_override,
            profile_override=request.profile_override,
            work_id=request.work_id,
        )

    def export_docx(self, request: ExportRequest) -> dict[str, Any]:
        return self._orchestrator().export_docx(request.subject, work_id=request.work_id)

    def stop_job(self, request: StopJobRequest) -> dict[str, Any]:
        workspace = load_workspace_config(self.root_dir)
        work = resolve_work_config(workspace, work_id=request.work_id)
        return stop_autonomous_run(self.root_dir, work.slug, reason=request.reason)

    def _orchestrator(self) -> Any:
        return self._orchestrator_factory(self.root_dir)
