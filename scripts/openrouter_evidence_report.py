#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ALLOWED_OPENROUTER_ROUTES = {
    "academic-source-verifier": "verifier",
    "academic-submission-evaluator": "evaluator",
}
CONTROLLED_SMOKE_WORK_ID = "openrouter-live-smoke"
CONTROLLED_SMOKE_LANE = "article"
CONTROLLED_SMOKE_ACTION = "repair"
CONTROLLED_SMOKE_TARGET = "works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md"
DEFAULT_ROUTE = "default"
DEFAULT_EXECUTOR = "codex-cli"
SECRET_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]{20,}"),
    re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
    re.compile(r"OPENROUTER_API_KEY=(sk-or-v1-[A-Za-z0-9_-]{20,}|[A-Za-z0-9._-]{20,})"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate sanitized OpenRouter live-smoke evidence report.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--stdout-log", action="append", default=[], type=Path)
    parser.add_argument("--stderr-log", action="append", default=[], type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


def load_workflow(root: Path, workflow_id: str) -> dict[str, Any]:
    workflow_path = root / "output" / "runs" / workflow_id / "workflow.json"
    with workflow_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"workflow payload is not an object: {workflow_path}")
    return payload


def load_runtime_request(root: Path, workflow_id: str) -> tuple[dict[str, Any] | None, str | None]:
    request_path = root / "output" / "runtime" / "runs" / workflow_id / "request.json"
    try:
        with request_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"runtime request is unavailable: {request_path} ({exc})"
    if not isinstance(payload, dict):
        return None, f"runtime request is not an object: {request_path}"
    return payload, None


def role_runs(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    raw_roles = workflow.get("role_runs", [])
    if not isinstance(raw_roles, list):
        return []
    return [role for role in raw_roles if isinstance(role, dict)]


def controlled_smoke_violations(
    workflow: dict[str, Any],
    runtime_request: dict[str, Any] | None,
    request_error: str | None,
    workflow_id: str,
) -> list[str]:
    violations: list[str] = []
    expected_workflow = {
        "workflow_id": workflow_id,
        "work_id": CONTROLLED_SMOKE_WORK_ID,
        "lane": CONTROLLED_SMOKE_LANE,
        "action": CONTROLLED_SMOKE_ACTION,
    }
    for field, expected in expected_workflow.items():
        actual = workflow.get(field)
        if actual != expected:
            violations.append(f"workflow {field} is {actual!r}; expected {expected!r}")
    if workflow.get("status") != "completed":
        violations.append(f"workflow status is {workflow.get('status')!r}; expected 'completed'")
    if workflow.get("execution_status") != "succeeded":
        violations.append(
            f"workflow execution_status is {workflow.get('execution_status')!r}; expected 'succeeded'"
        )
    if request_error:
        violations.append(request_error)
        return violations
    if runtime_request is None:
        violations.append("runtime request is unavailable")
        return violations
    expected_request = {
        "workflow_id": workflow_id,
        "work_id": CONTROLLED_SMOKE_WORK_ID,
        "lane": CONTROLLED_SMOKE_LANE,
        "action": CONTROLLED_SMOKE_ACTION,
        "target": CONTROLLED_SMOKE_TARGET,
    }
    for field, expected in expected_request.items():
        actual = runtime_request.get(field)
        if actual != expected:
            violations.append(f"runtime request {field} is {actual!r}; expected {expected!r}")
    if runtime_request.get("search_override") is not False:
        violations.append("runtime request search_override is not false")
    return violations


def route_policy_violations(roles: list[dict[str, Any]]) -> list[str]:
    violations: list[str] = []
    observed_allowed_roles: set[str] = set()
    for role in roles:
        role_id = str(role.get("role_id", ""))
        route = str(role.get("executor_route", ""))
        executor_id = str(role.get("executor_id", ""))
        status = str(role.get("status", ""))
        expected_route = ALLOWED_OPENROUTER_ROUTES.get(role_id)
        if expected_route is not None:
            observed_allowed_roles.add(role_id)
            if route != expected_route or executor_id != "openrouter" or status != "succeeded":
                violations.append(
                    f"{role_id} used {route or '<missing-route>'}/{executor_id or '<missing-executor>'} "
                    f"with status {status or '<missing-status>'}; expected {expected_route}/openrouter succeeded"
                )
            continue
        if route != DEFAULT_ROUTE or executor_id != DEFAULT_EXECUTOR:
            violations.append(
                f"{role_id or '<missing-role>'} used {route or '<missing-route>'}/"
                f"{executor_id or '<missing-executor>'}; expected {DEFAULT_ROUTE}/{DEFAULT_EXECUTOR}"
            )
    for role_id, expected_route in ALLOWED_OPENROUTER_ROUTES.items():
        if role_id not in observed_allowed_roles:
            violations.append(f"required role {role_id} did not run on {expected_route}/openrouter")
    return violations


def _scan_paths(root: Path, workflow_id: str, stdout_logs: list[Path], stderr_logs: list[Path]) -> list[Path]:
    candidates = [
        root / "output" / "runs" / workflow_id,
        root / "output" / "runtime" / "runs" / workflow_id,
        root / "README.md",
        root / ".env.example",
        root / "docs" / "deploy" / "openrouter-runbook.md",
        root / "docs" / "deploy" / "evidence",
        root / "works" / "openrouter-live-smoke",
        *stdout_logs,
        *stderr_logs,
    ]
    files: list[Path] = []
    for candidate in candidates:
        if candidate.is_file():
            files.append(candidate)
        elif candidate.is_dir():
            files.extend(path for path in candidate.rglob("*") if path.is_file())
    seen: set[Path] = set()
    unique_files: list[Path] = []
    for path in files:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_files.append(path)
    return unique_files


def secret_scan_failures(paths: list[Path], exact_secret: str | None) -> list[str]:
    failures: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if exact_secret and exact_secret in text:
            failures.append(f"{path}: exact OPENROUTER_API_KEY value")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                failures.append(f"{path}: secret-like pattern")
                break
    return failures


def artifact_counts(root: Path, workflow_id: str) -> tuple[int, int]:
    workflow_dir = root / "output" / "runs" / workflow_id
    if not workflow_dir.exists():
        return 0, 0
    files = [path for path in workflow_dir.rglob("*") if path.is_file()]
    role_files = [path for path in files if "roles" in path.relative_to(workflow_dir).parts]
    return len(files), len(role_files)


def status_text(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def route_table(roles: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Role | Route | Executor | Status |",
        "| --- | --- | --- | --- |",
    ]
    for role in roles:
        lines.append(
            "| {role} | {route} | {executor} | {status} |".format(
                role=str(role.get("role_id", "")),
                route=str(role.get("executor_route", "")),
                executor=str(role.get("executor_id", "")),
                status=str(role.get("status", "")),
            )
        )
    return lines


def write_report(
    report_path: Path,
    workflow: dict[str, Any],
    roles: list[dict[str, Any]],
    controlled_ok: bool,
    route_ok: bool,
    secret_ok: bool,
    controlled_violations: list[str],
    route_violations: list[str],
    secret_failures: list[str],
    counts: tuple[int, int],
    model: str,
) -> None:
    total_artifacts, role_artifacts = counts
    lines = [
        "# OpenRouter Evidence Report",
        "",
        f"Workflow ID: {workflow.get('workflow_id', '')}",
        f"Work ID: {workflow.get('work_id', '')}",
        f"OpenRouter model: {model or '<unset>'}",
        "",
        f"Controlled smoke: {status_text(controlled_ok)}",
        f"Route policy: {status_text(route_ok)}",
        f"Secret scan: {status_text(secret_ok)}",
        "",
        "## Artifact Counts",
        "",
        f"- Workflow artifacts: {total_artifacts}",
        f"- Role artifacts: {role_artifacts}",
        "",
        "## Route Table",
        "",
        *route_table(roles),
        "",
        "## Findings",
        "",
    ]
    if controlled_violations:
        lines.extend(f"- Controlled smoke violation: {violation}" for violation in controlled_violations)
    if route_violations:
        lines.extend(f"- Route policy violation: {violation}" for violation in route_violations)
    if secret_failures:
        lines.extend(f"- Secret scan failed: {failure}" for failure in secret_failures)
    if not controlled_violations and not route_violations and not secret_failures:
        lines.append("- No controlled-smoke, route-policy, or secret-scan failures detected.")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.root
    workflow = load_workflow(root, args.workflow_id)
    runtime_request, request_error = load_runtime_request(root, args.workflow_id)
    roles = role_runs(workflow)
    controlled_violations = controlled_smoke_violations(
        workflow,
        runtime_request,
        request_error,
        args.workflow_id,
    )
    route_violations = route_policy_violations(roles)
    scan_files = _scan_paths(root, args.workflow_id, args.stdout_log, args.stderr_log)
    secret_failures = secret_scan_failures(scan_files, os.environ.get("OPENROUTER_API_KEY"))
    controlled_ok = not controlled_violations
    route_ok = not route_violations
    secret_ok = not secret_failures
    write_report(
        args.report,
        workflow,
        roles,
        controlled_ok,
        route_ok,
        secret_ok,
        controlled_violations,
        route_violations,
        secret_failures,
        artifact_counts(root, args.workflow_id),
        os.environ.get("ACADEMIC_ENGINE_OPENROUTER_MODEL", ""),
    )
    if not controlled_ok:
        print("Controlled smoke violation", file=sys.stderr)
    if not route_ok:
        print("Route policy violation", file=sys.stderr)
    if not secret_ok:
        print("Secret scan failed", file=sys.stderr)
    return 0 if controlled_ok and route_ok and secret_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
