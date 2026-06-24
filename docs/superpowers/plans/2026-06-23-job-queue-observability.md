# Job Queue and Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable file-backed job queue, dispatcher, run inspection payload, and export-blocker explanation layer above the existing workflow engine.

**Architecture:** Add focused core modules instead of rewriting launch/runtime behavior: `job_queue.py` owns durable job records and dispatch/reconcile, `job_inspector.py` builds the black-box job/run snapshot, and `export_explain.py` mirrors existing fail-closed export gates as structured reasons. `EngineService` exposes the public Python facade, while `work_cli.py` adds explicit queue commands without changing existing immediate launch commands.

**Tech Stack:** Python 3.11 standard library, `unittest`, existing `WorkflowOrchestrator`, `RuntimeStore`, `workspace.py`, `utils.utc_now`, `utils.parse_datetime`, and JSON files under `output/runtime/jobs/`.

---

## File Structure

- Create `academic_engine/job_queue.py`
  - Durable job JSON records under `output/runtime/jobs/`.
  - Job lifecycle operations: submit/list/get/cancel/retry/resume.
  - Dispatcher and reconciliation with the existing `WorkflowOrchestrator.start_run()`.
  - Public queue exceptions: `JobQueueError`, `JobNotFoundError`, `InvalidJobStateError`.
- Create `academic_engine/job_inspector.py`
  - Reads a job payload plus linked `output/runs/<workflow_id>/` artifacts.
  - Builds timeline, durations, failure summary, blockers, changed files, export blockers, attachments, and warnings.
- Create `academic_engine/export_explain.py`
  - Explains current DOCX export blockers without weakening `orchestrator_exports.py`.
  - Uses existing workspace resolution and scans current `workflow-run/v1` artifacts.
- Modify `academic_engine/engine_service.py`
  - Add request dataclasses and service methods over `JobQueue`, `inspect_job`, and `explain_export`.
- Modify `academic_engine/work_cli.py`
  - Add `jobs` subcommands, top-level `job-inspect`, and top-level `export-explain`.
  - Keep existing `launch-thesis`, `launch-academic`, and export commands unchanged.
- Create `tests/test_job_queue.py`
  - Queue persistence, filtering, lifecycle transitions, dispatch, limits, and reconciliation tests.
- Create `tests/test_job_inspector.py`
  - Timeline/duration/failure/blocker/changed-file/warning tests.
- Create `tests/test_export_explain.py`
  - Structured export explanation tests for thesis and article gates.
- Modify `tests/test_engine_service.py`
  - Service facade tests for queue and explanation methods.
- Create `tests/test_work_cli_jobs.py`
  - CLI parser/handler tests using a fake `EngineService`.

---

### Task 1: Durable Job Store and Lifecycle

**Files:**
- Create: `academic_engine/job_queue.py`
- Create: `tests/test_job_queue.py`

- [ ] **Step 1: Write failing store/lifecycle tests**

Create `tests/test_job_queue.py` with these initial tests:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from academic_engine.job_queue import (
    InvalidJobStateError,
    JobNotFoundError,
    JobQueue,
    WorkflowJobSpec,
)


class JobQueueStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_submit_workflow_persists_queued_job(self) -> None:
        queue = JobQueue(self.root, now=lambda: "2026-06-23T10:00:00+00:00", id_factory=lambda: "job-demo")

        job = queue.submit_workflow(
            WorkflowJobSpec(
                work_id="demo-work",
                lane="thesis",
                action="write-section",
                target_or_topic="thesis/manuscript/sections/01.md",
                notes="draft carefully",
                search_override=True,
                model_override="test-model",
                profile_override=None,
            )
        )

        self.assertEqual(job["kind"], "engine-job")
        self.assertEqual(job["version"], "job/v1")
        self.assertEqual(job["job_id"], "job-demo")
        self.assertEqual(job["work_id"], "demo-work")
        self.assertEqual(job["job_type"], "workflow")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["attempt"], 0)
        self.assertEqual(job["max_attempts"], 3)
        self.assertIsNone(job["workflow_id"])
        self.assertEqual(job["payload"]["lane"], "thesis")
        self.assertEqual(job["payload"]["action"], "write-section")
        self.assertEqual(job["payload"]["target_or_topic"], "thesis/manuscript/sections/01.md")
        self.assertEqual(job["payload"]["notes"], "draft carefully")
        self.assertTrue(job["payload"]["search_override"])
        self.assertEqual(job["payload"]["model_override"], "test-model")
        self.assertEqual(job["limits"], {"global_concurrency": 2, "per_work_concurrency": 1})
        self.assertEqual(job["history"][0]["event"], "job-submitted")

        stored = json.loads((self.root / "output" / "runtime" / "jobs" / "job-demo.json").read_text())
        self.assertEqual(stored, job)

    def test_list_jobs_filters_by_work_and_status(self) -> None:
        queue = JobQueue(self.root)
        first = queue.submit_workflow(WorkflowJobSpec("alpha", "thesis", "verify", "section-1"))
        second = queue.submit_workflow(WorkflowJobSpec("beta", "article", "review", "draft.md"))
        queue.cancel_job(second["job_id"], reason="operator-cancelled")

        self.assertEqual([item["job_id"] for item in queue.list_jobs(work_id="alpha")], [first["job_id"]])
        self.assertEqual([item["job_id"] for item in queue.list_jobs(status="blocked")], [second["job_id"]])

    def test_get_job_rejects_unknown_id(self) -> None:
        with self.assertRaises(JobNotFoundError):
            JobQueue(self.root).get_job("missing-job")

    def test_cancel_retry_and_resume_use_public_states(self) -> None:
        queue = JobQueue(self.root, now=lambda: "2026-06-23T10:00:00+00:00")
        queued = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))

        blocked = queue.cancel_job(queued["job_id"], reason="operator-cancelled")
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["blocked_reason"], "operator-cancelled")
        self.assertEqual(blocked["history"][-1]["event"], "job-cancelled")

        resumed = queue.resume_job(queued["job_id"])
        self.assertEqual(resumed["status"], "queued")
        self.assertIsNone(resumed["blocked_reason"])
        self.assertEqual(resumed["history"][-1]["event"], "job-resumed")

        failed = queue._transition_for_test(queued["job_id"], status="failed", failure={"code": "boom"})
        self.assertEqual(failed["status"], "failed")
        retried = queue.retry_job(queued["job_id"])
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["attempt"], 1)
        self.assertIsNone(retried["failure"])
        self.assertEqual(retried["history"][-1]["event"], "job-retried")

    def test_invalid_retry_and_resume_states_fail_closed(self) -> None:
        queue = JobQueue(self.root)
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))

        with self.assertRaises(InvalidJobStateError):
            queue.retry_job(job["job_id"])

        with self.assertRaises(InvalidJobStateError):
            queue.resume_job(job["job_id"])
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_job_queue.JobQueueStoreTests -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'academic_engine.job_queue'` or `ImportError` for `JobQueue`.

- [ ] **Step 3: Implement minimal durable store**

Create `academic_engine/job_queue.py` with these public definitions and behavior:

```python
from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .utils import utc_now

