# Provider Executor Abstraction Design

## Goal

Introduce a provider/executor boundary so the workflow engine can route role execution through different runtime adapters without weakening the existing file-first, fail-closed pipeline.

The v1 target is a hybrid stub design:

- keep Codex CLI as the default executor for all roles;
- add explicit routing for default, evaluator, and verifier execution paths;
- add API-shaped stub executors and configuration seams without live provider calls;
- make a later OpenAI/OpenRouter-compatible executor a small follow-up rather than a new architecture.

## Scope

This design covers the engine-side executor abstraction only.

In scope:

- typed role execution context;
- executor protocol;
- Codex CLI executor wrapper;
- API-shaped stub executor for evaluator and verifier routes;
- executor router and factory;
- default-preserving CLI integration;
- focused tests for routing, fail-closed behavior, and workflow compatibility.

Out of scope for v1:

- live OpenAI, OpenRouter, or local-model API calls;
- new runtime dependencies;
- HTTP backend or web UI changes;
- replacing `WorkflowEngine` as workflow authority;
- changing `role-result/v1`, promotion, gates, write-scope checks, or output directory layout.

## Recommended Approach

Use an executor router with a typed run context.

`WorkflowEngine` should stop calling a single untyped callable directly. Instead, when `_run_role()` has trusted engine-side role metadata, it creates a `RoleExecutionContext` and passes that context plus the prompt to an `ExecutorRouter`.

The router chooses among configured executor routes:

- default route for ordinary roles;
- evaluator route for `thesis-submission-evaluator` and `academic-submission-evaluator`;
- verifier route for source-verifier roles;
- future provider routes for OpenAI/OpenRouter-compatible APIs.

The executor writes raw role output to `context.output_file`. After that, `WorkflowEngine` continues to parse and validate the `role-result/v1` payload exactly as it does now.

This keeps executors as transport/runtime adapters. They do not decide whether a role succeeded, whether artifacts are valid, or whether promotion is allowed.

## Alternatives Considered

### Add Executor Fields To RoleNode

`RoleNode` could grow fields such as `executor_kind`.

This is simple, but it mixes role planning with provider/runtime selection. Role plans should decide which academic role runs next; routing should decide how that role is executed in the current environment.

### Let One Executor Inspect Prompt Text

The existing callable could stay in place, and the executor could inspect prompt text to decide between Codex, evaluator, verifier, or API behavior.

This hides routing decisions inside string parsing, makes fail-closed behavior harder to test, and makes live-provider support fragile.

### Implement Live API Executors Immediately

The v1 could include live OpenAI/OpenRouter calls.

This adds too many variables at once: provider credentials, network failures, model output shape, retry behavior, timeout handling, and structured result compliance. It is safer to make routing and contracts explicit first, then add live provider execution as a narrow follow-up.

## Architecture

Add a new module, `academic_engine/executors.py`, with:

- `RoleExecutionContext`;
- `RoleExecutorProtocol`;
- `CodexCliExecutor`;
- `StubApiExecutor`;
- `ExecutorRouter`;
- `build_executor_router(...)`.

`WorkflowEngine` remains responsible for:

- workflow lifecycle;
- sandbox creation;
- prompt construction;
- request metadata;
- role retries and timeout policy;
- changed-path detection;
- write-scope enforcement;
- `role-result/v1` parsing and validation;
- gates, readiness, repairs, and promotion.

The CLI should construct a default router instead of passing `_run_codex` directly. With no explicit executor configuration, the router sends all roles to `CodexCliExecutor`, preserving current behavior.

## RoleExecutionContext

The context should contain trusted engine-side facts only:

- `workflow_id`;
- `role_run_id`;
- `role_id`;
- `work_id`;
- `lane`;
- `action`;
- `sandbox_dir`;
- `output_file`;
- `use_search`;
- `model`;
- `timeout_seconds`;
- `is_evaluator`;
- `is_verifier`;
- `is_finalizer`.

Provider credentials must not be stored in workflow JSON, request JSON, event JSONL, or role output.

## Data Flow

1. `WorkflowEngine._run_role()` builds the role prompt and request metadata.
2. The engine creates a `RoleExecutionContext` from trusted `RoleNode` and workflow state.
3. `ExecutorRouter.execute(context, prompt)` selects the route.
4. The selected executor writes raw output to `context.output_file`.
5. The engine records changed paths.
6. The engine parses the fenced `role-result` block.
7. The dependency-free role-result validator accepts or rejects the result.
8. Existing gates, readiness, repair, and promotion logic run unchanged.

## Configuration

Default configuration:

- default executor: `codex-cli`;
- evaluator executor: unset, which means default route;
- verifier executor: unset, which means default route.

Environment variables can enable v1 stub routing:

- `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=codex-cli`;
- `ACADEMIC_ENGINE_EVALUATOR_EXECUTOR=stub-api`;
- `ACADEMIC_ENGINE_VERIFIER_EXECUTOR=stub-api`.

Unset route-specific variables inherit the default route. Explicit route selection must be respected and must not silently fall back.

The reserved future provider shape is:

- provider name;
- `base_url`;
- `api_key_env`;
- model;
- timeout seconds.

The future live executor should read secrets from environment variables at execution time and never serialize them.

## Error Handling

Executor routing is fail-closed.

- Unknown executor id: fail the role with a runtime blocker such as `executor-unavailable`.
- Explicitly configured but unavailable executor: fail the role with `executor-unavailable`.
- Stub executor selected in normal runtime without a test-provided output strategy: raise an executor-unavailable error that the engine records as an `executor-unavailable` runtime blocker.
- Provider credentials missing in future live mode: fail the role with a stable credential/config blocker, not a fallback to Codex.

No configured API or stub route should silently fall back to Codex. Fallback is only allowed through inheritance when a route-specific variable is unset.

## Testing

Use focused tests before implementation.

Executor tests:

- default router selects Codex for ordinary roles;
- evaluator route can be selected independently;
- verifier route can be selected independently;
- unknown explicit executor fails closed;
- explicit route does not silently fall back to default.

Workflow tests:

- routed execution still passes through `role-result/v1` validation;
- write-scope violations are still detected after routed execution;
- evaluator verdict handling still controls readiness;
- verifier gates still inspect verifier role output;
- CLI launch preserves current default Codex behavior when no executor env is set.

There should be no live network tests in v1.

## Follow-Up For Live Providers

The next step after v1 is `OpenAICompatibleExecutor`.

It should implement the same executor protocol and accept:

- `base_url`;
- `api_key_env`;
- model;
- timeout seconds;
- provider name.

It should be registered in the same router under a stable executor id such as `openai-compatible`. The engine should not need changes to support this executor if v1 is implemented correctly.

Live provider support must still obey the existing role-result and artifact rules. A provider is usable only if it can produce a valid `role-result/v1` payload and, for any role that writes files, valid artifact manifests with matching hashes.

## Acceptance Criteria

- Current CLI workflow behavior remains Codex CLI by default.
- Executor routing is explicit and tested.
- Evaluator and verifier routes can be configured independently.
- Explicit route misconfiguration fails closed with stable machine-readable blockers.
- No runtime dependency is added.
- No live network call is made in v1.
- `WorkflowEngine` remains the only authority for role-result validation, write-scope enforcement, gates, readiness, and promotion.
- Adding a live OpenAI/OpenRouter-compatible executor later requires adding an executor implementation and router registration, not redesigning workflow execution.
