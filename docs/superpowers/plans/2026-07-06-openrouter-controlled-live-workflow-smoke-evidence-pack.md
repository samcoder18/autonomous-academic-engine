# OpenRouter Controlled Live Workflow Smoke Evidence Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a controlled live OpenRouter workflow smoke path and commit-safe evidence pack proving that OpenRouter is used only for evaluator/verifier routes and that no provider secret leaks into repo or runtime artifacts.

**Architecture:** Keep `WorkflowEngine` authoritative and `codex-cli` as the default executor. Add a non-secret executor route trace to runtime role artifacts, add a small evidence-report generator that inspects one workflow run and leak-scans selected artifacts, then run one isolated article repair smoke on a dedicated non-default work bundle. The live provider call remains manual/opt-in and is never part of ordinary tests or CI.

**Tech Stack:** Python standard library, existing `academic_engine` CLI, existing `WorkflowEngine` runtime artifacts under `output/runs/`, Markdown docs, no new dependencies.

---

## File Structure

- Modify `academic_engine/executors.py`
  - Add `ExecutorSelection`.
  - Teach `ExecutorRouter` to describe selected route and executor id without secrets.
  - Preserve existing `execute(context, prompt)` behavior.
- Modify `academic_engine/workflow_engine.py`
  - Add `executor_route` and `executor_id` to `RoleRun`.
  - Record route trace before role execution.
  - Persist the trace in `workflow.json` and `roles/*/result.json`.
- Modify `tests/test_executors.py`
  - Cover route selection descriptions for default, evaluator, and verifier.
- Modify `tests/test_workflow_engine.py`
  - Cover persisted route trace in workflow role results.
- Create `scripts/openrouter_evidence_report.py`
  - Dependency-free evidence report generator and secret scanner.
- Create `tests/test_openrouter_evidence_report.py`
  - Deterministic tests for report generation, route policy checks, and exact secret detection.
- Modify `docs/deploy/openrouter-runbook.md`
  - Add a controlled live workflow smoke section that points to the evidence report script.
- Modify `README.md`
  - Add one short handoff line under the OpenRouter provider route section.
- Create `docs/deploy/evidence/README.md`
  - Explain which evidence reports may be committed and which raw runtime artifacts stay local.
- Create and commit a dedicated non-default smoke work:
  - `works/openrouter-live-smoke/work.toml`
  - `works/openrouter-live-smoke/work-canon.md`
  - `works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md`
  - Register it in `workspace.toml` without changing `default_work`.
- During the manual live task only, generate:
  - `docs/deploy/evidence/2026-07-06-openrouter-controlled-live-workflow-smoke.md`

Do not commit `.env`, shell history, captured stdout/stderr logs, or `output/runs/<workflow_id>/`.

---

### Task 1: Persist Non-Secret Executor Route Trace

**Files:**
- Modify: `academic_engine/executors.py`
- Modify: `academic_engine/workflow_engine.py`
- Test: `tests/test_executors.py`
- Test: `tests/test_workflow_engine.py`

- [ ] **Step 1: Add failing router trace test**

In `tests/test_executors.py`, add this test method to `ExecutorTests` after `test_router_routes_verifier_independently`:

```python
    def test_router_describes_selected_executor_route(self) -> None:
        default = RecordingExecutor("default")
        evaluator = RecordingExecutor("evaluator")
        verifier = RecordingExecutor("verifier")
        router = ExecutorRouter(
            default_executor=default,
            evaluator_executor=evaluator,
            verifier_executor=verifier,
            default_executor_id="codex-cli",
            evaluator_executor_id="openrouter",
            verifier_executor_id="openrouter",
        )

        self.assertEqual(
            router.describe_selection(self.context()).to_dict(),
            {"route_name": "default", "executor_id": "codex-cli"},
        )
        self.assertEqual(
            router.describe_selection(
                self.context("academic-submission-evaluator", is_evaluator=True)
            ).to_dict(),
            {"route_name": "evaluator", "executor_id": "openrouter"},
        )
        self.assertEqual(
            router.describe_selection(
                self.context("academic-source-verifier", is_verifier=True)
            ).to_dict(),
            {"route_name": "verifier", "executor_id": "openrouter"},
        )
```

- [ ] **Step 2: Run the focused failing test**

Run:

```bash
python3 -m pytest tests/test_executors.py::ExecutorTests::test_router_describes_selected_executor_route -q
```

Expected: FAIL because `ExecutorRouter` does not yet accept executor id fields and has no `describe_selection()`.

- [ ] **Step 3: Implement executor selection tracing**

