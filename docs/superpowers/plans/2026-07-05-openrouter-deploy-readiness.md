# OpenRouter Deploy Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an operator-facing OpenRouter deploy runbook, diagnostics contract, environment template, and README handoff without changing runtime provider behavior.

**Architecture:** Keep the existing `openrouter` executor and `WorkflowEngine` authority unchanged. This slice documents the deployment boundary around that code: env/secrets, live smoke, diagnostics, rollback, observability surfaces, and provider rollout policy. `.env.example` is a local template only; secrets remain outside git and outside runtime artifacts.

**Tech Stack:** Markdown, shell commands, existing `python3 -m academic_engine.work_cli` CLI, existing runtime artifacts under `output/`, no new Python code, no new dependencies, no live network in automated verification.

---

## File Structure

- Create `docs/deploy/openrouter-runbook.md`
  - The single operator runbook for OpenRouter deploy readiness, live smoke, diagnostics, rollback, and rollout policy.
- Create `.env.example`
  - A redacted local template for OpenRouter provider env and optional deploy attribution.
- Modify `README.md`
  - Keep the short OpenRouter recipe, but link to the runbook and `.env.example` so README does not become the full deploy manual.

This plan is intentionally docs/template only. It must not change `academic_engine/executors.py`, `academic_engine/work_cli.py`, `academic_engine/workflow_engine.py`, or any tests unless a later implementation plan explicitly expands the scope.

---

### Task 1: Create OpenRouter Deploy Runbook

**Files:**
- Create: `docs/deploy/openrouter-runbook.md`

- [ ] **Step 1: Add the runbook file**

Use `apply_patch` to create `docs/deploy/openrouter-runbook.md` with this exact content:

````markdown
# OpenRouter Deploy Runbook

## Purpose

This runbook explains how to enable the existing OpenRouter executor route for evaluator and verifier roles, verify it with a live smoke check, diagnose provider failures, and roll back to the Codex CLI default route.

It does not authorize OpenRouter as the default executor. Writer and finalizer roles still require Codex CLI because they need sandbox-aware file-writing behavior.

## Hard Boundaries

- Default executor remains `codex-cli`.
- OpenRouter may be routed only to evaluator and verifier roles.
- `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter` is forbidden in this slice.
- Live provider calls are never part of ordinary CI or unit tests.
- Provider secrets must not be committed, printed, copied into docs, or serialized into `output/runs/`.
- `WorkflowEngine` remains the authority for prompts, role-result validation, gates, blockers, readiness, repairs, and promotion.

## Environment Contract

| Variable | Required | Secret | Purpose |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | Yes for live smoke and live route | Yes | OpenRouter bearer token. |
| `ACADEMIC_ENGINE_OPENROUTER_MODEL` | Yes for live smoke and live route | No | OpenRouter model slug, for example `provider/model-slug`. |
| `ACADEMIC_ENGINE_EVALUATOR_EXECUTOR` | Yes to route evaluator through OpenRouter | No | Set to `openrouter` only after smoke passes. |
| `ACADEMIC_ENGINE_VERIFIER_EXECUTOR` | Yes to route verifier through OpenRouter | No | Set to `openrouter` only after smoke passes. |
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

Choose a model before live verification. Prefer a model that reliably follows fenced `role-result/v1` instructions for evaluator and verifier roles.

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

## Enable Evaluator And Verifier Routes

Enable OpenRouter only after the live smoke succeeds:

```bash
export ACADEMIC_ENGINE_EVALUATOR_EXECUTOR=openrouter
export ACADEMIC_ENGINE_VERIFIER_EXECUTOR=openrouter
unset ACADEMIC_ENGINE_DEFAULT_EXECUTOR
```

Do not set `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter`.

## First Workflow Check

Use a non-critical work bundle first. Prefer an article or thesis workflow where evaluator or verifier roles are expected to run, and keep the target explicit with `--work`.

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

- provider route is used only for evaluator and verifier roles;
- blockers are machine-readable if the provider fails;
- no secret value appears in workflow JSON, events, role output, stdout, or stderr.

## Diagnostics Matrix

