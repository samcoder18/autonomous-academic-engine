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
    "thesis-source-verifier": "verifier",
    "academic-submission-evaluator": "evaluator",
    "thesis-submission-evaluator": "evaluator",
}
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


def role_runs(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    raw_roles = workflow.get("role_runs", [])
    if not isinstance(raw_roles, list):
        return []
    return [role for role in raw_roles if isinstance(role, dict)]


def route_policy_violations(roles: list[dict[str, Any]]) -> list[str]:
    violations: list[str] = []
    for role in roles:
        role_id = str(role.get("role_id", ""))
        route = str(role.get("executor_route", ""))
        executor_id = str(role.get("executor_id", ""))
        if executor_id != "openrouter":
            continue
        expected_route = ALLOWED_OPENROUTER_ROUTES.get(role_id)
        if expected_route != route:
            expected = expected_route or "<not-allowed>"
            violations.append(
                f"{role_id or '<missing-role>'} used openrouter on route "
                f"{route or '<missing-route>'}; expected {expected}"
            )
    return violations


def _scan_paths(root: Path, workflow_id: str, stdout_logs: list[Path], stderr_logs: list[Path]) -> list[Path]:
    candidates = [
        root / "output" / "runs" / workflow_id,
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
    route_ok: bool,
    secret_ok: bool,
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
    if route_violations:
        lines.extend(f"- Route policy violation: {violation}" for violation in route_violations)
    if secret_failures:
        lines.extend(f"- Secret scan failed: {failure}" for failure in secret_failures)
    if not route_violations and not secret_failures:
        lines.append("- No policy or secret-scan failures detected.")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.root
    workflow = load_workflow(root, args.workflow_id)
    roles = role_runs(workflow)
    route_violations = route_policy_violations(roles)
    scan_files = _scan_paths(root, args.workflow_id, args.stdout_log, args.stderr_log)
    secret_failures = secret_scan_failures(scan_files, os.environ.get("OPENROUTER_API_KEY"))
    route_ok = not route_violations
    secret_ok = not secret_failures
    write_report(
        args.report,
        workflow,
        roles,
        route_ok,
        secret_ok,
        route_violations,
        secret_failures,
        artifact_counts(root, args.workflow_id),
        os.environ.get("ACADEMIC_ENGINE_OPENROUTER_MODEL", ""),
    )
    if not route_ok:
        print("Route policy violation", file=sys.stderr)
    if not secret_ok:
        print("Secret scan failed", file=sys.stderr)
    return 0 if route_ok and secret_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