In `academic_engine/executors.py`, add this dataclass after `ProviderSmokeResult`:

```python
@dataclass(frozen=True)
class ExecutorSelection:
    route_name: str
    executor_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "route_name": self.route_name,
            "executor_id": self.executor_id,
        }
```

Replace the existing `ExecutorRouter` dataclass with:

```python
@dataclass(frozen=True)
class ExecutorRouter:
    default_executor: RoleExecutorProtocol
    evaluator_executor: RoleExecutorProtocol | None = None
    verifier_executor: RoleExecutorProtocol | None = None
    default_executor_id: str = "custom"
    evaluator_executor_id: str | None = None
    verifier_executor_id: str | None = None

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        executor = self._select(context)
        executor.execute(context, prompt)

    def describe_selection(self, context: RoleExecutionContext) -> ExecutorSelection:
        if context.is_evaluator and self.evaluator_executor is not None:
            return ExecutorSelection("evaluator", self.evaluator_executor_id or "custom")
        if context.is_verifier and self.verifier_executor is not None:
            return ExecutorSelection("verifier", self.verifier_executor_id or "custom")
        return ExecutorSelection("default", self.default_executor_id)

    def _select(self, context: RoleExecutionContext) -> RoleExecutorProtocol:
        if context.is_evaluator and self.evaluator_executor is not None:
            return self.evaluator_executor
        if context.is_verifier and self.verifier_executor is not None:
            return self.verifier_executor
        return self.default_executor
```

Update `build_executor_router()` to pass executor ids:

```python
    return ExecutorRouter(
        default_executor=_executor_for(default_id, available, route_name="default"),
        evaluator_executor=_executor_for(evaluator_id, available, route_name="evaluator") if evaluator_id else None,
        verifier_executor=_executor_for(verifier_id, available, route_name="verifier") if verifier_id else None,
        default_executor_id=default_id,
        evaluator_executor_id=evaluator_id,
        verifier_executor_id=verifier_id,
    )
```

- [ ] **Step 4: Verify router trace test passes**

Run:

```bash
python3 -m pytest tests/test_executors.py::ExecutorTests::test_router_describes_selected_executor_route -q
```

Expected: PASS.

- [ ] **Step 5: Add failing workflow persistence test**

In `tests/test_workflow_engine.py`, update the import from `academic_engine.executors` to include `CallableRoleExecutor` and `ExecutorRouter`:

```python
from academic_engine.executors import (
    CallableRoleExecutor,
    ExecutorRouter,
    ExecutorUnavailableError,
    ProviderExecutionError,
    RoleExecutionContext,
)
```

Add this test method to `WorkflowEngineTests` after `test_executor_router_receives_trusted_role_context`:

```python
    def test_workflow_persists_executor_route_trace(self) -> None:
        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            if "Role ID: thesis-style-editor" in prompt:
                path = sandbox / self.target.relative_to(self.root)
                path.write_text("# Updated through traced router\n", encoding="utf-8")
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=_evaluator_payload("submission-ready")
                if "Role ID: thesis-submission-evaluator" in prompt
                else None,
            )

        router = ExecutorRouter(
            default_executor=CallableRoleExecutor(executor),
            evaluator_executor=CallableRoleExecutor(executor),
            default_executor_id="codex-cli",
            evaluator_executor_id="openrouter",
        )

        result = WorkflowEngine(self.root, executor_router=router).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(result.role_runs[0].executor_route, "default")
        self.assertEqual(result.role_runs[0].executor_id, "codex-cli")
        self.assertEqual(result.role_runs[1].executor_route, "evaluator")
        self.assertEqual(result.role_runs[1].executor_id, "openrouter")

        workflow_payload = json.loads((Path(result.workflow_dir) / "workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(workflow_payload["role_runs"][0]["executor_route"], "default")
        self.assertEqual(workflow_payload["role_runs"][0]["executor_id"], "codex-cli")
        self.assertEqual(workflow_payload["role_runs"][1]["executor_route"], "evaluator")
        self.assertEqual(workflow_payload["role_runs"][1]["executor_id"], "openrouter")
```

- [ ] **Step 6: Run the focused failing workflow test**

Run:

```bash
python3 -m pytest tests/test_workflow_engine.py::WorkflowEngineTests::test_workflow_persists_executor_route_trace -q
```

Expected: FAIL because `RoleRun` does not yet expose `executor_route` or `executor_id`.

- [ ] **Step 7: Persist route trace in workflow role artifacts**

In `academic_engine/workflow_engine.py`, add these fields to `RoleRun` after `action`:

