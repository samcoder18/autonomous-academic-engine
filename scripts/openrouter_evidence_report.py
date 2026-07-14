#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from academic_engine.executors import OPENROUTER_ALLOWED_ROLE_ROUTES, OPENROUTER_ROLE_POLICY  # noqa: E402
from academic_engine.work_bootstrap import WorkBootstrapError, validate_slug  # noqa: E402

EXPECTED_OPENROUTER_ROLE_POLICY = OPENROUTER_ROLE_POLICY
CONTROLLED_SMOKE_WORK_ID = "openrouter-live-smoke"
CONTROLLED_SMOKE_LANE = "article"
CONTROLLED_SMOKE_ACTION = "repair"
CONTROLLED_SMOKE_TARGET = "works/openrouter-live-smoke/articles/drafts/openrouter-live-smoke.md"
DEFAULT_ROUTE = "default"
DEFAULT_EXECUTOR = "codex-cli"
QUALIFICATION_ROLE_ID = "academic-intake"
QUALIFICATION_WORK_ID = "openrouter-live-smoke"
QUALIFICATION_LANE = "article"
QUALIFICATION_ACTION = "qualify-intake"
QUALIFICATION_SEED_PATH = "works/openrouter-live-smoke/articles/briefs/academic-intake-qualification.md"
QUALIFICATION_METADATA_KEYS = frozenset(
    {
        "candidate_id",
        "allowed_path",
        "before_sha256",
        "after_sha256",
        "canonical_unchanged",
    }
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
QUALIFICATION_WORKFLOW_ID_RE = re.compile(
    r"openrouter-live-smoke-article-qualify-intake-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}"
)
SECRET_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]{20,}"),
    re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
    re.compile(r"OPENROUTER_API_KEY=(sk-or-v1-[A-Za-z0-9_-]{20,}|[A-Za-z0-9._-]{20,})"),
)


def expected_work_id(value: str) -> str:
    try:
        validate_slug(value)
    except WorkBootstrapError as exc:
        raise argparse.ArgumentTypeError(f"expected work ID must be a canonical work slug: {exc}") from exc
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate sanitized OpenRouter live-smoke evidence report.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--stdout-log", action="append", default=[], type=Path)
    parser.add_argument("--stderr-log", action="append", default=[], type=Path)
    parser.add_argument("--expected-work-id", default=CONTROLLED_SMOKE_WORK_ID, type=expected_work_id)
    parser.add_argument("--expected-lane", default=CONTROLLED_SMOKE_LANE)
    parser.add_argument("--expected-action", default=CONTROLLED_SMOKE_ACTION)
    parser.add_argument("--expected-target", default=CONTROLLED_SMOKE_TARGET)
    parser.add_argument("--expected-role", action="append", default=[])
    parser.add_argument("--qualification-role", choices=(QUALIFICATION_ROLE_ID,))
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
    *,
    expected_work_id: str,
    expected_lane: str,
    expected_action: str,
    expected_target: str,
) -> list[str]:
    violations: list[str] = []
    expected_workflow = {
        "workflow_id": workflow_id,
        "work_id": expected_work_id,
        "lane": expected_lane,
        "action": expected_action,
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
        "work_id": expected_work_id,
        "lane": expected_lane,
        "action": expected_action,
        "target": expected_target,
    }
    for field, expected in expected_request.items():
        actual = runtime_request.get(field)
        if actual != expected:
            violations.append(f"runtime request {field} is {actual!r}; expected {expected!r}")
    if runtime_request.get("search_override") is not False:
        violations.append("runtime request search_override is not false")
    return violations


def qualification_controlled_smoke_violations(
    workflow: dict[str, Any],
    workflow_id: str,
) -> list[str]:
    violations: list[str] = []
    expected_workflow = {
        "workflow_id": workflow_id,
        "work_id": QUALIFICATION_WORK_ID,
        "lane": QUALIFICATION_LANE,
        "action": QUALIFICATION_ACTION,
        "status": "completed",
        "execution_status": "succeeded",
    }
    for field, expected in expected_workflow.items():
        if workflow.get(field) != expected:
            violations.append(f"qualification workflow {field} does not match the required qualification value")
    return violations


