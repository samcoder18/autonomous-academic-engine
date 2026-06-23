from __future__ import annotations

import json
import re
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
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
REQUIRED_SCALAR_FIELDS = ("job_id", "work_id", "job_type", "status", "created_at", "updated_at")
REQUIRED_DICT_FIELDS = ("payload", "limits")
REQUIRED_WORKFLOW_PAYLOAD_KEYS = ("lane", "action", "target_or_topic")
REQUIRED_LIMIT_KEYS = ("global_concurrency", "per_work_concurrency")
_UNSET = object()


class JobQueueError(RuntimeError):
    pass


class JobNotFoundError(JobQueueError):
    pass


class InvalidJobStateError(JobQueueError):
    pass


class CorruptJobError(JobQueueError):
    pass


class InvalidJobIdError(JobQueueError):
    pass


class DuplicateJobIdError(JobQueueError):
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
        job_id = self._validate_job_id(self._id_factory())
        if self._job_path(job_id).exists():
            raise DuplicateJobIdError(f"Job `{job_id}` already exists.")
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
        self._validate_job_record(job, context=f"constructed job `{job_id}`")
        self._create_job(job)
        return job

    def list_jobs(self, *, work_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in PUBLIC_JOB_STATUSES:
            raise InvalidJobStateError(f"Unknown job status filter `{status}`.")
        jobs = [self._read_job(path) for path in sorted(self.jobs_dir.glob("*.json"))] if self.jobs_dir.exists() else []
        result = [job for job in jobs if job is not None]
        if work_id is not None:
            result = [job for job in result if job.get("work_id") == work_id]
        if status is not None:
            result = [job for job in result if job.get("status") == status]
        return sorted(result, key=lambda job: (str(job.get("created_at") or ""), str(job.get("job_id") or "")))

    def get_job(self, job_id: str) -> dict[str, Any]:
        path = self._job_path(self._validate_job_id(job_id))
        job = self._read_job(path)
        if job is None:
            raise JobNotFoundError(f"Job `{job_id}` not found.")
        return job

    def cancel_job(self, job_id: str, *, reason: str = "operator-cancelled") -> dict[str, Any]:
        job = self.get_job(job_id)
        status = str(job.get("status") or "")
        if status in TERMINAL_JOB_STATUSES:
            raise InvalidJobStateError(f"Cannot cancel {status} job `{job_id}`.")
        details = {}
        if status == "running" and self._stop_job_func is not None:
            details["stop_result"] = self._stop_job_func(self.root_dir, str(job["work_id"]), reason)
        return self._transition(
            job,
            status="blocked",
            event="job-cancelled",
            blocked_reason=reason,
            details=details,
        )

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
        return self.jobs_dir / f"{self._validate_job_id(job_id)}.json"

    def _read_job(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CorruptJobError(f"Corrupt job record `{path}`: malformed JSON.") from exc
        return self._validate_loaded_job(payload, path)

    def _create_job(self, job: dict[str, Any]) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        path = self._job_path(str(job["job_id"]))
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(job, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except FileExistsError as exc:
            raise DuplicateJobIdError(f"Job `{job['job_id']}` already exists.") from exc

    def _write_job(self, job: dict[str, Any]) -> None:
        self._validate_job_record(job, context=f"updated job `{job.get('job_id')}`")
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        path = self._job_path(str(job["job_id"]))
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(self.jobs_dir)) as handle:
            json.dump(job, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temp_name = handle.name
        Path(temp_name).replace(path)

    def _validate_job_id(self, job_id: str) -> str:
        if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
            raise InvalidJobIdError(f"Invalid job id `{job_id}`.")
        return job_id

    def _validate_loaded_job(self, payload: Any, path: Path) -> dict[str, Any]:
        job = self._validate_job_record(payload, context=f"job record `{path}`")
        if job["job_id"] != path.stem:
            raise CorruptJobError(f"Corrupt job record `{path}`: job id does not match filename.")
        return job

    def _validate_job_record(self, payload: Any, *, context: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise CorruptJobError(f"Invalid {context}: expected JSON object.")
        if payload.get("kind") != JOB_KIND:
            raise CorruptJobError(f"Invalid {context}: expected kind `{JOB_KIND}`.")
        if payload.get("version") != JOB_VERSION:
            raise CorruptJobError(f"Invalid {context}: expected version `{JOB_VERSION}`.")
        job_id = payload.get("job_id")
        try:
            self._validate_job_id(job_id)
        except InvalidJobIdError as exc:
            raise CorruptJobError(f"Invalid {context}: invalid job id `{job_id}`.") from exc
        for field in REQUIRED_SCALAR_FIELDS:
            if not isinstance(payload.get(field), str):
                raise CorruptJobError(f"Invalid {context}: field `{field}` must be a string.")
        attempt = self._require_non_bool_int(payload.get("attempt"), context=context, field="attempt")
        if attempt < 0:
            raise CorruptJobError(f"Invalid {context}: field `attempt` must be non-negative.")
        max_attempts = self._require_non_bool_int(payload.get("max_attempts"), context=context, field="max_attempts")
        if max_attempts <= 0:
            raise CorruptJobError(f"Invalid {context}: field `max_attempts` must be a positive integer.")
        for field in REQUIRED_DICT_FIELDS:
            if not isinstance(payload.get(field), dict):
                raise CorruptJobError(f"Invalid {context}: field `{field}` must be an object.")
        if not isinstance(payload.get("history"), list):
            raise CorruptJobError(f"Invalid {context}: field `history` must be a list.")
        status = payload.get("status")
        if status not in PUBLIC_JOB_STATUSES:
            raise InvalidJobStateError(f"Invalid {context}: unknown status `{status}`.")
        job_type = payload.get("job_type")
        if job_type != "workflow":
            raise CorruptJobError(f"Invalid {context}: field `job_type` must be `workflow`.")
        workflow_payload = payload["payload"]
        for key in REQUIRED_WORKFLOW_PAYLOAD_KEYS:
            if not isinstance(workflow_payload.get(key), str):
                raise CorruptJobError(f"Invalid {context}: payload key `{key}` must be a string.")
        limits = payload["limits"]
        for key in REQUIRED_LIMIT_KEYS:
            value = self._require_non_bool_int(limits.get(key), context=context, field=f"limits.{key}")
            if value <= 0:
                raise CorruptJobError(f"Invalid {context}: limits key `{key}` must be a positive integer.")
        for index, item in enumerate(payload["history"]):
            self._validate_history_item(item, context=context, index=index)
        return payload

    def _require_non_bool_int(self, value: Any, *, context: str, field: str) -> int:
        if type(value) is not int:
            raise CorruptJobError(f"Invalid {context}: field `{field}` must be an integer.")
        return value

    def _validate_history_item(self, item: Any, *, context: str, index: int) -> None:
        if not isinstance(item, dict):
            raise CorruptJobError(f"Invalid {context}: history item {index} must be an object.")
        if not isinstance(item.get("timestamp"), str):
            raise CorruptJobError(f"Invalid {context}: history item {index} field `timestamp` must be a string.")
        if not isinstance(item.get("event"), str):
            raise CorruptJobError(f"Invalid {context}: history item {index} field `event` must be a string.")
        status = item.get("status")
        if status not in PUBLIC_JOB_STATUSES:
            raise InvalidJobStateError(f"Invalid {context}: history item {index} has unknown status `{status}`.")
        if not isinstance(item.get("details"), dict):
            raise CorruptJobError(f"Invalid {context}: history item {index} field `details` must be an object.")
