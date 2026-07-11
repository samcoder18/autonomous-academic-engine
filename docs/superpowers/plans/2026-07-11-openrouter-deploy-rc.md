# OpenRouter Deploy RC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a repeatable, fail-closed RC for OpenRouter on exactly the academic verifier and submission-evaluator routes, backed by one normal queued workflow and a sanitized evidence report.

**Architecture:** `WorkflowEngine` remains the authority for prompt construction, `role-result/v1` validation, gates, repairs, and promotion. `ExecutorRouter` enforces the narrow transport allowlist without fallback, while the evidence script validates the exact controlled-smoke identity, role routes, executor identities, execution result, and secret-free artifacts.

**Tech Stack:** Python 3.11+ standard library, `unittest`, existing CLI/job runner/runtime index, Git.

## Global Constraints

- Start from `main@fffbdae1` in an isolated branch.
- OpenRouter is allowed only for `academic-source-verifier` and `academic-submission-evaluator`.
- `codex-cli` remains the default executor; repair, citation, writer, finalizer, and thesis roles must not fall back automatically or reach OpenRouter.
- Preserve strict `role-result/v1`; only prompt/context changes are permitted if the real smoke later exposes a model-contract error.
- Do not add a wrapper CLI, safe write bridge, provider default, new HTTP dependency, or live network test to CI.
- Never commit `.env`, secrets, raw stdout/stderr, or `output/runs/<workflow_id>/`.

---

### Task 1: Fail Closed on Non-Academic OpenRouter Routes

**Files:**
- Modify: `academic_engine/executors.py`
- Test: `tests/test_executors.py`

**Interfaces:**
- Produces `OPENROUTER_ALLOWED_ROLE_ROUTES: dict[str, str]` with exactly `academic-source-verifier: verifier` and `academic-submission-evaluator: evaluator`.
- `ExecutorRouter.execute()` raises `ProviderExecutionError` with `blocker_code == "provider-route-forbidden"` before invoking an OpenRouter executor for every other role ID.

- [ ] **Step 1: Write failing route-boundary tests**

Add tests that construct an `ExecutorRouter` with `evaluator_executor_id="openrouter"` or `verifier_executor_id="openrouter"` and `RecordingExecutor` instances. Verify academic roles execute the selected route, while `thesis-submission-evaluator` and `thesis-source-verifier` raise `ProviderExecutionError`, keep `blocker_code == "provider-route-forbidden"`, and invoke neither the default nor OpenRouter recording executor.

- [ ] **Step 2: Run the focused tests and observe RED**

Run:

```bash
python3 -m unittest tests.test_executors.ExecutorTests.test_openrouter_evaluator_route_rejects_thesis_role_without_fallback tests.test_executors.ExecutorTests.test_openrouter_verifier_route_rejects_thesis_role_without_fallback -v
```

Expected: both tests fail because the current router routes any evaluator/verifier role with an OpenRouter-selected executor.

- [ ] **Step 3: Add the minimal router policy**

In `academic_engine/executors.py`, define:

```python
OPENROUTER_ALLOWED_ROLE_ROUTES = {
    "academic-source-verifier": "verifier",
    "academic-submission-evaluator": "evaluator",
}
```

In `ExecutorRouter._select()`, derive the normal `ExecutorSelection`, then return `ForbiddenProviderRouteExecutor("openrouter", selection.route_name)` when the selected executor ID is `openrouter` and `OPENROUTER_ALLOWED_ROLE_ROUTES.get(context.role_id) != selection.route_name`. Otherwise retain existing evaluator, verifier, and default selection logic.

- [ ] **Step 4: Run focused tests and provider/router regression tests**

Run:

```bash
python3 -m unittest tests.test_executors -q
```

Expected: PASS with no network activity.

- [ ] **Step 5: Commit the router policy**

```bash
git add academic_engine/executors.py tests/test_executors.py
git commit -m "fix: restrict openrouter to academic review roles"
```

### Task 2: Make the Evidence Report an RC Contract

**Files:**
- Modify: `scripts/openrouter_evidence_report.py`
- Test: `tests/test_openrouter_evidence_report.py`

**Interfaces:**
- Consumes `OPENROUTER_ALLOWED_ROLE_ROUTES` from `academic_engine.executors`.
- Requires the runtime request at `output/runtime/runs/<workflow_id>/request.json` and validates `work_id`, `lane`, `action`, target path, and `search_override is False`.
- Produces a nonzero exit if the controlled smoke is wrong, either required academic role is absent/not succeeded/not on its required OpenRouter route, or any observed non-provider role is not `default/codex-cli`.

- [ ] **Step 1: Write failing evidence-contract tests**

Extend the test fixture to create `output/runtime/runs/<workflow_id>/request.json` with the dedicated work, article repair action, smoke draft target, and `search_override: false`. Add tests for:

```python
# Expected provider role used codex-cli instead of OpenRouter.
# Thesis role uses OpenRouter.
# Citation checker uses default/stub-api.
# Runtime request has search_override=True.
```

Each test must expect exit code `1` and a report containing a specific controlled-smoke or route-policy violation.

- [ ] **Step 2: Run the focused report tests and observe RED**

Run:

```bash
python3 -m unittest tests.test_openrouter_evidence_report -q
```

Expected: the new tests fail because the report currently checks only unexpected OpenRouter use.

- [ ] **Step 3: Implement the smallest complete evidence policy**

Add constants for the controlled smoke identity:

