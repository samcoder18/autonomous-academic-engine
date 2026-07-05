# OpenRouter Provider Integration Design

## Goal

Connect a third-party LLM through OpenRouter while preparing the executor layer for later deployment and future provider expansion.

The first slice must be a real live-provider integration, but it must not move workflow authority out of `WorkflowEngine` or let an API model write files directly.

## Scope

In scope:

- `OpenAICompatibleExecutor` as a reusable transport adapter;
- an `openrouter` executor id registered in the existing executor router;
- env-only provider configuration;
- route-specific OpenRouter execution for evaluator and verifier roles;
- fail-closed provider errors with stable blocker codes;
- deterministic tests without live network calls;
- explicit CLI smoke for live provider verification;
- short README operator instructions.

Out of scope:

- OpenRouter as the default executor for all roles;
- file-writing API model support;
- provider config in `workspace.toml`, `work.toml`, or UI;
- streaming responses;
- SDK or new runtime HTTP dependency;
- retry inside the provider executor;
- automatic live tests in CI;
- executor-side repair or rewriting of invalid model output.

## Recommended Approach

Use a limited live provider route.

`WorkflowEngine` already creates a trusted `RoleExecutionContext` and calls `ExecutorRouter`. The OpenRouter integration should extend that boundary rather than changing workflow execution.

The router should register `openrouter`, backed by a general `OpenAICompatibleExecutor`. The `openrouter` id should be allowed only for route-specific evaluator and verifier configuration:

```bash
ACADEMIC_ENGINE_EVALUATOR_EXECUTOR=openrouter
ACADEMIC_ENGINE_VERIFIER_EXECUTOR=openrouter
```

The default route must remain `codex-cli`. Setting `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter` is forbidden in this slice because ordinary writer/finalizer roles need safe file-writing semantics that a plain chat-completion executor does not provide.

## Alternatives Considered

### OpenRouter For All Roles

This moves faster toward full provider independence, but it is unsafe for the current engine. Codex CLI can operate in the sandbox and write files. A regular OpenRouter chat completion returns text only. Before writer roles can move to API models, the engine needs a structured file-write bridge with validated patches or artifact manifests.

### Smoke Only

This would add a client and a live check without using OpenRouter in real workflow routes. It is safer, but it does not prove that routing, blockers, and evaluator/verifier role execution work in the production path.

### Recommended Limited Route

The limited route connects a real third-party LLM now, exercises the executor router in real workflow roles, and keeps the file-writing surface on Codex CLI until a separate safe-write design exists.

## Architecture

Add provider support inside `academic_engine.executors`.

Primary units:

- `OpenAICompatibleExecutor`: reusable executor protocol implementation for OpenAI-compatible chat-completion providers.
- `OpenRouterClient`: small synchronous HTTP helper based on Python standard library facilities.
- `ProviderExecutionError`: provider-specific unavailable/config/error exception carrying a stable blocker code.
- `openrouter` registry entry: an `OpenAICompatibleExecutor` configured for OpenRouter.

Execution flow:

```text
WorkflowEngine
  -> ExecutorRouter
  -> openrouter route for evaluator/verifier
  -> OpenAICompatibleExecutor
  -> OpenRouter chat completions
  -> output.md
  -> WorkflowEngine parses and validates role-result/v1
```

`WorkflowEngine` remains responsible for:

- prompt construction;
- timeout and role attempts;
- `role-result/v1` parsing and validation;
- write-scope enforcement;
- gates;
- readiness;
- repairs;
- promotion.

The provider executor only sends the prompt and writes the returned text to `context.output_file`.

## OpenRouter Request

Use the OpenRouter OpenAI-compatible Chat Completions API:

- base URL: `https://openrouter.ai/api/v1`;
- endpoint: `/chat/completions`;
- authorization: `Authorization: Bearer <OPENROUTER_API_KEY>`;
- payload model: `ACADEMIC_ENGINE_OPENROUTER_MODEL`;
- one non-streaming `user` message containing the exact prompt produced by `WorkflowEngine`.

The executor should not add a separate system message or split the prompt. That keeps prompt ownership in `WorkflowEngine` and prevents provider-specific behavior from changing the role contract.

Optional deploy attribution headers:

- `ACADEMIC_ENGINE_OPENROUTER_HTTP_REFERER` -> `HTTP-Referer`;
- `ACADEMIC_ENGINE_OPENROUTER_APP_TITLE` -> `X-OpenRouter-Title`.

If optional header env vars are unset, the executor sends no attribution headers. Secrets must never be written to workflow JSON, request JSON, events, role output, or docs examples.

