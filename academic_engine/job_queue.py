from __future__ import annotations

import json
import re
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .orchestrator import WorkflowOrchestrator
from .orchestrator_support import RunBusyError, WorkflowError
from .utils import utc_now
from .workspace import WorkspaceConfigError

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
OPTIONAL_WORKFLOW_STRING_PAYLOAD_KEYS = ("notes", "model_override", "profile_override")
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
        self._orchestrator_factory = orchestrator_factory or WorkflowOrchestrator
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

    def dispatch_jobs(self, *, limit: int | None = None) -> dict[str, Any]:
        orchestrator = self._orchestrator()
        self._sync_active_runs(orchestrator)
        active_runs = self._active_runs(orchestrator)
        running_jobs = self.list_jobs(status="running")
        running_total, running_by_work = self._running_counts(running_jobs, active_runs)
        dispatch_limit = None if limit is None else max(0, limit)
        result: dict[str, Any] = {
            "kind": "job-dispatch",
            "version": "v1",
            "dispatched": [],
            "skipped": [],
            "blocked": [],
            "reconciled": [],
        }

        for job in self._dispatch_candidates():
            if dispatch_limit is not None and len(result["dispatched"]) >= dispatch_limit:
                break
            work_id = str(job["work_id"])
            limits = job["limits"]
            global_limit = int(limits["global_concurrency"])
            per_work_limit = int(limits["per_work_concurrency"])
            if running_total >= global_limit:
                result["skipped"].append(self._dispatch_result(job, reason="global-concurrency-limit"))
                continue
            if running_by_work.get(work_id, 0) >= per_work_limit:
                result["skipped"].append(self._dispatch_result(job, reason="per-work-concurrency-limit"))
                continue
            try:
                started = orchestrator.start_run(
                    str(job["payload"]["lane"]),
                    str(job["payload"]["action"]),
                    str(job["payload"]["target_or_topic"]),
                    notes=job["payload"].get("notes"),
                    search_override=job["payload"].get("search_override"),
                    model_override=job["payload"].get("model_override"),
                    profile_override=job["payload"].get("profile_override"),
                    work_id=work_id,
                )
            except RunBusyError as exc:
                result["skipped"].append(self._dispatch_result(job, reason="run-busy", message=str(exc)))
                continue
            except (WorkflowError, WorkspaceConfigError, ValueError) as exc:
                blocked = self._block_job(
                    job,
                    code="workflow-start-config-error",
                    category="config",
                    exc=exc,
                )
                result["blocked"].append(self._dispatch_result(blocked))
                continue
            except Exception as exc:
                blocked = self._block_job(
                    job,
                    code="workflow-start-runtime-error",
                    category="runtime",
                    exc=exc,
                )
                result["blocked"].append(self._dispatch_result(blocked))
                continue

            if int(job.get("attempt") or 0) == 0:
                job["attempt"] = 1
            job["workflow_id"] = started.get("workflow_id")
            job["active_run_id"] = started.get("run_id")
            running = self._transition(
                job,
                status="running",
                event="job-dispatched",
                blocked_reason=None,
                failure=None,
                details={"workflow_id": job["workflow_id"], "active_run_id": job["active_run_id"]},
            )
            running_total += 1
            running_by_work[work_id] = running_by_work.get(work_id, 0) + 1
            result["dispatched"].append(self._dispatch_result(running))

        return result

    def reconcile_jobs(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": "job-reconcile",
            "version": "v1",
            "reconciled": [],
            "blocked": [],
        }
        for job in self.list_jobs(status="running"):
            workflow_id = job.get("workflow_id")
            if not isinstance(workflow_id, str) or not workflow_id:
                blocked = self._block_job(
                    job,
                    code="missing-runtime-result",
                    category="runtime",
                    message="Running job does not have a workflow id.",
                )
                result["blocked"].append(self._dispatch_result(blocked))
                continue
            workflow = self._workflow_payload(workflow_id)
            if workflow is None:
                blocked = self._block_job(
                    job,
                    code="missing-runtime-result",
                    category="runtime",
                    message=f"Missing runtime result for workflow `{workflow_id}`.",
                )
                result["blocked"].append(self._dispatch_result(blocked))
                continue
            execution_status = workflow.get("execution_status")
            if execution_status == "succeeded":
                job["active_run_id"] = None
                completed = self._transition(
                    job,
                    status="completed",
                    event="job-completed",
                    failure=None,
                    details={"workflow_id": workflow_id},
                )
                result["reconciled"].append(self._dispatch_result(completed))
            elif execution_status == "failed":
                job["active_run_id"] = None
                failed = self._transition(
                    job,
                    status="failed",
                    event="job-failed",
                    failure={
                        "code": "workflow-failed",
                        "category": "runtime",
                        "message": f"Workflow `{workflow_id}` failed.",
                    },
                    details={"workflow_id": workflow_id},
                )
                result["reconciled"].append(self._dispatch_result(failed))
        return result

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

    def _orchestrator(self) -> Any:
        return self._orchestrator_factory(self.root_dir)

    def _sync_active_runs(self, orchestrator: Any) -> None:
        sync = getattr(orchestrator, "sync_active_run", None)
        if callable(sync):
            sync()

    def _active_runs(self, orchestrator: Any) -> list[dict[str, Any]]:
        store = getattr(orchestrator, "store", None)
        list_active_runs = getattr(store, "list_active_runs", None)
        if not callable(list_active_runs):
            return []
        return [item for item in list_active_runs() if isinstance(item, dict)]

    def _dispatch_candidates(self) -> list[dict[str, Any]]:
        if not self.jobs_dir.exists():
            return []
        candidates: list[tuple[str, int, str, dict[str, Any]]] = []
        for path in sorted(self.jobs_dir.glob("*.json")):
            job = self._read_job(path)
            if job is None or job.get("status") != "queued":
                continue
            candidates.append(
                (
                    str(job.get("created_at") or ""),
                    path.stat().st_mtime_ns,
                    str(job.get("job_id") or ""),
                    job,
                )
            )
        return [job for _, _, _, job in sorted(candidates, key=lambda item: item[:3])]

    def _running_counts(
        self,
        running_jobs: list[dict[str, Any]],
        active_runs: list[dict[str, Any]],
    ) -> tuple[int, dict[str, int]]:
        total = 0
        by_work: dict[str, int] = {}
        for item in [*running_jobs, *active_runs]:
            total += 1
            work_id = item.get("work_id")
            if isinstance(work_id, str) and work_id:
                by_work[work_id] = by_work.get(work_id, 0) + 1
        return total, by_work

    def _block_job(
        self,
        job: dict[str, Any],
        *,
        code: str,
        category: str,
        exc: BaseException | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        failure = {
            "code": code,
            "category": category,
            "message": message or (str(exc) if exc is not None else code),
        }
        if exc is not None:
            failure["error_type"] = type(exc).__name__
        return self._transition(
            job,
            status="blocked",
            event="job-blocked",
            blocked_reason=code,
            failure=failure,
        )

    def _dispatch_result(self, job: dict[str, Any], **extra: Any) -> dict[str, Any]:
        item = {
            "job_id": job["job_id"],
            "status": job["status"],
            "work_id": job["work_id"],
            "workflow_id": job.get("workflow_id"),
            "active_run_id": job.get("active_run_id"),
        }
        item.update(extra)
        return item

    def _workflow_payload(self, workflow_id: str) -> dict[str, Any] | None:
        path = self.root_dir / "output" / "runs" / workflow_id / "workflow.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None

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
        for key in OPTIONAL_WORKFLOW_STRING_PAYLOAD_KEYS:
            if workflow_payload.get(key) is not None and not isinstance(workflow_payload.get(key), str):
                raise CorruptJobError(f"Invalid {context}: payload key `{key}` must be a string or null.")
        search_override = workflow_payload.get("search_override")
        if search_override is not None and type(search_override) is not bool:
            raise CorruptJobError(f"Invalid {context}: payload key `search_override` must be a boolean or null.")
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