```python
    executor_route: str | None = None
    executor_id: str | None = None
```

In `RoleRun.to_dict()`, add these keys after `"action": self.action,`:

```python
            "executor_route": self.executor_route,
            "executor_id": self.executor_id,
```

In `WorkflowEngine._run_role()`, after creating `context = RoleExecutionContext(...)` and before `self.executor_router.execute(context, prompt)`, add:

```python
                if isinstance(self.executor_router, ExecutorRouter):
                    selection = self.executor_router.describe_selection(context)
                    role.executor_route = selection.route_name
                    role.executor_id = selection.executor_id
                else:
                    role.executor_route = (
                        "evaluator"
                        if context.is_evaluator
                        else "verifier"
                        if context.is_verifier
                        else "default"
                    )
                    role.executor_id = "custom"
```

This trace is non-secret: it records only route names such as `default`, `evaluator`, `verifier` and executor ids such as `codex-cli`, `openrouter`, or `custom`.

- [ ] **Step 8: Verify focused route trace tests pass**

Run:

```bash
python3 -m pytest tests/test_executors.py::ExecutorTests::test_router_describes_selected_executor_route tests/test_workflow_engine.py::WorkflowEngineTests::test_workflow_persists_executor_route_trace -q
```

Expected: PASS.

- [ ] **Step 9: Run provider/executor regression tests**

Run:

```bash
python3 -m pytest tests/test_executors.py tests/test_workflow_engine.py::WorkflowEngineTests::test_provider_execution_error_records_provider_blocker_code -q
```

Expected: PASS. No live network calls happen.

- [ ] **Step 10: Commit route trace slice**

Run:

```bash
git add academic_engine/executors.py academic_engine/workflow_engine.py tests/test_executors.py tests/test_workflow_engine.py
git commit -m "feat: record executor route trace"
```

Expected: commit succeeds with only the route trace code and tests staged.

---

### Task 2: Add Evidence Report Generator

**Files:**
- Create: `scripts/openrouter_evidence_report.py`
- Create: `tests/test_openrouter_evidence_report.py`

- [ ] **Step 1: Add failing evidence report tests**

Create `tests/test_openrouter_evidence_report.py` with:

```python
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


FAKE_OPENROUTER_KEY = "sk-or-v1-" + "unit-test-secret-1234567890"


class OpenRouterEvidenceReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.script = Path(__file__).resolve().parents[1] / "scripts" / "openrouter_evidence_report.py"
        self.workflow_id = "workflow-live-smoke"
        self.workflow_dir = self.root / "output" / "runs" / self.workflow_id
        self.workflow_dir.mkdir(parents=True)
        (self.workflow_dir / "roles").mkdir()
        self.stdout_log = self.root / "stdout.log"
        self.stderr_log = self.root / "stderr.log"
        self.stdout_log.write_text("Workflow ID: workflow-live-smoke\n", encoding="utf-8")
        self.stderr_log.write_text("", encoding="utf-8")
        self.report = self.root / "docs" / "deploy" / "evidence" / "report.md"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_workflow(self, *, roles: list[dict[str, object]]) -> None:
        payload = {
            "version": "workflow-run/v1",
            "workflow_id": self.workflow_id,
            "work_id": "openrouter-live-smoke",
            "lane": "article",
            "action": "repair",
            "status": "completed",
            "execution_status": "succeeded",
            "readiness_status": "strong-draft-with-blockers",
            "role_runs": roles,
            "blockers": [],
        }
        (self.workflow_dir / "workflow.json").write_text(json.dumps(payload), encoding="utf-8")

    def run_report(self, *, secret: str = FAKE_OPENROUTER_KEY) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["OPENROUTER_API_KEY"] = secret
        env["ACADEMIC_ENGINE_OPENROUTER_MODEL"] = "openrouter/test-model"
        return subprocess.run(
            [
                sys.executable,
                str(self.script),
                "--root",
                str(self.root),
                "--workflow-id",
                self.workflow_id,
                "--stdout-log",
                str(self.stdout_log),
                "--stderr-log",
                str(self.stderr_log),
                "--report",
                str(self.report),
            ],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def test_report_passes_for_allowed_openrouter_routes(self) -> None:
        self.write_workflow(
            roles=[
                {
                    "role_run_id": "01-academic-repair-orchestrator",
                    "role_id": "academic-repair-orchestrator",
                    "status": "succeeded",
                    "executor_route": "default",
                    "executor_id": "codex-cli",
                    "blockers": [],
                },
                {
                    "role_run_id": "02-academic-source-verifier",
                    "role_id": "academic-source-verifier",
                    "status": "succeeded",
                    "executor_route": "verifier",
                    "executor_id": "openrouter",
                    "blockers": [],
                },
                {
                    "role_run_id": "04-academic-submission-evaluator",
                    "role_id": "academic-submission-evaluator",
                    "status": "succeeded",
                    "executor_route": "evaluator",
                    "executor_id": "openrouter",
                    "blockers": [{"code": "primary-support-gap"}],
                },
            ]
        )

        result = self.run_report()

        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.report.read_text(encoding="utf-8")
        self.assertIn("Route policy: PASS", text)
        self.assertIn("Secret scan: PASS", text)
        self.assertIn("| academic-source-verifier | verifier | openrouter | succeeded |", text)
        self.assertIn("| academic-submission-evaluator | evaluator | openrouter | succeeded |", text)

    def test_report_fails_when_openrouter_reaches_finalizer(self) -> None:
        self.write_workflow(
            roles=[
                {
                    "role_run_id": "01-academic-finalizer",
                    "role_id": "academic-finalizer",
                    "status": "succeeded",
                    "executor_route": "default",
                    "executor_id": "openrouter",
                    "blockers": [],
                }
            ]
        )

        result = self.run_report()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Route policy violation", result.stderr)

    def test_report_fails_on_exact_secret_leak(self) -> None:
        secret = FAKE_OPENROUTER_KEY
        self.write_workflow(
            roles=[
                {
                    "role_run_id": "01-academic-submission-evaluator",
                    "role_id": "academic-submission-evaluator",
                    "status": "succeeded",
                    "executor_route": "evaluator",
                    "executor_id": "openrouter",
                    "blockers": [],
                }
            ]
        )
        self.stdout_log.write_text(f"leaked {secret}\n", encoding="utf-8")

        result = self.run_report(secret=secret)

        self.assertEqual(result.returncode, 1)
        self.assertIn("Secret scan failed", result.stderr)
```

