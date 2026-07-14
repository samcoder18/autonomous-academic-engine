# OpenRouter Deploy Runbook

## Purpose

This runbook explains how to enable the existing OpenRouter executor route only for `academic-source-verifier` and `academic-submission-evaluator`, verify it with a live smoke check, diagnose provider failures, and administer the current RC route.

It does not authorize OpenRouter as the default executor. Writer, finalizer, repair, citation, and thesis roles stay on Codex CLI because they need sandbox-aware file-writing behavior or are outside this RC scope.

The versioned [full-transition policy](openrouter-full-transition-policy.md)
and [role qualification matrix](openrouter-role-qualification.md) describe the
approved broader migration. They do not expand the current route, change the
default executor, or make an unqualified role runnable through OpenRouter.

## Hard Boundaries

- Default executor remains `codex-cli`.
- OpenRouter may be routed only to `academic-source-verifier` on the verifier route and `academic-submission-evaluator` on the evaluator route.
- An OpenRouter selection for every other role, including thesis evaluator/verifier roles, fails closed with `provider-route-forbidden`; it never falls back automatically to Codex CLI.
- `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter` is fail-closed while the
  role-policy coverage is incomplete. The current RC has only two read-only
  entries, so the default remains unavailable.
- Live provider calls are never part of ordinary CI or unit tests.
- Provider secrets must not be committed, printed, copied into docs, or serialized into `output/runs/`.
- `WorkflowEngine` remains the authority for prompts, role-result validation, gates, blockers, readiness, repairs, and promotion.

## Full-Transition Control Records

For the broader approved transition, use the two versioned records before
changing a route:

- [OpenRouter Full-Transition Policy](openrouter-full-transition-policy.md)
  defines the authority boundary, execution modes, production rollback, and
  guarded default-switch gate.
- [OpenRouter Role Qualification Matrix](openrouter-role-qualification.md)
  records every article and thesis role, its execution mode, approved model,
  evidence link, rollback action, and qualification status.

The current read-only RC baselines for `academic-source-verifier` and
`academic-submission-evaluator` are the sanitized [2026-07-13 controlled live
workflow smoke](evidence/2026-07-13-openrouter-controlled-live-workflow-smoke.md).
They do not qualify any thesis or write-plan role.

## Guarded Default (Not Active In The Current RC)

The router recognizes `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter` only when
all of the following are true before any executor invocation:

1. `ACADEMIC_ENGINE_OPENROUTER_MODEL` is nonempty;
2. the runtime role-policy map covers every supported article and thesis role;
3. every map entry has `executor_id = openrouter` and a valid `read-only` or
   `write-plan` mode.

Otherwise selection returns `provider-route-forbidden`; it never silently
reroutes to Codex. The guard is implemented ahead of qualification to make the
final switch mechanically constrained, but it is not authorization to set the
default today. The policy remains incomplete until the serial live evidence,
secret scan, full-lane workflows, and rollback drill in the full-transition
policy all pass.

## Environment Contract

| Variable | Required | Secret | Purpose |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | Yes for live smoke and live route | Yes | OpenRouter bearer token. |
| `ACADEMIC_ENGINE_OPENROUTER_MODEL` | Yes for live smoke and live route | No | OpenRouter model slug, for example `provider/model-slug`. |
| `ACADEMIC_ENGINE_EVALUATOR_EXECUTOR` | Yes to route academic submission evaluator through OpenRouter | No | Set to `openrouter` only after smoke passes. |
| `ACADEMIC_ENGINE_VERIFIER_EXECUTOR` | Yes to route academic source verifier through OpenRouter | No | Set to `openrouter` only after smoke passes. |
| `ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST` | Yes only for smoke | No | Set to `1` for `provider-smoke openrouter`; unset after smoke. |
| `ACADEMIC_ENGINE_OPENROUTER_HTTP_REFERER` | Optional | No | Optional OpenRouter app attribution header. |
| `ACADEMIC_ENGINE_OPENROUTER_APP_TITLE` | Optional | No | Optional OpenRouter app title attribution header. |
| `OPS_ALERT_LOG_PATH` | Optional | No | Existing daemon ops-alert tee log path for long-running local operations. |

## Secret Handling

Use shell exports, a local untracked `.env`, or the deployment platform's secret store. Do not commit `.env`.

The repository may contain `.env.example` with empty or redacted values. It must never contain a real OpenRouter key.

If a key is printed to a terminal transcript, committed, or written into `output/runs/`, rotate it before continuing deployment.

## Pre-Deploy Checks

Run from the repository root:

```bash
git status --short --branch
python3 -m unittest discover -s tests -q
```

Expected:

- git status shows no unrelated uncommitted tracked changes;
- unit tests pass without network access.

Choose a model before live verification. Prefer a model that reliably follows fenced `role-result/v1` instructions for `academic-source-verifier` and `academic-submission-evaluator`.

## Live Smoke

Set only the provider secret, model, and explicit live-smoke flag:

```bash
export OPENROUTER_API_KEY="<set-in-shell-or-secret-store>"
export ACADEMIC_ENGINE_OPENROUTER_MODEL="provider/model-slug"
export ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1
python3 -m academic_engine.work_cli provider-smoke openrouter
unset ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST
```

Expected success output shape:

```text
[provider-smoke] provider: openrouter
[provider-smoke] model: provider/model-slug
[provider-smoke] response_chars: <number>
[provider-smoke] preview: provider-smoke-ok
```

The smoke command must not print `OPENROUTER_API_KEY` or the bearer value.

## Enable Academic Verifier And Evaluator Routes

Enable OpenRouter only after the live smoke succeeds:

```bash
export ACADEMIC_ENGINE_EVALUATOR_EXECUTOR=openrouter
export ACADEMIC_ENGINE_VERIFIER_EXECUTOR=openrouter
unset ACADEMIC_ENGINE_DEFAULT_EXECUTOR
```

Do not set `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter`.

## First Workflow Check

Use the dedicated non-critical article bundle first, and keep the target explicit with `--work`.

After the workflow starts, capture the `workflow_id` from CLI output and inspect runtime artifacts:

```bash
python3 -m academic_engine.work_cli work-status --json
python3 -m academic_engine.work_cli runtime-index refresh --json
python3 -m academic_engine.work_cli runtime-index status --json
```

For a specific run, inspect:

```text
output/runs/<workflow_id>/workflow.json
output/runs/<workflow_id>/events.jsonl
output/runs/<workflow_id>/roles/
```

Expected:

- provider route is used only for `academic-source-verifier` and `academic-submission-evaluator`;
- every other executed role uses `default/codex-cli`;
- blockers are machine-readable if the provider fails;
- no secret value appears in workflow JSON, events, role output, stdout, or stderr.

## Controlled Live Workflow Smoke Evidence

Use this only after `provider-smoke openrouter` passes. The controlled smoke uses the dedicated non-default article work `openrouter-live-smoke`; it does not change `starter-work` and it does not authorize OpenRouter for writer, finalizer, default, or thesis routes.

Required environment:

```bash
export OPENROUTER_API_KEY="<set-in-shell-or-secret-store>"
export ACADEMIC_ENGINE_OPENROUTER_MODEL="provider/model-slug"
export ACADEMIC_ENGINE_EVALUATOR_EXECUTOR=openrouter
export ACADEMIC_ENGINE_VERIFIER_EXECUTOR=openrouter
unset ACADEMIC_ENGINE_DEFAULT_EXECUTOR
```

Run the workflow with search disabled:

```bash
python3 -m academic_engine.work_cli launch-academic repair \
  works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md \
  --work openrouter-live-smoke \
  --no-search
```

Capture the returned `workflow_id`, dispatch the queued workflow through the normal job runner, then generate the commit-safe evidence report:

```bash
python3 -m academic_engine.work_cli jobs dispatch --limit 1 --json
python3 -m academic_engine.work_cli runtime-index refresh --json
python3 scripts/openrouter_evidence_report.py \
  --root . \
  --workflow-id "<workflow_id>" \
  --stdout-log "/tmp/openrouter-live-smoke.stdout.log" \
  --stderr-log "/tmp/openrouter-live-smoke.stderr.log" \
  --report docs/deploy/evidence/2026-07-11-openrouter-controlled-live-workflow-smoke.md
```

Expected evidence:

- `academic-source-verifier` uses `verifier/openrouter`;
- `academic-submission-evaluator` uses `evaluator/openrouter`;
- writer, repair, citation, and finalizer roles use `default/codex-cli` or do not run;
- the workflow is the `openrouter-live-smoke` article repair for the fixed draft target with `--no-search` and `execution_status: succeeded`;
- readiness may be `strong-draft-with-blockers`;
- the evidence report says `Controlled smoke: PASS`;
- the evidence report says `Route policy: PASS`;
- the evidence report says `Secret scan: PASS`.

Do not commit raw `output/runs/<workflow_id>/` artifacts. Commit only the sanitized Markdown evidence report under `docs/deploy/evidence/`.

## Bounded Academic-Intake Qualification Harness (Not Production)

The first write-plan candidate, `academic-intake`, has a separate bounded
qualification harness. It is not `launch-academic`, does not enqueue or
dispatch jobs, and does not run the normal article lane. The only supported
direct command is:

```bash
python3 -m academic_engine.work_cli qualify-openrouter-role academic-intake \
  --work openrouter-live-smoke \
  --seed works/openrouter-live-smoke/articles/briefs/academic-intake-qualification.md \
  --no-search
```

Run it only after the harness's offline tests and code review pass. It has one
OpenRouter `write-plan` role, writes only inside its sandbox, and is successful
only when the workflow reports `status: completed` and
`execution_status: succeeded`. Its promotion manifest must be
`status: skipped` with `reason: qualification-no-promotion`, and the canonical
seed fixture's before/after SHA-256 values must match with
`canonical_unchanged: true`.

The direct harness has no runtime request/job record. Generate its sanitized
evidence with the qualification mode, then require all four lines to pass:

```bash
python3 scripts/openrouter_evidence_report.py \
  --root . \
  --workflow-id "<workflow_id>" \
  --stdout-log "/tmp/openrouter-intake-qualification.stdout.log" \
  --stderr-log "/tmp/openrouter-intake-qualification.stderr.log" \
  --qualification-role academic-intake \
  --report /tmp/openrouter-intake-qualification-evidence.md
```

```text
Controlled smoke: PASS
Route policy: PASS
Qualification controls: PASS
Secret scan: PASS
```

Do not commit raw `output/runs/<workflow_id>/` artifacts, raw provider output,
write plans, or terminal logs. The generic evaluator gate may leave the
workflow at `strong-draft-with-blockers`; that does not invalidate this bounded
qualification evidence and must never be described as submission-ready.

As an offline rollback-selection control, remove the qualification candidate
policy and verify that selecting `academic-intake` returns
`provider-route-forbidden` before any executor invocation. This is not the
later production rollback drill. A harness pass does not authorize a
production allowlist change, model approval, default switch, or the next role.

## Diagnostics Matrix

| Code | Likely Cause | Operator Action | Rollback Needed |
| --- | --- | --- | --- |
| `provider-config-missing` | Missing key, missing model, or missing explicit live-smoke flag. | Set `OPENROUTER_API_KEY`, set `ACADEMIC_ENGINE_OPENROUTER_MODEL`, or set `ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1` for smoke only. Rerun smoke. | No, unless a workflow is blocked and should continue with Codex. |
| `provider-auth-failed` | OpenRouter rejected the key or account access. | Rotate or replace the key, check account/billing/model access, rerun smoke. | Yes for active workflow rollout: unset evaluator/verifier route env. |
| `provider-http-failed` | Timeout, network error, OpenRouter 5xx, or non-auth HTTP failure. | Check local network/proxy, OpenRouter status, model availability, and rerun smoke. | Yes if a production workflow is waiting on provider recovery. |
| `provider-response-invalid` | OpenRouter response JSON is malformed, empty, or lacks `choices[0].message.content`. | Retry smoke once, then switch model if repeated. If workflow output exists but role-result validation fails, treat it as model contract failure. | Yes if repeated for the selected model. |
| `provider-route-forbidden` | OpenRouter was selected for default, thesis, or another non-academic route. | Unset `ACADEMIC_ENGINE_DEFAULT_EXECUTOR`; keep OpenRouter route env only for the dedicated academic controlled smoke. | No after env is corrected. |
| `role-result-schema-invalid` | Provider returned text but not a valid `role-result/v1` payload. | Switch to a model that follows the role-result contract or roll back evaluator/verifier routes to Codex CLI. | Yes if repeated for the selected model. |

## Observability Surfaces

Start with the command that failed:

- smoke failure: stderr from `provider-smoke openrouter`;
- workflow failure: `output/runs/<workflow_id>/workflow.json`;
- role-level failure: role output and role result under `output/runs/<workflow_id>/roles/`;
- current work summary: `python3 -m academic_engine.work_cli work-status --json`;
- indexed runtime summary: `runtime-index refresh --json` followed by `runtime-index status --json`;
- daemon operations: stderr/logging and optional `OPS_ALERT_LOG_PATH`.

Provider diagnostics must stay safe to paste into issue reports. Include blocker code, model slug, route name, workflow id, and role id. Do not include `OPENROUTER_API_KEY`.

## Rollback

### Current RC Administrative Rollback

Roll back provider routing by unsetting route-specific env:

```bash
unset ACADEMIC_ENGINE_EVALUATOR_EXECUTOR
unset ACADEMIC_ENGINE_VERIFIER_EXECUTOR
unset ACADEMIC_ENGINE_DEFAULT_EXECUTOR
```

In the current RC implementation, unsetting these two explicit routes returns
their execution to the existing `codex-cli` default behavior. This is an RC
administrative action only; it is not the production rollback contract for the
full transition.

### Full-Transition Production Rollback (Target Policy)

After a role has been qualified under the full-transition policy, remove that
affected `role_id` from the OpenRouter policy allowlist and record the removal.
The next OpenRouter selection for the role must fail closed with
`provider-route-forbidden` before invoking an executor. It must never
automatically reroute to `codex-cli`, and a partial result must never be
promoted. See [OpenRouter Full-Transition
Policy](openrouter-full-transition-policy.md#production-rollback) for the
required drill and evidence.

## Rollout Policy

Use this order:

1. Local deterministic tests pass without network.
2. `provider-smoke openrouter` passes with explicit live flag.
3. One non-critical `openrouter-live-smoke` article repair is run with the two academic OpenRouter routes enabled.
4. Runtime artifacts are inspected for blocker clarity and secret absence.
5. Only then use OpenRouter routes on normal `academic-source-verifier` and `academic-submission-evaluator` workflows.

Do not expand OpenRouter to thesis, writer, finalizer, or default executor
routes until the relevant row is qualified and the policy/runtime change is
approved and implemented. The guarded default remains forbidden until the
[full-transition default-switch gate](openrouter-full-transition-policy.md#guarded-default-switch-gate)
passes.
