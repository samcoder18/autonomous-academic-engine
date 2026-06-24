# Job Queue and Observability Design

## Goal

Add a durable job queue and run observability layer above the existing academic engine workflow core so CLI commands, the future local backend, and daemon controls can submit, inspect, cancel, retry, resume, and explain jobs through one stable service contract.

## Scope

This design covers the first core/backend-ready slice:

- Durable job records for workflow jobs.
- Explicit job states: `queued`, `running`, `blocked`, `failed`, `completed`.
- Cancel, retry, resume, and dispatch operations.
- Global and per-work concurrency limits.
- A black-box inspection payload for each job/run.
- Export-blocker explanation that reports the exact fail-closed reason.
- CLI access to the queue and inspection payloads.

This design does not introduce FastAPI, a web UI, authentication, a database, or a full worker-pool replacement. The current file-first model remains canonical: `works/<slug>/` for work content, `output/runs/<workflow_id>/` for workflow artifacts, and a new `output/runtime/jobs/` directory for queue state.

## Recommended Approach

Use a durable registry and dispatcher above the current launch path.

The queue should not replace `WorkflowOrchestrator` in this slice. It should wrap it:

- `WorkflowOrchestrator.start_run()` remains the low-level workflow launcher.
- `WorkflowEngine` remains the authority for `workflow-run/v1`, `role-result/v1`, `events.jsonl`, gates, promotion, role blockers, and changed paths.
- `EngineService` becomes the public core-facing facade for job operations.
- `RuntimeStore` remains available for active-run compatibility while the new queue records become the durable queue source of truth.

This keeps the external service/API contract stable while avoiding a broad launcher rewrite.

## Job Model

Create a focused queue module, tentatively `academic_engine/job_queue.py`.

Each job is persisted as JSON under:

```text
output/runtime/jobs/<job_id>.json
```

Use atomic writes and plain dictionaries compatible with future JSON APIs. A job payload has this shape:

```json
{
  "kind": "engine-job",
  "version": "job/v1",
  "job_id": "job-...",
  "work_id": "starter-work",
  "job_type": "workflow",
  "status": "queued",
  "created_at": "2026-06-23T...",
  "updated_at": "2026-06-23T...",
  "attempt": 0,
  "max_attempts": 3,
  "workflow_id": null,
  "active_run_id": null,
  "payload": {
    "lane": "thesis",
    "action": "write-section",
    "target_or_topic": "chapter-1"
  },
  "limits": {
    "global_concurrency": 2,
    "per_work_concurrency": 1
  },
  "blocked_reason": null,
  "failure": null,
  "history": []
}
```

`history` is append-only and stores compact lifecycle events:

```json
{
  "timestamp": "2026-06-23T...",
  "event": "job-dispatched",
  "status": "running",
  "details": {
    "workflow_id": "..."
  }
}
```

## Job States

The queue exposes exactly these public states:

- `queued`: waiting for dispatcher capacity.
- `running`: a workflow has been started and linked to the job.
- `blocked`: operator or engine intervention is required; retry is not automatic.
- `failed`: the linked run ended with a failure that can be retried if attempts remain.
- `completed`: the linked run finished successfully.

Derived statuses such as `cancelled`, `waiting`, `interrupted`, or `stale` should appear as `blocked_reason`, `failure.code`, or history events, not as extra public states.

State transitions:

```text
queued -> running
queued -> blocked
running -> completed
running -> failed
running -> blocked
failed -> queued       retry
blocked -> queued      resume
running -> blocked     cancel
```

`cancel_job()` in this slice is fail-closed and cooperative: it records an operator cancellation and calls the existing stop path where available. It does not promise process-group termination until the later worker-pool slice.

## Queue Operations

Expose queue operations through `EngineService` request dataclasses.

New request dataclasses:

- `SubmitWorkflowJobRequest`
- `CancelJobRequest`
- `RetryJobRequest`
- `ResumeJobRequest`
- `DispatchJobsRequest`
- `InspectJobRequest`