def route_policy_violations(
    roles: list[dict[str, Any]],
    *,
    expected_role_ids: set[str] | None = None,
) -> list[str]:
    violations: list[str] = []
    expected_roles = expected_role_ids or set(OPENROUTER_ALLOWED_ROLE_ROUTES)
    observed_expected_roles: set[str] = set()
    for role_id in sorted(expected_roles):
        if role_id not in EXPECTED_OPENROUTER_ROLE_POLICY:
            violations.append(f"expected role {role_id} is not approved by the OpenRouter policy")
    for role in roles:
        role_id = str(role.get("role_id", ""))
        route = str(role.get("executor_route", ""))
        executor_id = str(role.get("executor_id", ""))
        execution_mode = str(role.get("execution_mode") or "")
        status = str(role.get("status", ""))
        expected_policy = EXPECTED_OPENROUTER_ROLE_POLICY.get(role_id) if role_id in expected_roles else None
        if expected_policy is not None:
            observed_expected_roles.add(role_id)
            expected_executor_id = expected_policy["executor_id"]
            expected_mode = expected_policy["execution_mode"]
            expected_route = OPENROUTER_ALLOWED_ROLE_ROUTES.get(
                role_id,
                "role" if expected_mode == "write-plan" else "",
            )
            if (
                route != expected_route
                or executor_id != expected_executor_id
                or execution_mode != expected_mode
                or status != "succeeded"
            ):
                violations.append(
                    f"{role_id} used {route or '<missing-route>'}/{executor_id or '<missing-executor>'}/"
                    f"{execution_mode or '<missing-mode>'} with status {status or '<missing-status>'}; expected "
                    f"{expected_route}/{expected_executor_id}/{expected_mode} succeeded"
                )
            continue
        if executor_id == "openrouter":
            violations.append(
                f"{role_id or '<missing-role>'} used OpenRouter without being selected for this qualification"
            )
            continue
        if route != DEFAULT_ROUTE or executor_id != DEFAULT_EXECUTOR:
            violations.append(
                f"{role_id or '<missing-role>'} used {route or '<missing-route>'}/"
                f"{executor_id or '<missing-executor>'}; expected {DEFAULT_ROUTE}/{DEFAULT_EXECUTOR}"
            )
    for role_id in sorted(expected_roles):
        expected_policy = EXPECTED_OPENROUTER_ROLE_POLICY.get(role_id)
        if expected_policy is not None and role_id not in observed_expected_roles:
            violations.append(
                f"required role {role_id} did not run on "
                f"{expected_policy['executor_id']}/{expected_policy['execution_mode']}"
            )
    return violations


def qualification_route_policy_violations(workflow: dict[str, Any]) -> list[str]:
    raw_roles = workflow.get("role_runs")
    if not isinstance(raw_roles, list):
        return ["qualification role_runs must be a list"]
    if len(raw_roles) != 1:
        return [f"qualification requires exactly one role; observed {len(raw_roles)}"]
    role = raw_roles[0]
    if not isinstance(role, dict):
        return ["qualification role must be an object"]

    violations: list[str] = []
    expected_fields = {
        "role_id": QUALIFICATION_ROLE_ID,
        "executor_route": "role",
        "executor_id": "openrouter",
        "execution_mode": "write-plan",
        "status": "succeeded",
    }
    for field, expected in expected_fields.items():
        if role.get(field) != expected:
            violations.append(f"qualification role {field} must be {expected!r}")
    return violations


