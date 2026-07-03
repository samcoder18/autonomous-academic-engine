# Runtime State Index Design

## Goal

Add a local runtime-state index for fast UI/API inspection while keeping the file-first engine model authoritative.

The index is a rebuildable cache under `output/runtime/`. It must never become the source of truth for work content, readiness, gates, promotion, or artifact safety.

## Scope

This design covers two related slices:

- A SQLite-backed runtime-state index with recent runs, work statuses, blockers, artifacts, and timestamps.
- A clearer regression-fixture layer for end-to-end safety scenarios that should remain true across engine changes.

The slice does not introduce FastAPI, frontend UI work, authentication, a new worker pool, a second canonical state store, or any third-party dependency. SQLite uses Python's standard-library `sqlite3`.

## Sources of Truth

The current authority chain remains unchanged:

- `workspace.toml` and `works/<slug>/work.toml` define registered works, active lanes, titles, and profiles.
- `works/<slug>/work-canon.md` and lane artifacts under `works/<slug>/` remain canonical work content.
- `output/runs/<workflow_id>/workflow.json`, `status.json`, `events.jsonl`, role results, gates, and promotion files remain authoritative runtime artifacts.
- Existing readers such as `runtime_status.load_runtime_record()`, `WorkflowOrchestrator.get_work_state()`, `explain_export()`, and one-shot reports remain the decision layer for status and blocking logic.

The SQLite index is derived from those files. Deleting `output/runtime/runtime-index.sqlite` must not change any engine decision; it only removes a fast projection until the next refresh.

## Recommended Approach

Create a focused `academic_engine/runtime_index.py` module. It owns schema setup, refresh, and read queries.

The module should not duplicate workflow decision logic. Refresh should call existing readers and compact their JSON-ready output into indexed rows:

- `load_workspace_config()` and `resolve_work_config()` for registered works.
- `WorkflowOrchestrator.get_work_state()` or `EngineService.get_work_status()` for current work status, known blockers, next actions, and artifact summaries.
- `runtime_status.load_runtime_record()` over `output/runs/*` for workflow run projections.
- Existing artifact paths from work state, runtime attachments, role results, one-shot reports, and article/thesis bundle manifests.

Expose index operations through `EngineService`, then CLI adapters can call the service without owning logic.

## Storage

Default path:

```text
output/runtime/runtime-index.sqlite
```

The index directory is already runtime-local and not versioned. The database is safe to delete and rebuild.

Use atomic-ish refresh semantics:

1. Open the SQLite database.
2. Create schema if missing.
3. Start a transaction.
4. Upsert current work rows.
5. Replace derived rows for refreshed works and runs.
6. Commit.
7. Update `index_metadata` with `refreshed_at`, `schema_version`, and source counts.

For the first slice, one full-workspace refresh is enough. Incremental refresh can be added later without changing the public payload.

## Data Model

Use a small schema optimized for list and detail queries.

`index_metadata`

- `key TEXT PRIMARY KEY`
- `value TEXT NOT NULL`

`works`

- `work_id TEXT PRIMARY KEY`
- `title TEXT`
- `artifact_type TEXT`
- `active_lanes_json TEXT NOT NULL`
- `status TEXT NOT NULL`
- `known_blocker_count INTEGER NOT NULL`
- `suggested_next_action_json TEXT`
- `work_state_json TEXT NOT NULL`
- `updated_at TEXT`

`runs`

- `record_id TEXT PRIMARY KEY`
- `workflow_id TEXT`
- `work_id TEXT`
- `lane TEXT`
- `action TEXT`
- `status TEXT NOT NULL`
- `stage TEXT`
- `readiness_status TEXT`
- `promotion_status TEXT`
- `started_at TEXT`
- `finished_at TEXT`
- `summary TEXT`
- `runtime_dir TEXT`
- `status_path TEXT`
- `source TEXT`
- `record_json TEXT NOT NULL`

`blockers`

- `blocker_id TEXT PRIMARY KEY`
- `work_id TEXT`
- `run_record_id TEXT`
- `lane TEXT`
- `category TEXT`
- `code TEXT`
- `message TEXT`
- `repairable INTEGER NOT NULL DEFAULT 0`
- `blocks_statuses_json TEXT NOT NULL`
- `source TEXT NOT NULL`
- `details_json TEXT NOT NULL`
- `created_at TEXT`

`artifacts`