New service methods:

- `submit_workflow_job(request) -> dict`
- `list_jobs(work_id: str | None = None, status: str | None = None) -> dict`
- `get_job(job_id: str) -> dict`
- `cancel_job(request) -> dict`
- `retry_job(request) -> dict`
- `resume_job(request) -> dict`
- `dispatch_jobs(request) -> dict`
- `inspect_job(request) -> dict`
- `explain_export(subject: str, work_id: str | None = None) -> dict`

Existing `start_workflow()` remains available for compatibility, but new API/backend-facing code should prefer `submit_workflow_job()` followed by dispatcher execution. CLI launch commands may remain on the existing immediate path until they are deliberately moved.

## Dispatching

`dispatch_jobs()` is a small deterministic dispatcher:

1. Sync existing active runs through `WorkflowOrchestrator.sync_active_run()`.
2. Reconcile `running` jobs whose linked workflow has finished.
3. Count active jobs and active runs.
4. Pick oldest queued jobs while respecting:
   - global concurrency default: `2`;
   - per-work concurrency default: `1`.
5. Call `WorkflowOrchestrator.start_run()` for selected workflow jobs.
6. Link returned `workflow_id`, `run_id`, and `active_run` metadata back to the job.
7. Return a JSON-ready summary of dispatched, skipped, blocked, and reconciled jobs.

If `WorkflowOrchestrator.start_run()` raises `RunBusyError`, the job remains `queued` unless the per-work conflict is stale and can be reconciled first. If it raises a validation/config error, the job moves to `blocked` with a structured failure.

The first slice should be synchronous and explicit: a CLI or daemon calls `dispatch_jobs`. A persistent worker loop can be added later without changing the job file format.

## Reconciliation

Reconciliation maps existing runtime artifacts back onto jobs:

- If the linked `workflow.json` has `execution_status == "succeeded"`, mark the job `completed`.
- If the linked `workflow.json` has `execution_status == "failed"`, mark the job `failed`.
- If the active run disappeared and no terminal artifact exists, mark the job `blocked` with `failure.code = "missing-runtime-result"`.
- If a job is `running` but the active run belongs to another work/job, mark it `blocked` with `failure.code = "runtime-link-mismatch"`.

This is deliberately conservative. The system should surface ambiguity instead of pretending the job succeeded.

## Observability Snapshot

`inspect_job()` returns a black-box payload suitable for CLI JSON and future UI/API views:

```json
{
  "kind": "job-inspection",
  "version": "v1",
  "job": {},
  "timeline": [],
  "durations": {
    "total_seconds": 120.4,
    "roles": [
      {
        "role_run_id": "01-academic-intake",
        "role_id": "academic-intake",
        "status": "succeeded",
        "duration_seconds": 8.2
      }
    ]
  },
  "failure": null,
  "blockers": [],
  "changed_files": [],
  "export_blockers": [],
  "attachments": {}
}
```

The snapshot should collect:

- Job lifecycle history from the job JSON.
- Workflow lifecycle from `output/runs/<workflow_id>/events.jsonl`.
- Role start/finish timestamps from role run records.
- Role duration per `role_run_id`.
- First failed role, its error, reported status, blockers, and output path.
- All blockers from workflow and role results.
- All changed files from role results and promotion data.
- Gate results and promotion status.
- Attachments: job file, workflow file, events, gates, promotion, role outputs, status, request, result, log.

Missing or malformed files should be reported as `observability_warnings`, not hidden.

## Export Explanation

Add an explain-only path separate from `export_docx()`.

`EngineService.explain_export(subject, work_id)` should return:

```json
{
  "kind": "export-explanation",
  "version": "v1",
  "subject": "thesis",
  "work_id": "starter-work",
  "status": "blocked",
  "reasons": [
    {
      "code": "latest-workflow-not-submission-ready",
      "message": "Latest workflow readiness is strong-draft-with-blockers.",
      "details": {}
    }
  ]
}
```

