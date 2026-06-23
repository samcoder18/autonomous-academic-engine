from __future__ import annotations

import json
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import utc_now

JOB_KIND = "engine-job"
JOB_VERSION = "job/v1"
PUBLIC_JOB_STATUSES = {"queued", "running", "blocked", "failed", "completed"}
TERMINAL_JOB_STATUSES = {"failed", "completed"}
DEFAULT_GLOBAL_CONCURRENCY = 2
DEFAULT_PER_WORK_CONCURRENCY = 1
DEFAULT_MAX_ATTEMPTS = 3
_UNSET = object()


class JobQueueError(RuntimeError):
    pass


class JobNotFoundError(JobQueueError):
    pass


class InvalidJobStateError(JobQueueError):
    pass


@dataclass(frozen=True)
class WorkflowJobSpec:
    work_id: str
    lane: str
    action: str
    target_or_topic: str
    notes: str | None = None
    search_override: bool | None = None
    model_override: str | None = None
    profile_override: str | None = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    global_concurrency: int = DEFAULT_GLOBAL_CONCURRENCY
    per_work_concurrency: int = DEFAULT_PER_WORK_CONCURRENCY


class JobQueue:
    def __init__(
        self,
        root_dir: str | Path,
        *,
        now: Callable[[], str] = utc_now,
        id_factory: Callable[[], str] | None = None,
        orchestrator_factory: Callable[[Path], Any] | None = None,
        stop_job_func: Callable[[Path, str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.jobs_dir = self.root_dir / "output" / "runtime" / "jobs"
        self._now = now
        self._id_factory = id_factory or (lambda: f"job-{uuid.uuid4().hex}")
        self._orchestrator_factory = orchestrator_factory
        self._stop_job_func = stop_job_func

    def submit_workflow(self, spec: WorkflowJobSpec) -> dict[str, Any]:
        job_id = self._id_factory()
        now = self._now()
        job = {
            "kind": JOB_KIND,
            "version": JOB_VERSION,
            "job_id": job_id,
            "work_id": spec.work_id,
            "job_type": "workflow",
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "attempt": 0,
            "max_attempts": spec.max_attempts,
            "workflow_id": None,
            "active_run_id": None,
            "payload": {
                "lane": spec.lane,
                "action": spec.action,
                "target_or_topic": spec.target_or_topic,
                "notes": spec.notes,
                "search_override": spec.search_override,
                "model_override": spec.model_override,
                "profile_override": spec.profile_override,
            },
            "limits": {
                "global_concurrency": spec.global_concurrency,
                "per_work_concurrency": spec.per_work_concurrency,
            },
            "blocked_reason": None,
            "failure": None,
            "history": [
                {
                    "timestamp": now,
                    "event": "job-submitted",
                    "status": "queued",
                    "details": {},
                }
            ],
        }
        self._write_job(job)
        return job

    def list_jobs(self, *, work_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        jobs = [self._read_job(path) for path in sorted(self.jobs_dir.glob("*.json"))] if self.jobs_dir.exists() else []
        result = [job for job in jobs if job is not None]
        if work_id is not None:
            result = [job for job in result if job.get("work_id") == work_id]
        if status is not None:
            result = [job for job in result if job.get("status") == status]
        return sorted(result, key=lambda job: (str(job.get("created_at") or ""), str(job.get("job_id") or "")))

    def get_job(self, job_id: str) -> dict[str, Any]:
        path = self._job_path(job_id)
        job = self._read_job(path)
        if job is None:
            raise JobNotFoundError(f"Job `{job_id}` not found.")
        return job

    def cancel_job(self, job_id: str, *, reason: str = "operator-cancelled") -> dict[str, Any]:
        job = self.get_job(job_id)
        status = str(job.get("status") or "")
        if status in {"completed", "failed"}:
            raise InvalidJobStateError(f"Cannot cancel {status} job `{job_id}`.")
        return self._transition(job, status="blocked", event="job-cancelled", blocked_reason=reason)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job.get("status") != "failed":
            raise InvalidJobStateError(f"Only failed jobs can be retried: `{job_id}`.")
        attempt = int(job.get("attempt") or 0) + 1
        if attempt > int(job.get("max_attempts") or DEFAULT_MAX_ATTEMPTS):
            raise InvalidJobStateError(f"Job `{job_id}` has no retry attempts left.")
        job["attempt"] = attempt
        job["workflow_id"] = None
        job["active_run_id"] = None
        return self._transition(job, status="queued", event="job-retried", blocked_reason=None, failure=None)

    def resume_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job.get("status") != "blocked":
            raise InvalidJobStateError(f"Only blocked jobs can be resumed: `{job_id}`.")
        job["workflow_id"] = None
        job["active_run_id"] = None
        return self._transition(job, status="queued", event="job-resumed", blocked_reason=None, failure=None)

    def _transition_for_test(
        self,
        job_id: str,
        *,
        status: str,
        failure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._transition(self.get_job(job_id), status=status, event=f"job-{status}", failure=failure)

    def _transition(
        self,
        job: dict[str, Any],
        *,
        status: str,
        event: str,
        blocked_reason: str | None | object = _UNSET,
        failure: dict[str, Any] | None | object = _UNSET,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in PUBLIC_JOB_STATUSES:
            raise InvalidJobStateError(f"Unknown job status `{status}`.")
        now = self._now()
        job["status"] = status
        job["updated_at"] = now
        if blocked_reason is not _UNSET:
            job["blocked_reason"] = blocked_reason
        if failure is not _UNSET:
            job["failure"] = failure
        job.setdefault("history", []).append(
            {
                "timestamp": now,
                "event": event,
                "status": status,
                "details": details or {},
            }
        )
        self._write_job(job)
        return job

    def _job_path(self, job_id: str) -> Path:
        safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in job_id)
        return self.jobs_dir / f"{safe or 'job'}.json"

    def _read_job(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) and payload.get("kind") == JOB_KIND else None

    def _write_job(self, job: dict[str, Any]) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        path = self._job_path(str(job["job_id"]))
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(self.jobs_dir)) as handle:
            json.dump(job, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temp_name = handle.name
        Path(temp_name).replace(path)