| Code | Likely Cause | Operator Action | Rollback Needed |
| --- | --- | --- | --- |
| `provider-config-missing` | Missing key, missing model, or missing explicit live-smoke flag. | Set `OPENROUTER_API_KEY`, set `ACADEMIC_ENGINE_OPENROUTER_MODEL`, or set `ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1` for smoke only. Rerun smoke. | No, unless a workflow is blocked and should continue with Codex. |
| `provider-auth-failed` | OpenRouter rejected the key or account access. | Rotate or replace the key, check account/billing/model access, rerun smoke. | Yes for active workflow rollout: unset evaluator/verifier route env. |
| `provider-http-failed` | Timeout, network error, OpenRouter 5xx, or non-auth HTTP failure. | Check local network/proxy, OpenRouter status, model availability, and rerun smoke. | Yes if a production workflow is waiting on provider recovery. |
| `provider-response-invalid` | OpenRouter response JSON is malformed, empty, or lacks `choices[0].message.content`. | Retry smoke once, then switch model if repeated. If workflow output exists but role-result validation fails, treat it as model contract failure. | Yes if repeated for the selected model. |
| `provider-route-forbidden` | OpenRouter was selected for the default executor route. | Unset `ACADEMIC_ENGINE_DEFAULT_EXECUTOR`; keep only evaluator/verifier route env. | No after env is corrected. |
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

Roll back provider routing by unsetting route-specific env:

```bash
unset ACADEMIC_ENGINE_EVALUATOR_EXECUTOR
unset ACADEMIC_ENGINE_VERIFIER_EXECUTOR
unset ACADEMIC_ENGINE_DEFAULT_EXECUTOR
```

Then rerun the relevant command. With no explicit executor env, the router returns to Codex CLI default behavior.

## Rollout Policy

Use this order:

1. Local deterministic tests pass without network.
2. `provider-smoke openrouter` passes with explicit live flag.
3. One non-critical evaluator/verifier workflow is run with OpenRouter routes enabled.
4. Runtime artifacts are inspected for blocker clarity and secret absence.
5. Only then use OpenRouter routes on normal evaluator/verifier workflows.

Do not expand OpenRouter to writer, finalizer, or default executor routes until a separate safe file-write bridge design is approved and implemented.
````

- [ ] **Step 2: Inspect the runbook excerpt**

Run:

```bash
sed -n '1,260p' docs/deploy/openrouter-runbook.md
```

Expected:

- the file exists;
- it contains the env contract, smoke sequence, diagnostics matrix, observability surfaces, rollback, and rollout policy;
- it contains no real API key.

- [ ] **Step 3: Commit the runbook**

Run:

```bash
git add docs/deploy/openrouter-runbook.md
git commit -m "docs: add openrouter deploy runbook"
```

Expected: commit succeeds with only the runbook staged.

---

### Task 2: Add Redacted Environment Template

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Add `.env.example`**

Use `apply_patch` to create `.env.example` with this exact content:

```dotenv
# Autonomous Academic Engine local environment template.
# Copy values into your shell, local .env loader, or deploy secret store.
# Do not commit a real .env file and do not paste real secrets into docs.

# OpenRouter provider route.
# Required for live provider smoke and live evaluator/verifier routing.
OPENROUTER_API_KEY=
ACADEMIC_ENGINE_OPENROUTER_MODEL=

# Enable only after `provider-smoke openrouter` passes.
# ACADEMIC_ENGINE_EVALUATOR_EXECUTOR=openrouter
# ACADEMIC_ENGINE_VERIFIER_EXECUTOR=openrouter

# Do not set ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter in this slice.

# Optional OpenRouter app attribution headers.
# ACADEMIC_ENGINE_OPENROUTER_HTTP_REFERER=https://your-deploy-domain.example
# ACADEMIC_ENGINE_OPENROUTER_APP_TITLE="Academic Engine"

# Explicit live-smoke opt-in. Set only for the smoke command, then unset.
# ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1

# Optional daemon/ops alert tee log path for long-running local operations.
# OPS_ALERT_LOG_PATH=output/runtime/ops-alerts.jsonl
```

- [ ] **Step 2: Verify the template has no real secret**

Run:

```bash
rg -n "sk-or-v1-|Bearer |OPENROUTER_API_KEY=\\S+" .env.example
```