It must explain the existing fail-closed export gates without weakening them:

- no successful workflow v1 for work/lane;
- latest workflow readiness is not `submission-ready`;
- latest workflow contains failed mandatory gates;
- latest workflow promotion is blocked/conflicted;
- thesis one-shot machine gates have not passed;
- article final Markdown is missing;
- export subject is unknown or unsupported.

`export_docx()` continues to raise `WorkflowError` and must not export when `explain_export()` reports `blocked`.

## CLI Surface

Add a compact queue CLI under the existing `work_cli` module:

```text
python3 -m academic_engine.work_cli jobs submit-workflow --lane thesis --action write-section --target chapter-1 --work starter-work
python3 -m academic_engine.work_cli jobs list [--work starter-work] [--status queued] [--json]
python3 -m academic_engine.work_cli jobs status <job_id> [--json]
python3 -m academic_engine.work_cli jobs cancel <job_id> [--reason "..."] [--json]
python3 -m academic_engine.work_cli jobs retry <job_id> [--json]
python3 -m academic_engine.work_cli jobs resume <job_id> [--json]
python3 -m academic_engine.work_cli jobs dispatch [--limit 1] [--json]
python3 -m academic_engine.work_cli job-inspect <job_id> [--json]
python3 -m academic_engine.work_cli export-explain thesis --work starter-work [--json]
```

Text output should be short and operator-focused. JSON output is the contract for future API/UI.

## Compatibility Rules

- Do not change `workflow-run/v1`, `role-result/v1`, or existing `output/runs/<workflow_id>/` layout.
- Do not remove `active_run` in this slice.
- Do not change the semantics of `WorkflowOrchestrator.start_run()` in this slice.
- Do not weaken export fail-closed checks.
- Do not introduce a database or network service.
- Do not make CLI launch commands silently change from immediate start to queued execution without explicit command naming.
- Do not edit generated DOCX files or derived build artifacts as sources of truth.

## Testing

Use TDD for implementation.

Core queue tests:

- Submitting a workflow job persists a `queued` job with stable JSON fields.
- Listing jobs filters by work and status.
- Dispatching starts the oldest queued job and links the returned workflow id.
- Dispatching respects global and per-work concurrency.
- Canceling a queued job moves it to `blocked`.
- Canceling a running job records `operator-cancelled` and delegates to the existing stop path.
- Retrying a failed job increments attempt and returns it to `queued`.
- Resuming a blocked job returns it to `queued`.
- Reconciliation maps terminal `workflow.json` payloads to `completed` or `failed`.
- Ambiguous/missing runtime artifacts become `blocked`, not successful.

Observability tests:

- Inspecting a job merges job history and `events.jsonl` into a timeline.
- Role durations are computed from `started_at` and `finished_at`.
- The first failed role is reported with role id, error, blockers, and output path.
- Changed files are aggregated from role runs.
- Missing/malformed observability attachments produce warnings.

Export explanation tests:

- No successful workflow yields a blocked reason.
- Non-`submission-ready` readiness yields a blocked reason.
- Failed mandatory gates yield a blocked reason.
- Blocked/conflicted promotion yields a blocked reason.
- Missing thesis one-shot gates yields a blocked reason.
- Missing article final Markdown yields a blocked reason.
- Passing gates produce `status: "ready"`.

CLI tests:

- Each new command calls `EngineService` and preserves JSON payloads.
- Text output includes job id, status, work id, and next operator action.

## Acceptance Criteria

- Queue files are durable under `output/runtime/jobs/`.
- `EngineService` exposes submit/list/status/cancel/retry/resume/dispatch/inspect/export-explain methods.
- CLI exposes the first queue and inspection commands.
- The dispatcher can start queued workflow jobs without changing existing direct workflow launch behavior.
- Job state reconciliation is fail-closed.
- Inspection payloads show timeline, role durations, failed role, blockers, changed files, and export blockers.
- Full unit test suite and ruff checks pass.