Official references:

- <https://openrouter.ai/docs/quickstart>
- <https://openrouter.ai/docs/api-reference/overview>
- <https://openrouter.ai/docs/features/app-attribution>

## Configuration

First-slice configuration is env-only:

```bash
OPENROUTER_API_KEY=...
ACADEMIC_ENGINE_OPENROUTER_MODEL=...
ACADEMIC_ENGINE_EVALUATOR_EXECUTOR=openrouter
ACADEMIC_ENGINE_VERIFIER_EXECUTOR=openrouter
```

Optional:

```bash
ACADEMIC_ENGINE_OPENROUTER_HTTP_REFERER=https://your-deploy-domain.example
ACADEMIC_ENGINE_OPENROUTER_APP_TITLE="Academic Engine"
```

There is no hard-coded default model. Missing key or missing model is a configuration error. CLI `--model` remains a Codex/run override and should not replace `ACADEMIC_ENGINE_OPENROUTER_MODEL` in this slice.

## Error Handling

OpenRouter routing is fail-closed.

If a role is explicitly routed to `openrouter`, the executor must not silently fall back to Codex CLI. Fallback is allowed only through the existing route inheritance behavior when a route-specific env var is unset.

Stable blocker codes:

- `provider-config-missing`: missing required env such as key or model.
- `provider-route-forbidden`: `openrouter` selected for the default executor route.
- `provider-auth-failed`: OpenRouter returns authentication or authorization failure, including 401 or 403.
- `provider-http-failed`: network failure, timeout, or non-auth HTTP failure.
- `provider-response-invalid`: response JSON is malformed or lacks usable `choices[0].message.content`.

`WorkflowEngine` should record these codes as runtime blockers. The provider executor should make diagnostics useful without including secrets.

## Timeout And Retry

Use `context.timeout_seconds` as the HTTP request timeout.

The provider executor does not implement internal retry. `WorkflowEngine` already controls role attempts, so one provider call should happen per role attempt. This avoids hidden retry multiplication and keeps execution accounting predictable.

## CLI Smoke

Add an explicit provider smoke command:

```bash
python3 -m academic_engine.work_cli provider-smoke openrouter
```

The smoke command should:

- validate required env;
- require live execution to be explicit through `ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1`;
- make one short non-streaming request;
- print provider id, model id, and safe response metadata;
- never print the API key;
- return a non-zero exit code for config, auth, HTTP, or invalid-response failure.

This command is for operator/deploy verification before running a full workflow.

## Testing

Default tests must not call OpenRouter.

Deterministic tests:

- payload construction uses one `user` message;
- required headers are present and secrets are not exposed in errors;
- optional attribution headers are included only when configured;
- missing key/model returns `provider-config-missing`;
- 401/403 returns `provider-auth-failed`;
- timeout/network/non-auth status returns `provider-http-failed`;
- malformed JSON or missing content returns `provider-response-invalid`;
- `build_executor_router` can select `openrouter` for evaluator/verifier routes;
- `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter` fails closed with `provider-route-forbidden`;
- fake provider response writes raw `message.content` to `output.md`;
- workflow integration still lets `WorkflowEngine` validate `role-result/v1`.

Live smoke is explicit and not part of normal CI:

```bash
OPENROUTER_API_KEY=...
ACADEMIC_ENGINE_OPENROUTER_MODEL=...
ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1 \
python3 -m academic_engine.work_cli provider-smoke openrouter
```

## README Update

Add a short operator section with:

- env variables required for OpenRouter evaluator/verifier routing;
- smoke command;
- warning that default route remains Codex CLI;
- note that live tests are explicit and ordinary CI has no network dependency.

Keep deep architecture details in this spec rather than expanding README.

## Acceptance Criteria

- Current behavior remains Codex CLI by default.
- `openrouter` can be selected for evaluator and verifier routes by env.
- `openrouter` cannot be selected as the default executor in this slice.
- OpenRouter key and model are required env values.
- No provider secrets are serialized or printed.
- Provider failures produce stable machine-readable blocker codes.
- Provider executor writes only raw model content to `output.md`.
- `WorkflowEngine` remains the only authority for `role-result/v1`, write scopes, gates, readiness, repairs, and promotion.
- Ordinary tests use fake transports and make no network calls.
- A manual CLI smoke path exists for deploy/operator verification.
- README contains the minimal operator recipe.

## Follow-Up

The next provider expansion should be a safe file-write bridge for writer/finalizer roles. That design should require structured patches or artifact manifests, engine-side path validation, hash checks where applicable, and explicit promotion through existing gates before API models can write project files.