- `artifact_id TEXT PRIMARY KEY`
- `work_id TEXT`
- `run_record_id TEXT`
- `lane TEXT`
- `artifact_type TEXT NOT NULL`
- `path TEXT NOT NULL`
- `exists INTEGER NOT NULL`
- `source TEXT NOT NULL`
- `metadata_json TEXT NOT NULL`
- `updated_at TEXT`

Rows should keep full JSON payloads where useful, but list views should not need to parse every source file.

## Public Payload

Add `EngineService.refresh_runtime_index() -> dict`:

```json
{
  "kind": "runtime-index-refresh",
  "version": "v1",
  "status": "refreshed",
  "index_path": "output/runtime/runtime-index.sqlite",
  "refreshed_at": "2026-07-03T...",
  "works_indexed": 1,
  "runs_indexed": 4,
  "blockers_indexed": 2,
  "artifacts_indexed": 12
}
```

Add `EngineService.get_runtime_index(work_id=None, limit=20) -> dict`:

```json
{
  "kind": "runtime-index",
  "version": "v1",
  "index_path": "output/runtime/runtime-index.sqlite",
  "refreshed_at": "2026-07-03T...",
  "works": [],
  "recent_runs": [],
  "blockers": [],
  "artifacts": []
}
```

If the database is missing, `get_runtime_index()` may either return an empty payload with `status: "missing"` or perform an explicit refresh if the caller requested it. It should not silently claim current data without a refresh timestamp.

## CLI

Add thin CLI commands after the service methods exist:

```text
python3 -m academic_engine.work_cli runtime-index refresh [--json]
python3 -m academic_engine.work_cli runtime-index status [--work <slug>] [--limit N] [--json]
```

The commands should not make independent indexing decisions. They call `EngineService` and format the returned dict.

## Regression Fixtures

Create a visible regression-fixture layer for engine safety scenarios. The tests should use temporary workspaces and existing helpers where possible.

Required scenarios:

1. Article without evidence is blocked from `submission-ready`.
   - A role/evaluator can claim readiness, but article runtime classification must downgrade or block when evidence and claim-map artifacts are absent.

2. VKR without originality corpus is blocked.
   - `run_one_shot()` over a VKR-style manuscript without an originality corpus must produce `blocked` with an originality-related blocker.

3. Evaluator says `submission-ready`, but a required gate fails.
   - Export/status must remain blocked when latest machine gates or contract gates fail, even if evaluator readiness says `submission-ready`.

4. Promotion conflict must not corrupt canon.
   - A workflow promotion conflict should leave canonical files unchanged and expose a blocked/conflict status through runtime/export explanation.

These fixtures should supplement, not replace, narrower unit tests. If existing tests already cover part of a scenario, keep them and add scenario-named regression tests that make the intended invariant obvious.

## Failure Handling

Index refresh should be conservative:

- Malformed runtime files become warning entries in refresh output where practical; they should not crash the whole refresh unless the workspace config cannot be loaded.
- Missing artifacts are indexed with `exists = 0`.
- Unknown blocker fields are preserved inside `details_json` or source JSON.
- Database errors should return a structured failure through the service/CLI path, not partially formatted text.

Runtime decisions remain fail-closed in the existing engine. The index reports what the engine readers expose.

## Testing Strategy

Use TDD for implementation:

- `tests/test_runtime_index.py` for schema creation, refresh output, cache deletability, query payloads, blocker extraction, artifact extraction, and no-decision-from-SQLite behavior.
- `tests/test_engine_service.py` for service delegation and JSON-ready return shape.
- `tests/test_work_cli_runtime.py` or a focused CLI test file for the new `runtime-index` commands.
- `tests/test_runtime_regression_fixtures.py` or an expanded `tests/test_regression_harness.py` for the four named safety fixtures.

Verification should include focused tests first, then ruff, then the full suite when the implementation is complete.

## Non-Goals

- No HTTP server or frontend changes in this slice.
- No background daemon auto-refresh.
- No replacement for `WorkflowEngine`, `WorkflowOrchestrator`, `work_state`, `explain_export`, or one-shot gates.
- No database-backed canonical work model.
- No new runtime dependency.

## Open Decisions Resolved

- Storage format: SQLite, because the user explicitly allowed it and the standard library is enough.
- Location: `output/runtime/runtime-index.sqlite`, because it is local runtime state.
- Authority: files and existing engine readers remain authoritative; SQLite is cache only.
- API surface: service first, CLI adapter second, future UI/API can consume the same JSON-ready payload.