def qualification_control_violations(workflow: dict[str, Any]) -> list[str]:
    raw_roles = workflow.get("role_runs")
    if not isinstance(raw_roles, list) or len(raw_roles) != 1 or not isinstance(raw_roles[0], dict):
        return ["qualification controls require exactly one role object"]
    role = raw_roles[0]
    violations: list[str] = []
    if role.get("write_plan_applied") is not True:
        violations.append("qualification write_plan_applied must be true")
    if role.get("changed_paths") != [QUALIFICATION_SEED_PATH]:
        violations.append(f"qualification changed paths must equal {QUALIFICATION_SEED_PATH}")
    if role.get("forbidden_paths") != []:
        violations.append("qualification forbidden paths must be empty")

    promotion = workflow.get("promotion")
    if not isinstance(promotion, dict):
        violations.append("qualification promotion must be an object")
    else:
        if promotion.get("status") != "skipped":
            violations.append("qualification promotion status must be skipped")
        if promotion.get("reason") != "qualification-no-promotion":
            violations.append("qualification promotion reason must be qualification-no-promotion")

    metadata = workflow.get("metadata")
    if not isinstance(metadata, dict):
        violations.append("qualification canonical metadata must be an object")
        return violations
    if set(metadata) != QUALIFICATION_METADATA_KEYS:
        violations.append("qualification canonical metadata keys are invalid")
    if metadata.get("candidate_id") != QUALIFICATION_ROLE_ID:
        violations.append("qualification canonical metadata candidate_id is invalid")
    if metadata.get("allowed_path") != QUALIFICATION_SEED_PATH:
        violations.append("qualification canonical metadata allowed_path is invalid")
    before_sha256 = metadata.get("before_sha256")
    after_sha256 = metadata.get("after_sha256")
    if (
        not isinstance(before_sha256, str)
        or not isinstance(after_sha256, str)
        or SHA256_RE.fullmatch(before_sha256) is None
        or SHA256_RE.fullmatch(after_sha256) is None
        or before_sha256 != after_sha256
    ):
        violations.append("qualification canonical metadata hashes must be equal lowercase SHA-256 values")
    if metadata.get("canonical_unchanged") is not True:
        violations.append("qualification canonical metadata canonical_unchanged must be true")
    return violations


