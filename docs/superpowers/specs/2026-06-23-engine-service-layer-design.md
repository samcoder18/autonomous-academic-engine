# Engine Service Layer Design

## Goal

Add a stable internal Python service layer over the existing academic engine core so CLI commands, the future local API/backend, and daemon-facing controls can call the same operations without duplicating orchestration logic.

## Scope

This design covers the first service-layer slice:

- Create a work bundle.
- Start a role workflow.
- Read work status.
- Stop a running autonomous job.
- Export DOCX artifacts.
- Preserve existing CLI command names, arguments, text output, JSON output, and fail-closed gate behavior.

This design does not introduce FastAPI, a web server, authentication, background job storage changes, or new workflow semantics. The service is an internal Python boundary designed to be API-ready later.

## Recommended Approach

Use a facade service: `academic_engine.engine_service.EngineService`.

`EngineService` should compose existing modules instead of replacing them:

- `work_bootstrap.bootstrap_work` remains the source of truth for work creation.
- `WorkflowOrchestrator` remains the source of truth for workflow launch, runtime records, work state, and DOCX export.
- `work_cli_autonomous` dependencies such as `stop_autonomous_run`, daemon status helpers, and daemon stop request helpers remain the source of truth for stop/status behavior.
- CLI modules become thin adapters that parse arguments, call `EngineService`, and format the returned payload.

This keeps the migration conservative: the new layer creates a stable contract without restructuring the workflow engine.

## Service API

Create `academic_engine/engine_service.py` with immutable request dataclasses:

- `CreateWorkRequest`
- `StartWorkflowRequest`
- `ExportRequest`
- `StopJobRequest`

Expose `EngineService(root_dir, *, orchestrator_factory=None)`.

The service methods return JSON-ready dictionaries:

- `create_work(request: CreateWorkRequest) -> dict`
- `start_workflow(request: StartWorkflowRequest) -> dict`
- `get_work_status(work_id: str | None = None) -> dict`
- `stop_job(request: StopJobRequest) -> dict`
- `export_docx(request: ExportRequest) -> dict`

Each payload includes:

- `kind`
- `version`
- operation-specific identifiers such as `work_id`, `workflow_id`, `lane`, `action`, or `path`
- fields already emitted by the existing CLI where applicable

The service does not print and does not call `sys.exit`.

## First Implementation Slice

Implement the slice in this order:

1. Add `EngineService.create_work()` and `EngineService.get_work_status()`.
2. Move the `work init` payload construction from `work_cli.py` into `EngineService.create_work()`.
3. Switch `work init` to call `EngineService.create_work()` while preserving the existing text and JSON output.
4. Switch `work-status` to call `EngineService.get_work_status()` while preserving existing behavior.

This proves the service boundary on one mutating operation and one read-only operation before workflow launch/export are moved.

## Follow-Up Slices

After the first slice is green:

1. Add `EngineService.start_workflow()` as a thin wrapper around `WorkflowOrchestrator.start_run()` for public queued workflow starts.
2. Add `EngineService.export_docx()` as a thin wrapper around `WorkflowOrchestrator.export_docx()`.
3. Add `EngineService.stop_job()` for autonomous stop requests, using existing single-work stop behavior first.
4. Move daemon status/start/stop helpers behind service methods only after the simpler autonomous stop/status boundary is stable.

## Error Handling

Service methods should raise existing domain exceptions where that preserves current behavior:

- `WorkBootstrapError` for invalid work creation requests.
- `WorkspaceConfigError` for workspace/work selection failures.
- `WorkflowError` for workflow and export failures.

The CLI remains responsible for mapping these exceptions to exit codes and stderr/stdout formatting. Future FastAPI code can map the same exceptions to HTTP responses without changing core behavior.

## Testing

Use TDD for each slice.

First slice tests:

- A new `tests/test_engine_service.py` verifies `create_work()` creates the same payload currently built by CLI.
- The same test file verifies `get_work_status()` returns the existing work-state payload shape, including `kind: "work-state"` and the selected work id.
- Existing CLI tests in `tests/test_work_bootstrap.py` continue to verify `work init` text output, JSON output, and invalid slug exit behavior after the CLI is routed through `EngineService`.
- Existing work-state tests remain the regression pack for next-action semantics.

Later slices should add service tests for queued workflow start, export result payloads, and stop request payloads before moving additional CLI commands.

## Compatibility Rules

- Do not edit generated DOCX outputs or thesis build artifacts as service-layer sources of truth.
- Do not change workflow ids, run directory layout, `output/runs/<workflow-id>/`, or fail-closed export gates.
- Do not weaken source connector defaults or live-mode flags.
- Do not introduce HTTP/backend dependencies in this slice.
- Do not rename public CLI commands.

## Acceptance Criteria

- `EngineService` exists and can be imported by future backend code.
- `work init` and `work-status` call `EngineService`.
- Existing CLI behavior remains compatible.
- New service tests fail before implementation and pass after implementation.
- Targeted tests and the relevant existing regression tests pass.