JOB_KIND = "engine-job"
JOB_VERSION = "job/v1"
PUBLIC_JOB_STATUSES = {"queued", "running", "blocked", "failed", "completed"}
TERMINAL_JOB_STATUSES = {"blocked", "failed", "completed"}
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
        if attempt >= int(job.get("max_attempts") or DEFAULT_MAX_ATTEMPTS):
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
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_job_queue.JobQueueStoreTests -q
```

Expected: `Ran 5 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/job_queue.py tests/test_job_queue.py
git commit -m "feat: add durable job queue store"
```

---

### Task 2: Dispatcher and Reconciliation

**Files:**
- Modify: `academic_engine/job_queue.py`
- Modify: `tests/test_job_queue.py`

- [ ] **Step 1: Write failing dispatcher/reconcile tests**

Append these tests and fakes to `tests/test_job_queue.py`:

```python
class JobQueueDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)
        self.fake = FakeOrchestrator(self.root)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def queue(self) -> JobQueue:
        return JobQueue(self.root, orchestrator_factory=lambda root: self.fake)

    def test_dispatch_starts_oldest_queued_job_and_links_workflow(self) -> None:
        queue = self.queue()
        first = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "section-1"))
        queue.submit_workflow(WorkflowJobSpec("other-work", "article", "review", "draft.md"))

        result = queue.dispatch_jobs(limit=1)

        self.assertEqual([item["job_id"] for item in result["dispatched"]], [first["job_id"]])
        job = queue.get_job(first["job_id"])
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["workflow_id"], "wf-1")
        self.assertEqual(job["active_run_id"], "run-1")
        self.assertEqual(job["attempt"], 1)
        self.assertEqual(self.fake.start_calls[0]["work_id"], "demo-work")

    def test_dispatch_respects_global_and_per_work_limits(self) -> None:
        queue = self.queue()
        running = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "running"))
        queue._transition_for_test(running["job_id"], status="running")
        first = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "queued-same-work"))
        second = queue.submit_workflow(WorkflowJobSpec("other-work", "article", "review", "queued-other-work"))

        result = queue.dispatch_jobs(limit=5)

        self.assertEqual([item["job_id"] for item in result["dispatched"]], [second["job_id"]])
        self.assertEqual(queue.get_job(first["job_id"])["status"], "queued")

    def test_reconcile_completed_and_failed_workflows(self) -> None:
        queue = self.queue()
        done = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "done"))
        failed = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "failed"))
        done_payload = queue.get_job(done["job_id"])
        done_payload["workflow_id"] = "wf-done"
        queue._transition(done_payload, status="running", event="job-dispatched")
        failed_payload = queue.get_job(failed["job_id"])
        failed_payload["workflow_id"] = "wf-failed"
        queue._transition(failed_payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-done", execution_status="succeeded")
        _write_workflow(self.root, "wf-failed", execution_status="failed")

        result = queue.reconcile_jobs()

        self.assertEqual({item["job_id"]: item["status"] for item in result["reconciled"]}, {
            done["job_id"]: "completed",
            failed["job_id"]: "failed",
        })

    def test_reconcile_missing_runtime_blocks_running_job(self) -> None:
        queue = self.queue()
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))
        job["workflow_id"] = "wf-missing"
        queue._transition(job, status="running", event="job-dispatched")

        queue.reconcile_jobs()

        blocked = queue.get_job(job["job_id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["failure"]["code"], "missing-runtime-result")


class FakeStore:
    def __init__(self) -> None:
        self.active_runs: list[dict[str, object]] = []

    def list_active_runs(self) -> list[dict[str, object]]:
        return list(self.active_runs)


class FakeOrchestrator:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = FakeStore()
        self.start_calls: list[dict[str, object]] = []

    def sync_active_run(self, work_id: str | None = None) -> list[dict[str, object]]:
        return []

    def start_run(
        self,
        lane: str,
        action: str,
        target_or_topic: str,
        *,
        notes: str | None = None,
        search_override: bool | None = None,
        model_override: str | None = None,
        profile_override: str | None = None,
        work_id: str | None = None,
    ) -> dict[str, object]:
        workflow_id = f"wf-{len(self.start_calls) + 1}"
        run_id = f"run-{len(self.start_calls) + 1}"
        self.start_calls.append(
            {
                "lane": lane,
                "action": action,
                "target_or_topic": target_or_topic,
                "notes": notes,
                "search_override": search_override,
                "model_override": model_override,
                "profile_override": profile_override,
                "work_id": work_id,
            }
        )
        return {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "status": "queued",
            "work_id": work_id,
            "lane": lane,
            "action": action,
        }


def _write_workflow(root: Path, workflow_id: str, *, execution_status: str) -> None:
    workflow_dir = root / "output" / "runs" / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "workflow.json").write_text(
        json.dumps(
            {
                "version": "workflow-run/v1",
                "workflow_id": workflow_id,
                "run_id": workflow_id,
                "work_id": "demo-work",
                "lane": "thesis",
                "action": "verify",
                "status": "completed" if execution_status == "succeeded" else "failed",
                "execution_status": execution_status,
                "readiness_status": "submission-ready" if execution_status == "succeeded" else "strong-draft-with-blockers",
                "started_at": "2026-06-23T10:00:00+00:00",
                "finished_at": "2026-06-23T10:01:00+00:00",
                "workflow_dir": str(workflow_dir),
                "sandbox_dir": str(workflow_dir / "sandbox"),
                "role_runs": [],
                "gates": [],
                "gate_summary": {},
                "blockers": [],
                "promotion": {"status": "promoted"},
                "promotion_status": "promoted",
                "evaluator_verdict": None,
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_job_queue.JobQueueDispatchTests -q
```

Expected: FAIL with missing `dispatch_jobs` or `reconcile_jobs`.

- [ ] **Step 3: Implement dispatcher and reconciliation**

In `academic_engine/job_queue.py`:

- Import `WorkflowError`, `RunBusyError`, and `WorkflowOrchestrator`.
- Default `orchestrator_factory` to `WorkflowOrchestrator`.
- Add `dispatch_jobs(self, *, limit: int | None = None) -> dict[str, Any]`.
- Add `reconcile_jobs(self) -> dict[str, Any]`.
- Add helper `_workflow_payload(workflow_id: str) -> dict[str, Any] | None`.
- Count `running` jobs from queue plus active runs from `orchestrator.store.list_active_runs()`.
- Enforce `global_concurrency` and `per_work_concurrency` from each job's `limits`.
- On successful start, update `workflow_id`, `active_run_id`, `attempt += 1`, `status="running"`, and append `job-dispatched`.
- On `RunBusyError`, leave the job queued and return it in `skipped`.
- On `WorkflowError` or workspace/config exceptions, transition to `blocked` with `failure.category="config"` or `failure.category="runtime"`.

The returned dispatch payload must be:

```python
{
    "kind": "job-dispatch",
    "version": "v1",
    "dispatched": [...],
    "skipped": [...],
    "blocked": [...],
    "reconciled": [...],
}
```

The returned reconcile payload must be:

```python
{
    "kind": "job-reconcile",
    "version": "v1",
    "reconciled": [...],
    "blocked": [...],
}
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_job_queue -q
```

Expected: all `tests.test_job_queue` tests pass.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/job_queue.py tests/test_job_queue.py
git commit -m "feat: dispatch durable workflow jobs"
```

---

### Task 3: Job Inspection Payload

**Files:**
- Create: `academic_engine/job_inspector.py`
- Modify: `academic_engine/job_queue.py`
- Create: `tests/test_job_inspector.py`

- [ ] **Step 1: Write failing inspection tests**

Create `tests/test_job_inspector.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from academic_engine.job_inspector import inspect_job
from academic_engine.job_queue import JobQueue, WorkflowJobSpec


class JobInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)
        self.queue = JobQueue(self.root, now=lambda: "2026-06-23T10:00:00+00:00", id_factory=lambda: "job-demo")
        self.job = self.queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "section-1"))
        self.job["workflow_id"] = "wf-demo"
        self.queue._transition(self.job, status="running", event="job-dispatched", details={"workflow_id": "wf-demo"})
        self.workflow_dir = self.root / "output" / "runs" / "wf-demo"
        self.workflow_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_inspection_merges_timeline_durations_failure_and_changed_files(self) -> None:
        (self.workflow_dir / "events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"timestamp": "2026-06-23T10:00:01+00:00", "event": "workflow-queued"}),
                    json.dumps({"timestamp": "2026-06-23T10:00:02+00:00", "event": "role-started", "role_run_id": "01-role"}),
                    json.dumps({"timestamp": "2026-06-23T10:00:12+00:00", "event": "role-finished", "role_run_id": "01-role"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (self.workflow_dir / "workflow.json").write_text(
            json.dumps(
                {
                    "version": "workflow-run/v1",
                    "workflow_id": "wf-demo",
                    "run_id": "wf-demo",
                    "work_id": "demo-work",
                    "lane": "thesis",
                    "action": "verify",
                    "status": "failed",
                    "execution_status": "failed",
                    "readiness_status": "strong-draft-with-blockers",
                    "started_at": "2026-06-23T10:00:00+00:00",
                    "finished_at": "2026-06-23T10:02:00+00:00",
                    "workflow_dir": str(self.workflow_dir),
                    "sandbox_dir": str(self.workflow_dir / "sandbox"),
                    "role_runs": [
                        {
                            "role_run_id": "01-role",
                            "role_id": "thesis-source-verifier",
                            "status": "failed",
                            "started_at": "2026-06-23T10:00:02+00:00",
                            "finished_at": "2026-06-23T10:00:12+00:00",
                            "reported_status": "failed",
                            "error": "source missing",
                            "blockers": [{"category": "primary-support", "code": "missing-source", "message": "Add source."}],
                            "changed_paths": ["works/demo-work/thesis/sources/source-pack.md"],
                            "output_file": str(self.workflow_dir / "roles" / "01-role" / "output.md"),
                        }
                    ],
                    "gates": [{"gate_id": "required-output", "status": "block", "blocking": True, "reason": "missing"}],
                    "gate_summary": {"block": 1},
                    "blockers": [{"category": "runtime", "code": "workflow-failed", "message": "Workflow failed."}],
                    "promotion": {"status": "blocked", "reason": "Workflow did not promote."},
                    "promotion_status": "blocked",
                    "evaluator_verdict": None,
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertEqual(payload["kind"], "job-inspection")
        self.assertEqual(payload["job"]["job_id"], "job-demo")
        self.assertEqual(payload["durations"]["total_seconds"], 120.0)
        self.assertEqual(payload["durations"]["roles"][0]["duration_seconds"], 10.0)
        self.assertEqual(payload["failure"]["role_id"], "thesis-source-verifier")
        self.assertEqual(payload["failure"]["error"], "source missing")
        self.assertEqual(payload["blockers"][0]["code"], "workflow-failed")
        self.assertIn("works/demo-work/thesis/sources/source-pack.md", payload["changed_files"])
        self.assertTrue(payload["attachments"]["workflow"]["exists"])
        self.assertTrue(any(item["event"] == "workflow-queued" for item in payload["timeline"]))

    def test_missing_or_malformed_files_are_observability_warnings(self) -> None:
        (self.workflow_dir / "workflow.json").write_text("{bad json", encoding="utf-8")

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertEqual(payload["kind"], "job-inspection")
        self.assertTrue(any("workflow.json" in item["path"] for item in payload["observability_warnings"]))
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_job_inspector -q
```

Expected: FAIL with missing `academic_engine.job_inspector`.

- [ ] **Step 3: Implement inspector**

Create `academic_engine/job_inspector.py` with:

- `inspect_job(root_dir: str | Path, job: dict[str, Any], *, export_blockers: list[dict[str, Any]] | None = None) -> dict[str, Any]`.
- Helpers:
  - `_read_json(path: Path, warnings: list[dict[str, Any]])`.
  - `_read_events(path: Path, warnings: list[dict[str, Any]])`.
  - `_seconds_between(started_at: object, finished_at: object) -> float | None` using `utils.parse_datetime`.
  - `_attachments(paths: dict[str, Path | None]) -> dict[str, dict[str, Any]]`.
- Timeline format:

```python
{"timestamp": "...", "event": "...", "source": "job" | "workflow", "details": {...}}
```

- Failure selection:
  - First role with `status != "succeeded"`.
  - If no failed role, use `job["failure"]`.
- Blockers:
  - Workflow-level blockers first.
  - Role blockers next.
- Changed files:
  - Unique sorted union of all `role_run["changed_paths"]`.

Add `JobQueue.inspect_job(job_id: str) -> dict[str, Any]` as a thin wrapper that imports and calls `inspect_job(self.root_dir, self.get_job(job_id))`.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_job_inspector tests.test_job_queue -q
```

Expected: both modules pass.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/job_inspector.py academic_engine/job_queue.py tests/test_job_inspector.py
git commit -m "feat: inspect workflow job runs"
```

---

### Task 4: Export Blocker Explanation

**Files:**
- Create: `academic_engine/export_explain.py`
- Create: `tests/test_export_explain.py`

- [ ] **Step 1: Write failing export explanation tests**

Create `tests/test_export_explain.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from academic_engine.export_explain import explain_export
from tests.test_academic_engine import TEST_ARTICLE_FINAL, TEST_WORK_ID, build_fake_repo


class ExportExplainTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)
        build_fake_repo(self.root)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_no_successful_workflow_blocks_export(self) -> None:
        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)

        self.assertEqual(payload["kind"], "export-explanation")
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "no-successful-workflow")

    def test_non_submission_ready_workflow_blocks_export(self) -> None:
        _write_workflow(self.root, "wf-blocked", lane="thesis", execution_status="succeeded", readiness_status="strong-draft-with-blockers")

        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "latest-workflow-not-submission-ready")

    def test_failed_mandatory_gate_blocks_export(self) -> None:
        _write_workflow(
            self.root,
            "wf-gate",
            lane="thesis",
            execution_status="succeeded",
            readiness_status="submission-ready",
            gates=[{"gate_id": "required-output", "status": "block", "blocking": True, "reason": "missing"}],
        )

        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "mandatory-gates-failed")

    def test_promotion_conflict_blocks_export(self) -> None:
        _write_workflow(
            self.root,
            "wf-promotion",
            lane="thesis",
            execution_status="succeeded",
            readiness_status="submission-ready",
            promotion={"status": "conflict"},
        )

        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "promotion-not-safe")

    def test_missing_thesis_machine_gates_blocks_export(self) -> None:
        _write_workflow(self.root, "wf-ready", lane="thesis", execution_status="succeeded", readiness_status="submission-ready")

        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "machine-gates-not-passed")

    def test_missing_article_final_markdown_blocks_export(self) -> None:
        _write_workflow(self.root, "wf-article", lane="article", execution_status="succeeded", readiness_status="submission-ready")
        (self.root / TEST_ARTICLE_FINAL).unlink()

        payload = explain_export(self.root, "article:demo", work_id=TEST_WORK_ID)

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "article-final-markdown-missing")

    def test_ready_when_article_workflow_and_final_markdown_exist(self) -> None:
        _write_workflow(self.root, "wf-article-ready", lane="article", execution_status="succeeded", readiness_status="submission-ready")

        payload = explain_export(self.root, "article:demo", work_id=TEST_WORK_ID)

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["reasons"], [])


def _write_workflow(
    root: Path,
    workflow_id: str,
    *,
    lane: str,
    execution_status: str,
    readiness_status: str,
    gates: list[dict[str, object]] | None = None,
    promotion: dict[str, object] | None = None,
) -> None:
    workflow_dir = root / "output" / "runs" / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "workflow.json").write_text(
        json.dumps(
            {
                "version": "workflow-run/v1",
                "workflow_id": workflow_id,
                "run_id": workflow_id,
                "work_id": TEST_WORK_ID,
                "lane": lane,
                "action": "finalize" if lane == "article" else "verify",
                "status": "completed",
                "execution_status": execution_status,
                "readiness_status": readiness_status,
                "started_at": "2026-06-23T10:00:00+00:00",
                "finished_at": f"2026-06-23T10:0{len(workflow_id) % 9}:00+00:00",
                "workflow_dir": str(workflow_dir),
                "sandbox_dir": str(workflow_dir / "sandbox"),
                "role_runs": [],
                "gates": gates or [],
                "gate_summary": {},
                "blockers": [],
                "promotion": promotion or {"status": "promoted"},
                "promotion_status": (promotion or {"status": "promoted"})["status"],
                "evaluator_verdict": None,
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_export_explain -q
```

Expected: FAIL with missing `academic_engine.export_explain`.

- [ ] **Step 3: Implement export explanation**

Create `academic_engine/export_explain.py` with:

- `explain_export(root_dir: str | Path, subject: str, *, work_id: str | None = None) -> dict[str, Any]`.
- Use `load_workspace_config` and `resolve_work_config`.
- Determine lane:
  - `subject == "thesis"` -> lane `thesis`.
  - `subject.startswith("article:")` -> lane `article`, article slug from suffix.
  - unsupported subject -> blocked reason `unsupported-export-subject`.
- Scan `root/output/runs/*/workflow.json` for `version == "workflow-run/v1"`, matching `work_id` and lane.
- Sort candidates by `finished_at or started_at`, descending.
- Reasons:
  - no latest with `execution_status == "succeeded"` -> `no-successful-workflow`.
  - readiness not `submission-ready` -> `latest-workflow-not-submission-ready`.
  - any gate with `blocking` true and `status != "pass"` -> `mandatory-gates-failed`.
  - promotion status in `{"blocked", "conflict"}` -> `promotion-not-safe`.
  - thesis missing one-shot report with `status == "machine-gates-passed"` -> `machine-gates-not-passed`.
  - article missing final Markdown path from `_article_bundle_status` equivalent path resolution -> `article-final-markdown-missing`.
- Return `status: "ready"` only when reasons list is empty.

Do not import or call `export_docx()` from this module.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_export_explain -q
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/export_explain.py tests/test_export_explain.py
git commit -m "feat: explain export blockers"
```

---

### Task 5: EngineService Queue Facade

**Files:**
- Modify: `academic_engine/engine_service.py`
- Modify: `tests/test_engine_service.py`

- [ ] **Step 1: Write failing service facade tests**

Append to `tests/test_engine_service.py`:

```python
class EngineServiceJobQueueTests(unittest.TestCase):
    def test_submit_and_read_job_delegate_to_queue(self) -> None:
        queue = FakeJobQueue()
        service = EngineService("/tmp/example-root", job_queue_factory=lambda root, orchestrator_factory: queue)

        from academic_engine.engine_service import SubmitWorkflowJobRequest

        submitted = service.submit_workflow_job(
            SubmitWorkflowJobRequest(
                work_id="demo-work",
                lane="thesis",
                action="verify",
                target_or_topic="chapter-1",
                notes="check sources",
            )
        )
        fetched = service.get_job("job-demo")

        self.assertEqual(submitted["job_id"], "job-demo")
        self.assertEqual(fetched["job_id"], "job-demo")
        self.assertEqual(queue.submit_specs[0].work_id, "demo-work")

    def test_queue_control_methods_delegate_to_queue(self) -> None:
        queue = FakeJobQueue()
        service = EngineService("/tmp/example-root", job_queue_factory=lambda root, orchestrator_factory: queue)

        from academic_engine.engine_service import CancelJobRequest, DispatchJobsRequest, ResumeJobRequest, RetryJobRequest

        self.assertEqual(service.cancel_job(CancelJobRequest("job-demo", reason="stop"))["event"], "cancel")
        self.assertEqual(service.retry_job(RetryJobRequest("job-demo"))["event"], "retry")
        self.assertEqual(service.resume_job(ResumeJobRequest("job-demo"))["event"], "resume")
        self.assertEqual(service.dispatch_jobs(DispatchJobsRequest(limit=2))["kind"], "job-dispatch")

    def test_inspect_job_delegates_to_queue(self) -> None:
        queue = FakeJobQueue()
        service = EngineService("/tmp/example-root", job_queue_factory=lambda root, orchestrator_factory: queue)

        from academic_engine.engine_service import InspectJobRequest

        payload = service.inspect_job(InspectJobRequest("job-demo"))

        self.assertEqual(payload["kind"], "job-inspection")
        self.assertEqual(payload["job"]["job_id"], "job-demo")

    def test_explain_export_delegates_to_export_explainer(self) -> None:
        service = EngineService(
            "/tmp/example-root",
            export_explainer=lambda root, subject, work_id=None: {
                "kind": "export-explanation",
                "subject": subject,
                "work_id": work_id,
                "status": "blocked",
                "reasons": [{"code": "no-successful-workflow"}],
            },
        )

        payload = service.explain_export("thesis", work_id="demo-work")

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "no-successful-workflow")


class FakeJobQueue:
    def __init__(self) -> None:
        self.submit_specs: list[object] = []

    def submit_workflow(self, spec: object) -> dict[str, object]:
        self.submit_specs.append(spec)
        return {"kind": "engine-job", "job_id": "job-demo", "status": "queued"}

    def list_jobs(self, *, work_id: str | None = None, status: str | None = None) -> list[dict[str, object]]:
        return [{"job_id": "job-demo", "work_id": work_id, "status": status or "queued"}]

    def get_job(self, job_id: str) -> dict[str, object]:
        return {"job_id": job_id, "status": "queued"}

    def cancel_job(self, job_id: str, *, reason: str) -> dict[str, object]:
        return {"job_id": job_id, "event": "cancel", "reason": reason}

    def retry_job(self, job_id: str) -> dict[str, object]:
        return {"job_id": job_id, "event": "retry"}

    def resume_job(self, job_id: str) -> dict[str, object]:
        return {"job_id": job_id, "event": "resume"}

    def dispatch_jobs(self, *, limit: int | None = None) -> dict[str, object]:
        return {"kind": "job-dispatch", "limit": limit, "dispatched": []}

    def inspect_job(self, job_id: str) -> dict[str, object]:
        return {"kind": "job-inspection", "job": {"job_id": job_id}}
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_engine_service.EngineServiceJobQueueTests -q
```

Expected: FAIL with `TypeError` for unexpected `job_queue_factory` or missing request dataclasses.

- [ ] **Step 3: Implement service facade**

In `academic_engine/engine_service.py`:

- Import `JobQueue`, `WorkflowJobSpec`, and `explain_export`.
- Add dataclasses:

```python
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
```

- Extend `EngineService.__init__`:

```python
job_queue_factory: Callable[[Path, Callable[[Path], Any]], Any] | None = None,
export_explainer: Callable[..., dict[str, Any]] | None = None,
```

- Add methods:

```python
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

def list_jobs(self, *, work_id: str | None = None, status: str | None = None) -> dict[str, Any]:
    return {"kind": "job-list", "version": "v1", "jobs": self._job_queue().list_jobs(work_id=work_id, status=status)}

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
```

- Add `_job_queue()` helper that uses the injected factory when present, otherwise `JobQueue(self.root_dir, orchestrator_factory=self._orchestrator_factory)`.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_engine_service tests.test_job_queue tests.test_job_inspector tests.test_export_explain -q
```

Expected: all listed modules pass.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/engine_service.py tests/test_engine_service.py
git commit -m "feat: expose job queue through engine service"
```

---

### Task 6: CLI Queue Commands

**Files:**
- Modify: `academic_engine/work_cli.py`
- Create: `tests/test_work_cli_jobs.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_work_cli_jobs.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from academic_engine import work_cli as work_cli_module


class WorkCliJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def run_cli(self, argv: list[str]) -> tuple[int, str, str, FakeService]:
        fake = FakeService(self.root)
        stdout = StringIO()
        stderr = StringIO()
        with patch.object(work_cli_module, "EngineService", lambda root: fake):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(argv, root_dir=self.root)
        return code, stdout.getvalue(), stderr.getvalue(), fake

    def test_jobs_submit_workflow_json(self) -> None:
        code, stdout, stderr, fake = self.run_cli(
            [
                "jobs",
                "submit-workflow",
                "--work",
                "demo-work",
                "--lane",
                "thesis",
                "--action",
                "verify",
                "--target",
                "chapter-1",
                "--notes",
                "check",
                "--json",
            ]
        )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["job_id"], "job-demo")
        self.assertEqual(fake.submitted[0].work_id, "demo-work")
        self.assertEqual(fake.submitted[0].target_or_topic, "chapter-1")

    def test_jobs_list_status_and_dispatch_json(self) -> None:
        for argv, expected_kind in (
            (["jobs", "list", "--status", "queued", "--json"], "job-list"),
            (["jobs", "status", "job-demo", "--json"], "engine-job"),
            (["jobs", "dispatch", "--limit", "1", "--json"], "job-dispatch"),
        ):
            code, stdout, stderr, _fake = self.run_cli(argv)
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(json.loads(stdout)["kind"], expected_kind)

    def test_jobs_cancel_retry_resume_text(self) -> None:
        for argv, expected in (
            (["jobs", "cancel", "job-demo", "--reason", "operator"], "blocked"),
            (["jobs", "retry", "job-demo"], "queued"),
            (["jobs", "resume", "job-demo"], "queued"),
        ):
            code, stdout, stderr, _fake = self.run_cli(argv)
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertIn("job-demo", stdout)
            self.assertIn(expected, stdout)

    def test_job_inspect_json(self) -> None:
        code, stdout, stderr, _fake = self.run_cli(["job-inspect", "job-demo", "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "job-inspection")

    def test_export_explain_json(self) -> None:
        code, stdout, stderr, _fake = self.run_cli(["export-explain", "thesis", "--work", "demo-work", "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "export-explanation")
        self.assertEqual(payload["status"], "blocked")


class FakeService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.submitted: list[object] = []

    def submit_workflow_job(self, request: object) -> dict[str, object]:
        self.submitted.append(request)
        return {"kind": "engine-job", "job_id": "job-demo", "status": "queued", "work_id": request.work_id}

    def list_jobs(self, *, work_id: str | None = None, status: str | None = None) -> dict[str, object]:
        return {"kind": "job-list", "version": "v1", "jobs": [{"job_id": "job-demo", "work_id": work_id, "status": status or "queued"}]}

    def get_job(self, job_id: str) -> dict[str, object]:
        return {"kind": "engine-job", "job_id": job_id, "status": "queued", "work_id": "demo-work"}

    def cancel_job(self, request: object) -> dict[str, object]:
        return {"kind": "engine-job", "job_id": request.job_id, "status": "blocked", "work_id": "demo-work"}

    def retry_job(self, request: object) -> dict[str, object]:
        return {"kind": "engine-job", "job_id": request.job_id, "status": "queued", "work_id": "demo-work"}

    def resume_job(self, request: object) -> dict[str, object]:
        return {"kind": "engine-job", "job_id": request.job_id, "status": "queued", "work_id": "demo-work"}

    def dispatch_jobs(self, request: object) -> dict[str, object]:
        return {"kind": "job-dispatch", "version": "v1", "dispatched": [], "skipped": [], "blocked": [], "reconciled": [], "limit": request.limit}

    def inspect_job(self, request: object) -> dict[str, object]:
        return {"kind": "job-inspection", "version": "v1", "job": {"job_id": request.job_id}}

    def explain_export(self, subject: str, *, work_id: str | None = None) -> dict[str, object]:
        return {"kind": "export-explanation", "version": "v1", "subject": subject, "work_id": work_id, "status": "blocked", "reasons": [{"code": "no-successful-workflow"}]}
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_work_cli_jobs -q
```

Expected: FAIL because `jobs`, `job-inspect`, and `export-explain` commands are not registered.

- [ ] **Step 3: Add CLI parser and handlers**

In `academic_engine/work_cli.py`:

- Extend the import from `engine_service` to include:

```python
CancelJobRequest,
DispatchJobsRequest,
InspectJobRequest,
ResumeJobRequest,
RetryJobRequest,
SubmitWorkflowJobRequest,
```

- Add parsers after `work-status`:

```python
jobs_parser = subparsers.add_parser("jobs")
jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command", required=True)

jobs_submit = jobs_subparsers.add_parser("submit-workflow")
jobs_submit.add_argument("--work", dest="work_id", required=True)
jobs_submit.add_argument("--lane", required=True, choices=("thesis", "article"))
jobs_submit.add_argument("--action", required=True)
jobs_submit.add_argument("--target", dest="target_or_topic", required=True)
jobs_submit.add_argument("--notes")
jobs_submit.add_argument("--search", dest="search_override", action="store_const", const=True)
jobs_submit.add_argument("--no-search", dest="search_override", action="store_const", const=False)
jobs_submit.add_argument("--model", dest="model_override")
jobs_submit.add_argument("--profile", dest="profile_override")
jobs_submit.add_argument("--json", action="store_true", dest="as_json")

jobs_list = jobs_subparsers.add_parser("list")
jobs_list.add_argument("--work", dest="work_id")
jobs_list.add_argument("--status")
jobs_list.add_argument("--json", action="store_true", dest="as_json")

jobs_status = jobs_subparsers.add_parser("status")
jobs_status.add_argument("job_id")
jobs_status.add_argument("--json", action="store_true", dest="as_json")

jobs_cancel = jobs_subparsers.add_parser("cancel")
jobs_cancel.add_argument("job_id")
jobs_cancel.add_argument("--reason", default="operator-cancelled")
jobs_cancel.add_argument("--json", action="store_true", dest="as_json")

jobs_retry = jobs_subparsers.add_parser("retry")
jobs_retry.add_argument("job_id")
jobs_retry.add_argument("--json", action="store_true", dest="as_json")

jobs_resume = jobs_subparsers.add_parser("resume")
jobs_resume.add_argument("job_id")
jobs_resume.add_argument("--json", action="store_true", dest="as_json")

jobs_dispatch = jobs_subparsers.add_parser("dispatch")
jobs_dispatch.add_argument("--limit", type=int)
jobs_dispatch.add_argument("--json", action="store_true", dest="as_json")

job_inspect = subparsers.add_parser("job-inspect")
job_inspect.add_argument("job_id")
job_inspect.add_argument("--json", action="store_true", dest="as_json")

export_explain_parser = subparsers.add_parser("export-explain")
export_explain_parser.add_argument("subject")
export_explain_parser.add_argument("--work", dest="work_id")
export_explain_parser.add_argument("--json", action="store_true", dest="as_json")
```

- Add dispatch branches before `work`:

```python
if args.command == "jobs":
    return jobs_cli(root_path, args)
if args.command == "job-inspect":
    return job_inspect_cli(root_path, args.job_id, as_json=args.as_json)
if args.command == "export-explain":
    return export_explain_cli(root_path, args.subject, args.work_id, as_json=args.as_json)
```

- Add helper functions near `work_status()`:

```python
def jobs_cli(root_dir: Path, args: Any) -> int:
    service = EngineService(root_dir)
    if args.jobs_command == "submit-workflow":
        payload = service.submit_workflow_job(
            SubmitWorkflowJobRequest(
                work_id=args.work_id,
                lane=args.lane,
                action=args.action,
                target_or_topic=args.target_or_topic,
                notes=args.notes,
                search_override=args.search_override,
                model_override=args.model_override,
                profile_override=args.profile_override,
            )
        )
    elif args.jobs_command == "list":
        payload = service.list_jobs(work_id=args.work_id, status=args.status)
    elif args.jobs_command == "status":
        payload = service.get_job(args.job_id)
    elif args.jobs_command == "cancel":
        payload = service.cancel_job(CancelJobRequest(args.job_id, reason=args.reason))
    elif args.jobs_command == "retry":
        payload = service.retry_job(RetryJobRequest(args.job_id))
    elif args.jobs_command == "resume":
        payload = service.resume_job(ResumeJobRequest(args.job_id))
    elif args.jobs_command == "dispatch":
        payload = service.dispatch_jobs(DispatchJobsRequest(limit=args.limit))
    else:
        return 1
    _print_job_payload(payload, as_json=args.as_json)
    return 0


def job_inspect_cli(root_dir: Path, job_id: str, *, as_json: bool = False) -> int:
    payload = EngineService(root_dir).inspect_job(InspectJobRequest(job_id))
    _print_job_payload(payload, as_json=as_json)
    return 0


def export_explain_cli(root_dir: Path, subject: str, work_id: str | None, *, as_json: bool = False) -> int:
    payload = EngineService(root_dir).explain_export(subject, work_id=work_id)
    _print_job_payload(payload, as_json=as_json)
    return 0


def _print_job_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    kind = payload.get("kind")
    if kind == "job-list":
        jobs = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
        print(f"Jobs: {len(jobs)}")
        for job in jobs:
            print(f"- {job.get('job_id')}: {job.get('status')} work={job.get('work_id')}")
        return
    if kind == "job-dispatch":
        print(f"Dispatched: {len(payload.get('dispatched') or [])}")
        print(f"Skipped: {len(payload.get('skipped') or [])}")
        print(f"Blocked: {len(payload.get('blocked') or [])}")
        print(f"Reconciled: {len(payload.get('reconciled') or [])}")
        return
    if kind == "job-inspection":
        job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
        print(f"Job: {job.get('job_id')} status={job.get('status')}")
        print(f"Timeline events: {len(payload.get('timeline') or [])}")
        print(f"Changed files: {len(payload.get('changed_files') or [])}")
        return
    if kind == "export-explanation":
        print(f"Export {payload.get('subject')}: {payload.get('status')}")
        for reason in payload.get("reasons") or []:
            print(f"- {reason.get('code')}: {reason.get('message')}")
        return
    print(f"Job: {payload.get('job_id')} status={payload.get('status')} work={payload.get('work_id')}")
```

- Extend the `except` tuple in `main()` to include `JobQueueError` after importing it from `job_queue`.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_work_cli_jobs tests.test_engine_service tests.test_work_cli_runtime -q
```

Expected: all listed modules pass.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/work_cli.py tests/test_work_cli_jobs.py
git commit -m "feat: add job queue cli commands"
```

---

### Task 7: Integration Verification and Cleanup

**Files:**
- Modify only files already changed in earlier tasks if verification finds formatting or import-order issues.

- [ ] **Step 1: Run targeted regression tests**

Run:

```bash
python3 -m unittest tests.test_job_queue tests.test_job_inspector tests.test_export_explain tests.test_engine_service tests.test_work_cli_jobs tests.test_work_cli_runtime tests.test_work_cli_autonomous tests.test_work_state -q
```

Expected: all targeted tests pass.

- [ ] **Step 2: Run full suite**

Run:

```bash
python3 -m unittest discover -s tests -q
```

Expected: full suite passes.

- [ ] **Step 3: Run ruff checks**

Run:

```bash
ruff check academic_engine/ tests/
```

Expected: `All checks passed!`

Run:

```bash
ruff format --check academic_engine/ tests/
```

Expected: all files already formatted, or a clean success message from ruff. If formatting fails, run `ruff format academic_engine/ tests/`, then re-run both ruff commands.

- [ ] **Step 4: Confirm git state and recent commits**

Run:

```bash
git status --short
git log --oneline -8
```

Expected: only intentional changes remain, and recent commits show each feature slice.

- [ ] **Step 5: Final commit if cleanup changed files**

If Step 3 formatted files or Step 1/2 required small cleanup, commit only those changes:

```bash
git add academic_engine tests
git commit -m "test: verify job queue integration"
```

If there are no cleanup changes, do not create an empty commit.

---

## Self-Review Checklist

- Spec coverage:
  - Durable job records: Task 1.
  - Public states and lifecycle: Task 1.
  - Cancel/retry/resume: Task 1 and Task 5.
  - Dispatcher and limits: Task 2.
  - Reconciliation: Task 2.
  - Observability snapshot: Task 3.
  - Export explanation: Task 4.
  - EngineService facade: Task 5.
  - CLI surface: Task 6.
  - Verification: Task 7.
- Type consistency:
  - Queue spec type: `WorkflowJobSpec`.
  - Service request type: `SubmitWorkflowJobRequest`.
  - CLI request field: `target_or_topic`, parsed from `--target`.
  - Public job version: `job/v1`.
  - Public inspection version: `v1`.
- Compatibility guard:
  - Existing launch commands remain immediate.
  - Existing export commands remain fail-closed.
  - `output/runs/<workflow_id>/` layout is read, not changed.