```python
CONTROLLED_SMOKE_WORK_ID = "openrouter-live-smoke"
CONTROLLED_SMOKE_LANE = "article"
CONTROLLED_SMOKE_ACTION = "repair"
CONTROLLED_SMOKE_TARGET = "works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md"
```

Read the runtime request, validate identity and `search_override is False`, and require `workflow["execution_status"] == "succeeded"`. Make `route_policy_violations()` require every mapping in `OPENROUTER_ALLOWED_ROLE_ROUTES`, including status `succeeded`, route match, and executor `openrouter`; require every other observed role to use route `default` and executor `codex-cli`. Extend `_scan_paths()` to include `output/runtime/runs/<workflow_id>`.

Render `Controlled smoke: PASS|FAIL` before the existing route and secret results, include all violations under Findings, and return nonzero when any controlled-smoke, route, or secret check fails.

- [ ] **Step 4: Run focused evidence tests and static checks**

Run:

```bash
python3 -m unittest tests.test_openrouter_evidence_report -q
python3 -m py_compile scripts/openrouter_evidence_report.py
```

Expected: PASS; all tests use synthetic data and no live request.

- [ ] **Step 5: Commit the evidence contract**

```bash
git add scripts/openrouter_evidence_report.py tests/test_openrouter_evidence_report.py
git commit -m "fix: enforce controlled openrouter smoke evidence"
```

### Task 3: Align Operator Documentation

**Files:**
- Modify: `docs/deploy/openrouter-runbook.md`
- Modify: `README.md`

**Interfaces:**
- Documents the exact two role IDs, no automatic fallback, and the three evidence pass lines: controlled smoke, route policy, and secret scan.

- [ ] **Step 1: Update the route wording**

Replace generic evaluator/verifier wording with `academic-source-verifier` and `academic-submission-evaluator`. State that thesis evaluator/verifier roles remain on Codex CLI and an explicit non-academic OpenRouter route fails closed with `provider-route-forbidden`.

- [ ] **Step 2: Update the evidence expectations**

In the runbook controlled smoke section, add that the report verifies `openrouter-live-smoke`, article repair, the fixed draft target, `--no-search`, successful workflow execution, the two required OpenRouter role traces, and `default/codex-cli` for all other executed roles.

- [ ] **Step 3: Check documentation diff**

Run:

```bash
git diff --check
git diff -- docs/deploy/openrouter-runbook.md README.md
```

Expected: no whitespace errors; no secret-like value introduced.

- [ ] **Step 4: Commit the documentation**

```bash
git add docs/deploy/openrouter-runbook.md README.md
git commit -m "docs: narrow openrouter RC routes"
```

### Task 4: Execute the Normal Controlled Smoke and Publish Evidence

**Files:**
- Create: `docs/deploy/evidence/2026-07-11-openrouter-controlled-live-workflow-smoke.md`

**Interfaces:**
- Uses existing `provider-smoke`, `launch-academic`, `jobs dispatch`, `runtime-index`, and evidence-report commands; no new wrapper.

- [ ] **Step 1: Verify local preconditions without printing secrets**

Confirm `OPENROUTER_API_KEY` and `ACADEMIC_ENGINE_OPENROUTER_MODEL` are nonempty without echoing values, ensure `ACADEMIC_ENGINE_DEFAULT_EXECUTOR` is unset, and refresh the runtime index once to reconcile any terminal prior launcher record.

- [ ] **Step 2: Run the live provider smoke**

Set `ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1`, run:

```bash
python3 -m academic_engine.work_cli provider-smoke openrouter
```

Unset the live-test flag immediately after a successful result. Capture only redacted operator output if needed.

- [ ] **Step 3: Run the normal queued workflow**

Set only the evaluator and verifier executor environment variables to `openrouter`, leave default unset, then run:

```bash
python3 -m academic_engine.work_cli launch-academic repair works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md --work openrouter-live-smoke --no-search
python3 -m academic_engine.work_cli jobs dispatch --limit 1 --json
python3 -m academic_engine.work_cli runtime-index refresh --json
```

Capture the returned workflow ID and pass temporary stdout/stderr paths to the evidence script. Do not use direct `WorkflowEngine` invocation or a deterministic default executor.

- [ ] **Step 4: Generate and inspect sanitized evidence**

Run the evidence script with the workflow ID and write the dated Markdown report. It must show:

```text
Controlled smoke: PASS
Route policy: PASS
Secret scan: PASS
```

The report may show `strong-draft-with-blockers`; it must not claim `submission-ready` solely from the smoke.

- [ ] **Step 5: Run final verification and secret scan**

Run:

```bash
python3 -m unittest discover -s tests -q
git diff --check
git status --short
```

Run the repository secret scan defined in the deploy runbook against staged content and review the report plus `git diff --cached` before staging generated-safe evidence only.

- [ ] **Step 6: Commit and push the RC evidence**

```bash
git add docs/deploy/evidence/2026-07-11-openrouter-controlled-live-workflow-smoke.md
git commit -m "docs: record openrouter deploy RC evidence"
git push -u origin codex/openrouter-deploy-rc
```

Expected: no `.env`, raw log, raw runtime directory, or secret enters the commit.

## Plan Self-Review

- Scope coverage: route restriction, evidence correctness, docs, normal smoke, testing, secret handling, commit, and push each have a task.
- No schema relaxation: no task edits `academic_engine/role_result_contract.py`.
- No wrapper or safe write bridge: the plan calls existing CLI surfaces only.
- RC completion is limited to the two named academic roles; later scope expansion requires a new design gate.
