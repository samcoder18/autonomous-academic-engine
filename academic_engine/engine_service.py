from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .autonomous_runner import stop_autonomous_run
from .export_explain import explain_export as _explain_export
from .job_queue import JobQueue, WorkflowJobSpec
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


@dataclass(frozen=True)
class SubmitWorkflowJobRequest:
    work_id: str
    lane: str
    action: str
    target_or_topic: str
    notes: str | None = None
    search_override: bool | None = None
    model_override: str | None = None
    profile_override: str | None = None


@dataclass(frozen=True)
class CancelJobRequest:
    job_id: str
    reason: str = "operator-cancelled"


@dataclass(frozen=True)
class RetryJobRequest:
    job_id: str


@dataclass(frozen=True)
class ResumeJobRequest:
    job_id: str


@dataclass(frozen=True)
class DispatchJobsRequest:
    limit: int | None = None


@dataclass(frozen=True)
class InspectJobRequest:
    job_id: str


class EngineService:
    """Stable internal service facade over the academic engine core."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        orchestrator_factory: Callable[[Path], Any] | None = None,
        job_queue_factory: Callable[[Path, Callable[[Path], Any]], Any] | None = None,
        export_explainer: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self._orchestrator_factory = orchestrator_factory or WorkflowOrchestrator
        self._job_queue_factory = job_queue_factory
        self._export_explainer = export_explainer or _explain_export

    def create_work(self, request: CreateWorkRequest) -> dict[str, Any]:
        topic = request.title if request.topic is None else request.topic.strip()
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

    def submit_workflow_job(self, request: SubmitWorkflowJobRequest) -> dict[str, Any]:
        return self._job_queue().submit_workflow(
            WorkflowJobSpec(
                work_id=request.work_id,
                lane=request.lane,
                action=request.action,
                target_or_topic=request.target_or_topic,
                notes=request.notes,
                search_override=request.search_override,
                model_override=request.model_override,
                profile_override=request.profile_override,
            )
        )

    def list_jobs(self, work_id: str | None = None, status: str | None = None) -> dict[str, Any]:
        return {
            "kind": "job-list",
            "version": "v1",
            "jobs": self._job_queue().list_jobs(work_id=work_id, status=status),
        }

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._job_queue().get_job(job_id)

    def cancel_job(self, request: CancelJobRequest) -> dict[str, Any]:
        return self._job_queue().cancel_job(request.job_id, reason=request.reason)

    def retry_job(self, request: RetryJobRequest) -> dict[str, Any]:
        return self._job_queue().retry_job(request.job_id)

    def resume_job(self, request: ResumeJobRequest) -> dict[str, Any]:
        return self._job_queue().resume_job(request.job_id)

    def dispatch_jobs(self, request: DispatchJobsRequest) -> dict[str, Any]:
        return self._job_queue().dispatch_jobs(limit=request.limit)

    def inspect_job(self, request: InspectJobRequest) -> dict[str, Any]:
        return self._job_queue().inspect_job(request.job_id)

    def explain_export(self, subject: str, *, work_id: str | None = None) -> dict[str, Any]:
        return self._export_explainer(self.root_dir, subject, work_id=work_id)

    def _orchestrator(self) -> Any:
        return self._orchestrator_factory(self.root_dir)

    def _job_queue(self) -> Any:
        if self._job_queue_factory is not None:
            return self._job_queue_factory(self.root_dir, self._orchestrator_factory)
        return JobQueue(
            self.root_dir,
            orchestrator_factory=self._orchestrator_factory,
            stop_job_func=stop_autonomous_run,
        )