- [ ] **Step 2: Run the failing evidence report tests**

Run:

```bash
python3 -m pytest tests/test_openrouter_evidence_report.py -q
```

Expected: FAIL because `scripts/openrouter_evidence_report.py` does not exist.

- [ ] **Step 3: Add the evidence report generator**

Create `scripts/openrouter_evidence_report.py` with:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ALLOWED_OPENROUTER_ROLES = {
    "academic-source-verifier",
    "academic-submission-evaluator",
    "thesis-source-verifier",
    "thesis-submission-evaluator",
}
SECRET_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]{20,}"),
    re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
    re.compile(r"OPENROUTER_API_KEY=(sk-or-v1-[A-Za-z0-9_-]{20,}|[A-Za-z0-9._-]{20,})"),
)


@dataclass(frozen=True)
class ScanFinding:
    path: Path
    reason: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an OpenRouter controlled live smoke evidence report.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--stdout-log", action="append", default=[])
    parser.add_argument("--stderr-log", action="append", default=[])
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    workflow_dir = root / "output" / "runs" / args.workflow_id
    workflow_path = workflow_dir / "workflow.json"
    if not workflow_path.exists():
        print(f"Missing workflow artifact: {workflow_path}", file=sys.stderr)
        return 1

    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    roles = workflow.get("role_runs") if isinstance(workflow.get("role_runs"), list) else []
    route_errors = _route_policy_errors(roles)

    scan_paths = _scan_paths(root, workflow_dir, args.stdout_log, args.stderr_log)
    scan_findings = _scan_for_secrets(scan_paths, os.environ.get("OPENROUTER_API_KEY") or "")

    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = root / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _render_report(
            root=root,
            workflow=workflow,
            workflow_dir=workflow_dir,
            roles=roles,
            route_errors=route_errors,
            scan_paths=scan_paths,
            scan_findings=scan_findings,
        ),
        encoding="utf-8",
    )

    if route_errors:
        print("Route policy violation: " + "; ".join(route_errors), file=sys.stderr)
    if scan_findings:
        print("Secret scan failed: " + "; ".join(f"{item.path}: {item.reason}" for item in scan_findings), file=sys.stderr)
    if route_errors or scan_findings:
        return 1
    print(f"Evidence report written: {report_path}")
    return 0


