# OpenRouter Academic-Intake Qualification Evidence

## Scope

This is the sanitized qualification record for the bounded article-lane
`academic-intake` workflow. It records one qualified `write-plan` route only;
it is not a full-lane production cutover, a default-executor switch, or a
submission-readiness verdict.

## Controlled Qualification Record

- Workflow ID: `openrouter-live-smoke-article-qualify-intake-20260714-122637-c875f174`
- Role: `academic-intake`
- Lane: `article`
- Execution mode: `write-plan`
- Approved model: `deepseek/deepseek-v4-flash`
- Controlled smoke: PASS
- Route policy: PASS
- Qualification controls: PASS
- Secret scan: PASS

Exactly one role executed. Its `execution_status` was `succeeded`,
`write_plan_applied` was `true`, and promotion had `status: skipped` with
`reason: qualification-no-promotion`. The canonical seed fixture was
unchanged.

This requalification ran after the reviewed hardening of both provider
`write-plan` prompt phases: provider prompts do not receive absolute sandbox
paths, while the engine-issued evidence envelope and strict role-result
validation remain enforced.

The generic readiness result is not `submission-ready`. This bounded record
does not assert submission readiness or qualify another role.

## Rollback-Selection Control

Offline rollback-selection controls passed before executor invocation when the
candidate or its policy was removed:

- `tests.test_openrouter_qualification.OpenRouterQualificationTests.test_rejects_unknown_and_removed_candidates_before_executor_invocation`
- `tests.test_openrouter_qualification.OpenRouterQualificationTests.test_rejects_injected_router_with_extra_or_missing_role_maps_before_executor_invocation`

Both controls require `provider-route-forbidden` before the provider executor
is called. They are qualification rollback-selection controls, not the later
production rollback drill.

## Boundaries

An earlier pre-remediation live attempt failed fail-closed and remains
unqualified evidence; it was not used to qualify this route. The default
executor remains `codex-cli`, `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter`
remains fail-closed and unswitched, and every unqualified role remains
forbidden for OpenRouter selection.