def _scan_paths(
    root: Path,
    workflow_id: str,
    stdout_logs: list[Path],
    stderr_logs: list[Path],
    *,
    work_id: str,
) -> list[Path]:
    candidates = [
        root / "output" / "runs" / workflow_id,
        root / "output" / "runtime" / "runs" / workflow_id,
        root / "README.md",
        root / ".env.example",
        root / "docs" / "deploy" / "openrouter-runbook.md",
        root / "docs" / "deploy" / "evidence",
        root / "works" / work_id,
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


def qualification_secret_scan_failures(paths: list[Path], exact_secret: str | None) -> list[str]:
    raw_failures = secret_scan_failures(paths, exact_secret)
    return [f"qualification scan finding {index}: secret-like content" for index, _ in enumerate(raw_failures, start=1)]


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
        "| Role | Route | Executor | Mode | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for role in roles:
        lines.append(
            "| {role} | {route} | {executor} | {mode} | {status} |".format(
                role=str(role.get("role_id", "")),
                route=str(role.get("executor_route", "")),
                executor=str(role.get("executor_id", "")),
                mode=str(role.get("execution_mode") or ""),
                status=str(role.get("status", "")),
            )
        )
    return lines


def _qualification_workflow_id(value: object) -> str:
    if isinstance(value, str) and QUALIFICATION_WORKFLOW_ID_RE.fullmatch(value):
        return value
    return "<invalid>"


def _qualification_fixed_value(value: object, expected: str) -> str:
    return expected if value == expected else "<invalid>"


def qualification_route_table(roles: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Role | Route | Executor | Mode | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for role in roles:
        lines.append(
            "| {role} | {route} | {executor} | {mode} | {status} |".format(
                role=_qualification_fixed_value(role.get("role_id"), QUALIFICATION_ROLE_ID),
                route=_qualification_fixed_value(role.get("executor_route"), "role"),
                executor=_qualification_fixed_value(role.get("executor_id"), "openrouter"),
                mode=_qualification_fixed_value(role.get("execution_mode"), "write-plan"),
                status=_qualification_fixed_value(role.get("status"), "succeeded"),
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
    *,
    qualification_mode: bool = False,
    qualification_ok: bool = True,
    qualification_violations: list[str] | None = None,
) -> None:
    total_artifacts, role_artifacts = counts
    qualification_violations = qualification_violations or []
    if qualification_mode:
        lines = [
            "# OpenRouter Evidence Report",
            "",
            f"Workflow ID: {_qualification_workflow_id(workflow.get('workflow_id'))}",
            f"Work ID: {_qualification_fixed_value(workflow.get('work_id'), QUALIFICATION_WORK_ID)}",
            "",
            f"Controlled smoke: {status_text(controlled_ok)}",
            f"Route policy: {status_text(route_ok)}",
            f"Qualification controls: {status_text(qualification_ok)}",
            f"Secret scan: {status_text(secret_ok)}",
            "",
            "## Artifact Counts",
            "",
            f"- Workflow artifacts: {total_artifacts}",
            f"- Role artifacts: {role_artifacts}",
            "",
            "## Route Table",
            "",
            *qualification_route_table(roles),
            "",
            "## Findings",
            "",
        ]
    else:
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
    if qualification_violations:
        lines.extend(f"- Qualification controls violation: {violation}" for violation in qualification_violations)
    if secret_failures:
        lines.extend(f"- Secret scan failed: {failure}" for failure in secret_failures)
    if not controlled_violations and not route_violations and not qualification_violations and not secret_failures:
        lines.append("- No controlled-smoke, route-policy, or secret-scan failures detected.")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.root
    workflow = load_workflow(root, args.workflow_id)
    roles = role_runs(workflow)
    qualification_mode = args.qualification_role is not None
    if qualification_mode:
        controlled_violations = qualification_controlled_smoke_violations(workflow, args.workflow_id)
        route_violations = qualification_route_policy_violations(workflow)
        qualification_violations = qualification_control_violations(workflow)
        work_id_for_scan = QUALIFICATION_WORK_ID
    else:
        runtime_request, request_error = load_runtime_request(root, args.workflow_id)
        controlled_violations = controlled_smoke_violations(
            workflow,
            runtime_request,
            request_error,
            args.workflow_id,
            expected_work_id=args.expected_work_id,
            expected_lane=args.expected_lane,
            expected_action=args.expected_action,
            expected_target=args.expected_target,
        )
        route_violations = route_policy_violations(
            roles,
            expected_role_ids=set(args.expected_role) or None,
        )
        qualification_violations = []
        work_id_for_scan = args.expected_work_id
    scan_files = _scan_paths(
        root,
        args.workflow_id,
        args.stdout_log,
        args.stderr_log,
        work_id=work_id_for_scan,
    )
    if qualification_mode:
        secret_failures = qualification_secret_scan_failures(scan_files, os.environ.get("OPENROUTER_API_KEY"))
    else:
        secret_failures = secret_scan_failures(scan_files, os.environ.get("OPENROUTER_API_KEY"))
    controlled_ok = not controlled_violations
    route_ok = not route_violations
    qualification_ok = not qualification_violations
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
        "" if qualification_mode else os.environ.get("ACADEMIC_ENGINE_OPENROUTER_MODEL", ""),
        qualification_mode=qualification_mode,
        qualification_ok=qualification_ok,
        qualification_violations=qualification_violations,
    )
    if not controlled_ok:
        print("Controlled smoke violation", file=sys.stderr)
    if not route_ok:
        print("Route policy violation", file=sys.stderr)
    if qualification_mode and not qualification_ok:
        print("Qualification controls violation", file=sys.stderr)
    if not secret_ok:
        print("Secret scan failed", file=sys.stderr)
    return 0 if controlled_ok and route_ok and qualification_ok and secret_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