def _route_policy_errors(roles: list[Any]) -> list[str]:
    errors: list[str] = []
    for item in roles:
        if not isinstance(item, dict):
            errors.append("role entry is not an object")
            continue
        role_id = str(item.get("role_id") or "")
        executor_id = str(item.get("executor_id") or "")
        executor_route = str(item.get("executor_route") or "")
        if not executor_id or not executor_route:
            errors.append(f"{role_id or 'unknown-role'} lacks executor route trace")
            continue
        if executor_id == "openrouter" and role_id not in ALLOWED_OPENROUTER_ROLES:
            errors.append(f"{role_id} used openrouter")
        if executor_id == "openrouter" and executor_route not in {"evaluator", "verifier"}:
            errors.append(f"{role_id} used openrouter on route {executor_route}")
        if role_id.endswith("finalizer") and executor_id == "openrouter":
            errors.append(f"{role_id} finalizer used openrouter")
    return errors


def _scan_paths(root: Path, workflow_dir: Path, stdout_logs: list[str], stderr_logs: list[str]) -> list[Path]:
    candidates = [
        root / "README.md",
        root / ".env.example",
        root / "docs" / "deploy" / "openrouter-runbook.md",
        root / "docs" / "deploy" / "evidence",
        root / "works" / "openrouter-live-smoke",
        workflow_dir,
    ]
    candidates.extend(Path(item) for item in stdout_logs)
    candidates.extend(Path(item) for item in stderr_logs)

    paths: list[Path] = []
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else root / candidate
        if not path.exists():
            continue
        if path.is_dir():
            paths.extend(item for item in path.rglob("*") if item.is_file())
        elif path.is_file():
            paths.append(path)
    return sorted(set(paths))


def _scan_for_secrets(paths: list[Path], exact_secret: str) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if exact_secret and exact_secret in text:
            findings.append(ScanFinding(path, "exact OPENROUTER_API_KEY value"))
            continue
        for pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(ScanFinding(path, f"secret-like pattern {pattern.pattern}"))
                break
    return findings