Expected: no matches.

- [ ] **Step 3: Commit the environment template**

Run:

```bash
git add .env.example
git commit -m "docs: add openrouter env template"
```

Expected: commit succeeds with only `.env.example` staged.

---

### Task 3: Update README Handoff

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the OpenRouter README section**

In `README.md`, replace the existing `### OpenRouter provider route` section with this exact section:

````markdown
### OpenRouter provider route

Codex CLI remains the default executor. OpenRouter can be enabled only for evaluator and verifier roles in this slice:

```bash
export OPENROUTER_API_KEY="sk-or-v1-redacted"
export ACADEMIC_ENGINE_OPENROUTER_MODEL="provider/model-slug"
export ACADEMIC_ENGINE_EVALUATOR_EXECUTOR=openrouter
export ACADEMIC_ENGINE_VERIFIER_EXECUTOR=openrouter
```

Optional deploy attribution:

```bash
export ACADEMIC_ENGINE_OPENROUTER_HTTP_REFERER="https://your-deploy-domain.example"
export ACADEMIC_ENGINE_OPENROUTER_APP_TITLE="Academic Engine"
```

Run an explicit live smoke check before using the route:

```bash
export ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1
python3 -m academic_engine.work_cli provider-smoke openrouter
unset ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST
```

Ordinary CI and unit tests do not call OpenRouter. `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter` is intentionally rejected until a safe file-write bridge exists for writer/finalizer roles.

Deploy runbook and diagnostics matrix: [docs/deploy/openrouter-runbook.md](docs/deploy/openrouter-runbook.md). Redacted local env template: [.env.example](.env.example).
````

- [ ] **Step 2: Inspect the updated README section**

Run:

```bash
sed -n '/### OpenRouter provider route/,/## Launcher \\/ CLI/p' README.md
```

Expected:

- the section links to `docs/deploy/openrouter-runbook.md`;
- the section links to `.env.example`;
- the smoke command unsets `ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST`;
- no real API key appears.

- [ ] **Step 3: Commit the README handoff**

Run:

```bash
git add README.md
git commit -m "docs: link openrouter deploy runbook"
```

Expected: commit succeeds with only `README.md` staged.

---

### Task 4: Documentation Verification

**Files:**
- Verify: `docs/deploy/openrouter-runbook.md`
- Verify: `.env.example`
- Verify: `README.md`

- [ ] **Step 1: Check links and required references**

Run:

```bash
test -f docs/deploy/openrouter-runbook.md
test -f .env.example
rg -n "openrouter-runbook.md|\\.env.example|provider-smoke openrouter|provider-config-missing|provider-auth-failed|provider-http-failed|provider-response-invalid|provider-route-forbidden" README.md docs/deploy/openrouter-runbook.md .env.example
```

Expected:

- both files exist;
- `README.md` references the runbook and env template;
- the runbook contains the smoke command and all provider blocker codes.

- [ ] **Step 2: Check for accidental secrets**

Run:

```bash
rg -n "sk-or-v1-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._-]{20,}|OPENROUTER_API_KEY=(sk-or-v1-[A-Za-z0-9_-]{20,}|[A-Za-z0-9._-]{20,})" README.md docs/deploy/openrouter-runbook.md .env.example
```

Expected: no matches. The literal redacted example `sk-or-v1-redacted` is acceptable because it is not a real-looking key.

- [ ] **Step 3: Check Markdown and whitespace**

Run:

```bash
git diff --check
```

Expected: no trailing whitespace or patch formatting warnings.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git status --short
git diff --stat HEAD~3..HEAD
```

Expected:

- working tree is clean if Tasks 1-3 were committed;
- final diff includes only `docs/deploy/openrouter-runbook.md`, `.env.example`, and `README.md`.

---

## Implementation Notes

- Do not run a live OpenRouter request while implementing this docs/template slice.
- Do not add OpenRouter to CI.
- Do not add a new dependency or SDK.
- Do not change executor routing behavior.
- Do not change blocker mapping.
- Do not document OpenRouter as safe for writer, finalizer, or default executor routes.
- Keep `.env.example` empty or commented for secret values.