def _render_report(
    *,
    root: Path,
    workflow: dict[str, Any],
    workflow_dir: Path,
    roles: list[Any],
    route_errors: list[str],
    scan_paths: list[Path],
    scan_findings: list[ScanFinding],
) -> str:
    workflow_id = str(workflow.get("workflow_id") or "")
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    commit = _git(["rev-parse", "--short", "HEAD"], root)
    model = os.environ.get("ACADEMIC_ENGINE_OPENROUTER_MODEL") or "not-recorded"
    route_status = "PASS" if not route_errors else "FAIL"
    scan_status = "PASS" if not scan_findings else "FAIL"
    lines = [
        "# OpenRouter Controlled Live Workflow Smoke Evidence",
        "",
        "## Summary",
        "",
        f"- Branch: `{branch}`",
        f"- Commit: `{commit}`",
        f"- Workflow ID: `{workflow_id}`",
        f"- Work ID: `{workflow.get('work_id')}`",
        f"- Lane/action: `{workflow.get('lane')}/{workflow.get('action')}`",
        f"- Execution status: `{workflow.get('execution_status')}`",
        f"- Readiness status: `{workflow.get('readiness_status')}`",
        f"- OpenRouter model: `{model}`",
        f"- Route policy: {route_status}",
        f"- Secret scan: {scan_status}",
        "",
        "## Route Table",
        "",
        "| Role | Route | Executor | Status | Blocker Codes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in roles:
        if not isinstance(item, dict):
            continue
        blocker_codes = ", ".join(
            str(blocker.get("code"))
            for blocker in item.get("blockers", [])
            if isinstance(blocker, dict) and blocker.get("code")
        )
        lines.append(
            "| {role} | {route} | {executor} | {status} | {codes} |".format(
                role=item.get("role_id") or "",
                route=item.get("executor_route") or "",
                executor=item.get("executor_id") or "",
                status=item.get("status") or "",
                codes=blocker_codes or "-",
            )
        )
    lines.extend(
        [
            "",
            "## Artifact Inspection",
            "",
            f"- Workflow directory inspected: `{_rel(root, workflow_dir)}`",
            f"- Files scanned for secrets: {len(scan_paths)}",
            "- Raw `output/runs/<workflow_id>/` artifacts remain local and are not committed.",
            "",
            "## Policy Findings",
            "",
        ]
    )
    if route_errors:
        lines.extend(f"- Route policy violation: {item}" for item in route_errors)
    else:
        lines.append("- No OpenRouter route was observed outside evaluator/verifier roles.")
    if scan_findings:
        lines.extend(f"- Secret finding: `{_rel(root, item.path)}` ({item.reason})" for item in scan_findings)
    else:
        lines.append("- No exact OpenRouter key or secret-like bearer value was found in scanned artifacts.")
    lines.extend(
        [
            "",
            "## Rollout Decision",
            "",
            (
                "Controlled live workflow smoke is acceptable for evaluator/verifier rollout."
                if not route_errors and not scan_findings
                else "Controlled live workflow smoke is blocked until the findings above are fixed."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _git(args: list[str], root: Path) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Make the script executable**

Run:

```bash
chmod +x scripts/openrouter_evidence_report.py
```

Expected: command exits 0.

- [ ] **Step 5: Verify evidence report tests pass**

Run:

```bash
python3 -m pytest tests/test_openrouter_evidence_report.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit evidence reporter**

Run:

```bash
git add scripts/openrouter_evidence_report.py tests/test_openrouter_evidence_report.py
git commit -m "test: add openrouter evidence report generator"
```

Expected: commit succeeds with only the script and tests staged.

---

### Task 3: Add Controlled Smoke Fixture And Docs Handoff

**Files:**
- Modify: `workspace.toml`
- Create: `works/openrouter-live-smoke/work.toml`
- Create: `works/openrouter-live-smoke/work-canon.md`
- Create: `works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md`
- Create: article lane directories under `works/openrouter-live-smoke/articles/`
- Modify: `docs/deploy/openrouter-runbook.md`
- Modify: `README.md`
- Create: `docs/deploy/evidence/README.md`

- [ ] **Step 1: Create the dedicated non-default smoke work**

Run:

```bash
python3 -m academic_engine.work_cli work init openrouter-live-smoke --artifact-type article --title "OpenRouter Live Smoke" --topic "Controlled live provider routing smoke fixture" --lanes article --json
```

Expected: command exits 0 and `workspace.toml` registers `openrouter-live-smoke`. Confirm `default_work` remains `starter-work`.

- [ ] **Step 2: Add the controlled smoke draft**

Create `works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md` with:

```markdown
# OpenRouter Controlled Live Smoke Draft

This is a deliberately small non-submission article draft used only to verify live provider routing.

Claim under review: an academic workflow engine can route evaluator and verifier roles through a live provider while keeping writer, finalizer, promotion, and gate authority inside the local engine.

Expected review outcome: this draft is not submission-ready. The reviewer should report that the claim is a deployment smoke assertion, not a supported academic article claim, and should preserve any blockers honestly.
```

- [ ] **Step 3: Inspect smoke work registration**

Run:

```bash
python3 -m academic_engine.work_cli work-status --work openrouter-live-smoke --json
```

Expected:

- output is valid JSON;
- `"work_id": "openrouter-live-smoke"`;
- active lane includes `"article"`;
- no OpenRouter network call happens.

- [ ] **Step 4: Update the runbook with controlled workflow smoke**

In `docs/deploy/openrouter-runbook.md`, add this section after `## First Workflow Check`:

````markdown
## Controlled Live Workflow Smoke Evidence

Use this only after `provider-smoke openrouter` passes. The controlled smoke uses the dedicated non-default work `openrouter-live-smoke`; it does not change `starter-work` and it does not authorize OpenRouter for writer, finalizer, or default routes.

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
  --workflow-id "<workflow_id>" \
  --stdout-log "/tmp/openrouter-live-smoke.stdout.log" \
  --stderr-log "/tmp/openrouter-live-smoke.stderr.log" \
  --report docs/deploy/evidence/2026-07-06-openrouter-controlled-live-workflow-smoke.md
```

Expected evidence:

- `academic-source-verifier` uses `verifier/openrouter`;
- `academic-submission-evaluator` uses `evaluator/openrouter`;
- writer, repair, citation, and finalizer roles use `default/codex-cli` or do not run;
- readiness may be `strong-draft-with-blockers`;
- the evidence report says `Route policy: PASS`;
- the evidence report says `Secret scan: PASS`.

Do not commit raw `output/runs/<workflow_id>/` artifacts. Commit only the sanitized Markdown evidence report under `docs/deploy/evidence/`.
````

- [ ] **Step 5: Update README handoff**

In `README.md`, under the OpenRouter provider route section after the runbook link sentence, add:

```markdown
For deploy rollout beyond provider smoke, use the controlled live workflow smoke and sanitized evidence report flow in [docs/deploy/openrouter-runbook.md](docs/deploy/openrouter-runbook.md).
```

- [ ] **Step 6: Add evidence directory README**

Create `docs/deploy/evidence/README.md` with:

```markdown
# Deploy Evidence Reports

This directory stores sanitized deploy evidence reports that are safe to commit.

Allowed:

- Markdown summaries generated by `scripts/openrouter_evidence_report.py`;
- workflow ids, role ids, route names, executor ids, blocker codes, model slugs, and pass/fail scan results.

Not allowed:

- `.env` files;
- raw shell transcripts containing secrets;
- raw `output/runs/<workflow_id>/` directories;
- provider request headers;
- `OPENROUTER_API_KEY` values or bearer tokens.
```

- [ ] **Step 7: Verify fixture/docs references**

Run:

```bash
test -f works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md
rg -n "openrouter-live-smoke|openrouter_evidence_report.py|controlled live workflow smoke|docs/deploy/evidence" README.md docs/deploy/openrouter-runbook.md docs/deploy/evidence/README.md workspace.toml works/openrouter-live-smoke/work.toml
```

Expected: all references are present and no command calls OpenRouter.

- [ ] **Step 8: Commit smoke fixture and docs**

Run:

```bash
git add workspace.toml works/openrouter-live-smoke README.md docs/deploy/openrouter-runbook.md docs/deploy/evidence/README.md
git commit -m "docs: add openrouter controlled smoke fixture"
```

Expected: commit succeeds. `workspace.toml` still has `default_work = "starter-work"`.

---

### Task 4: Run Controlled Live Workflow Smoke And Save Evidence

**Files:**
- Create: `docs/deploy/evidence/2026-07-06-openrouter-controlled-live-workflow-smoke.md`
- Local only: `/tmp/openrouter-live-smoke.stdout.log`
- Local only: `/tmp/openrouter-live-smoke.stderr.log`
- Local only: `output/runs/<workflow_id>/`

This task is manual and deploy-oriented. It requires network access and a valid OpenRouter key. Do not run it in ordinary CI.

- [ ] **Step 1: Verify preflight without network**

Run:

```bash
git status --short --branch
python3 -m pytest tests/test_executors.py tests/test_workflow_engine.py::WorkflowEngineTests::test_workflow_persists_executor_route_trace tests/test_openrouter_evidence_report.py -q
```

Expected:

- branch is `main` or the current feature branch;
- no unrelated tracked changes are present;
- tests pass;
- no OpenRouter network call happens.

- [ ] **Step 2: Verify live env is present without printing secrets**

Run:

```bash
test -n "${OPENROUTER_API_KEY:-}"
test -n "${ACADEMIC_ENGINE_OPENROUTER_MODEL:-}"
export ACADEMIC_ENGINE_EVALUATOR_EXECUTOR=openrouter
export ACADEMIC_ENGINE_VERIFIER_EXECUTOR=openrouter
unset ACADEMIC_ENGINE_DEFAULT_EXECUTOR
```

Expected: commands exit 0. No command prints the key.

- [ ] **Step 3: Run provider smoke and capture safe logs**

Run:

```bash
export ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1
python3 -m academic_engine.work_cli provider-smoke openrouter > /tmp/openrouter-provider-smoke.stdout.log 2> /tmp/openrouter-provider-smoke.stderr.log
unset ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST
```

Expected `/tmp/openrouter-provider-smoke.stdout.log` includes:

```text
[provider-smoke] provider: openrouter
[provider-smoke] model:
[provider-smoke] response_chars:
[provider-smoke] preview: provider-smoke-ok
```

Expected `/tmp/openrouter-provider-smoke.stderr.log` is empty. If the command fails, stop and use the diagnostics matrix in `docs/deploy/openrouter-runbook.md`.

- [ ] **Step 4: Enqueue controlled live workflow and capture launcher output**

Run:

```bash
python3 -m academic_engine.work_cli launch-academic repair \
  works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md \
  --work openrouter-live-smoke \
  --no-search \
  > /tmp/openrouter-live-smoke.stdout.log \
  2> /tmp/openrouter-live-smoke.stderr.log
```

Expected stdout includes:

```text
Enqueue status: queued
Workflow ID:
Run ID:
Work ID: openrouter-live-smoke
```

Extract the workflow id without printing secrets:

```bash
WORKFLOW_ID="$(awk '/Workflow ID:/ {print $3}' /tmp/openrouter-live-smoke.stdout.log)"
test -n "${WORKFLOW_ID:-}"
```

- [ ] **Step 5: Dispatch the queued workflow**

Run:

```bash
python3 -m academic_engine.work_cli jobs dispatch --limit 1 --json >> /tmp/openrouter-live-smoke.stdout.log 2>> /tmp/openrouter-live-smoke.stderr.log
```

Expected: the queued job is started or completed. If it is still running, poll:

```bash
python3 -m academic_engine.work_cli runtime-index refresh --json >> /tmp/openrouter-live-smoke.stdout.log 2>> /tmp/openrouter-live-smoke.stderr.log
python3 -m academic_engine.work_cli runtime-index status --work openrouter-live-smoke --json >> /tmp/openrouter-live-smoke.stdout.log 2>> /tmp/openrouter-live-smoke.stderr.log
```

Expected final workflow artifact exists:

```bash
test -f "output/runs/${WORKFLOW_ID}/workflow.json"
```

- [ ] **Step 6: Generate sanitized evidence report**

Run:

```bash
python3 scripts/openrouter_evidence_report.py \
  --workflow-id "${WORKFLOW_ID}" \
  --stdout-log /tmp/openrouter-provider-smoke.stdout.log \
  --stderr-log /tmp/openrouter-provider-smoke.stderr.log \
  --stdout-log /tmp/openrouter-live-smoke.stdout.log \
  --stderr-log /tmp/openrouter-live-smoke.stderr.log \
  --report docs/deploy/evidence/2026-07-06-openrouter-controlled-live-workflow-smoke.md
```

Expected: command exits 0 and prints `Evidence report written: ...`.

- [ ] **Step 7: Inspect route and secret evidence**

Run:

```bash
rg -n "Route policy: PASS|Secret scan: PASS|academic-source-verifier|academic-submission-evaluator|openrouter|codex-cli" docs/deploy/evidence/2026-07-06-openrouter-controlled-live-workflow-smoke.md
rg -n "sk-or-v1-[A-Za-z0-9_-]{20,}|Authorization: Bearer|OPENROUTER_API_KEY=(sk-or-v1-[A-Za-z0-9_-]{20,}|[A-Za-z0-9._-]{20,})" docs/deploy/evidence/2026-07-06-openrouter-controlled-live-workflow-smoke.md
```

Expected:

- first `rg` finds the route table and pass lines;
- second `rg` has no matches.

- [ ] **Step 8: Commit the evidence report**

Run:

```bash
git add docs/deploy/evidence/2026-07-06-openrouter-controlled-live-workflow-smoke.md
git commit -m "docs: add openrouter live workflow smoke evidence"
```

Expected: commit includes only the sanitized report. Do not stage `output/runs/` or `/tmp` logs.

---

### Task 5: Final Verification

**Files:**
- Verify all files touched by Tasks 1-4.

- [ ] **Step 1: Run deterministic tests**

Run:

```bash
python3 -m pytest tests/test_executors.py tests/test_workflow_engine.py::WorkflowEngineTests::test_workflow_persists_executor_route_trace tests/test_openrouter_evidence_report.py -q
```

Expected: PASS. No live network calls happen.

- [ ] **Step 2: Run full unit suite if time permits**

Run:

```bash
python3 -m unittest discover -s tests -q
```

Expected: PASS. If existing unrelated failures appear, record the exact failing tests and do not mix unrelated cleanup into this slice.

- [ ] **Step 3: Check for accidental secrets in committed surfaces**

Run:

```bash
rg -n "sk-or-v1-[A-Za-z0-9_-]{20,}|Authorization: Bearer|OPENROUTER_API_KEY=(sk-or-v1-[A-Za-z0-9_-]{20,}|[A-Za-z0-9._-]{20,})" README.md .env.example docs/deploy works/openrouter-live-smoke scripts tests
```

Expected: no matches. The literal `sk-or-v1-redacted` remains acceptable if it appears because it does not match the real-looking key pattern.

- [ ] **Step 4: Check formatting and final diff**

Run:

```bash
git diff --check
git status --short --branch
git log --oneline -5
```

Expected:

- no whitespace errors;
- no raw runtime artifacts staged;
- recent commits match the route trace, evidence reporter, fixture/docs, and evidence report slices.

---

## Self-Review

Spec coverage:

- Controlled live workflow smoke: Task 4.
- Non-critical work: Task 3 creates `openrouter-live-smoke` and keeps `starter-work` as default.
- Env contract: Task 4 uses existing OpenRouter env and keeps default route unset.
- Artifact inspection: Task 2 script and Task 4 report inspect `workflow.json`, role runs, and logs.
- Secret leakage proof: Task 2 exact/regex scanner and Task 4 report.
- Evidence report: Task 4 writes `docs/deploy/evidence/2026-07-06-openrouter-controlled-live-workflow-smoke.md`.
- Default/writer/finalizer boundary: Task 1 route trace plus Task 2 route policy enforce OpenRouter only on evaluator/verifier roles.

Scope guard:

- No OpenRouter live call is added to CI.
- No new dependency or SDK is added.
- No provider routing is expanded beyond evaluator/verifier.
- `WorkflowEngine` remains responsible for gates, blockers, readiness, and promotion.
