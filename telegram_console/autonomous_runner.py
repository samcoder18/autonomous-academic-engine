from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from .autonomous_planner import AutonomousPlan
from .autonomous_runtime_store import read_json_payload, runtime_file_path, write_json_payload
from .orchestrator import WorkflowOrchestrator
from .utils import utc_now

AUTONOMOUS_STATE_VERSION = "v1"


def autonomous_state_path(root_dir: str | Path, work_id: str) -> Path:
    return runtime_file_path(root_dir, f"{work_id}.json")


def write_autonomous_state(root_dir: str | Path, work_id: str, payload: dict[str, Any]) -> Path:
    path = autonomous_state_path(root_dir, work_id)
    write_json_payload(path, payload)
    return path


def read_autonomous_state(root_dir: str | Path, work_id: str) -> dict[str, Any] | None:
    return read_json_payload(autonomous_state_path(root_dir, work_id))


def autonomous_status_payload(root_dir: str | Path, work_id: str) -> dict[str, Any]:
    payload = read_autonomous_state(root_dir, work_id) or {}
    return {
        "version": AUTONOMOUS_STATE_VERSION,
        "kind": "autonomous-run-state",
        "status": str(payload.get("status") or "not-started"),
        "mode": payload.get("mode"),
        "work_id": str(payload.get("work_id") or work_id),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "readiness_claim": "none",
        "plan": payload.get("plan") if isinstance(payload.get("plan"), dict) else None,
        "executed_steps": payload.get("executed_steps") if isinstance(payload.get("executed_steps"), list) else [],
        "stop_reason": payload.get("stop_reason"),
    }


def run_autonomous_plan(
    *,
    root_dir: str | Path,
    plan: AutonomousPlan,
    dry_run: bool,
    execute: bool = False,
) -> dict[str, Any]:
    payload = plan.to_dict()
    work_id = str(payload.get("work_id") or "default")
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    executed: list[dict[str, Any]] = []
    status = "dry-run" if dry_run or not execute else "completed"
    stop_reason = payload.get("stop_reason")
    orchestrator = WorkflowOrchestrator(root_dir)

    if execute and not dry_run and not steps:
        status = "stopped"
        stop_reason = stop_reason or "no-autonomous-actions"
    elif execute and not dry_run:
        for step in steps:
            policy = step.get("policy") if isinstance(step, dict) else {}
            if not isinstance(policy, dict) or policy.get("decision") != "allowed":
                stop_reason = "operator-confirmation-required"
                status = "stopped"
                break
            command = str(step.get("command") or "").strip()
            result = _execute_allowed_command(orchestrator, command, work_id=work_id)
            executed.append(result)
            if result.get("status") == "started-run":
                stop_reason = "step-started"
                break
            if result.get("status") == "completed":
                stop_reason = "step-completed"
                continue
            if result.get("status") != "skipped":
                status = "stopped"
                stop_reason = str(result.get("reason") or "execution-stopped")
                break

    state = {
        "version": AUTONOMOUS_STATE_VERSION,
        "kind": "autonomous-run-state",
        "status": status,
        "mode": payload.get("mode"),
        "work_id": work_id,
        "started_at": utc_now(),
        "finished_at": utc_now(),
        "readiness_claim": "none",
        "plan": payload,
        "executed_steps": executed,
        "stop_reason": stop_reason,
    }
    write_autonomous_state(root_dir, work_id, state)
    return state


def stop_autonomous_run(root_dir: str | Path, work_id: str, *, reason: str = "operator-stop") -> dict[str, Any]:
    existing = read_autonomous_state(root_dir, work_id) or {}
    payload = {
        **existing,
        "version": AUTONOMOUS_STATE_VERSION,
        "kind": "autonomous-run-state",
        "status": "stopped",
        "work_id": work_id,
        "finished_at": utc_now(),
        "readiness_claim": "none",
        "stop_reason": reason,
    }
    write_autonomous_state(root_dir, work_id, payload)
    return payload


def execute_autonomous_command(
    orchestrator: WorkflowOrchestrator,
    command: str,
    *,
    work_id: str | None = None,
) -> dict[str, Any]:
    args = shlex.split(command)
    if not args:
        return {"status": "skipped", "reason": "empty-command"}
    if args[0] == "work-status":
        return {"status": "completed", "command": command}
    if args[0] == "standards-status":
        return {"status": "completed", "command": command}
    if args[0] == "export-thesis-docx":
        result = orchestrator.export_docx("thesis", work_id=work_id)
        return _validated_export_result(command, result, work_id=work_id)
    if args[0] == "export-article-docx" and len(args) >= 2:
        article_slug = Path(args[1]).stem
        result = orchestrator.export_docx(f"article:{article_slug}", work_id=work_id)
        return _validated_export_result(command, result, work_id=work_id)
    if args[0] == "launch-thesis" and len(args) >= 3:
        active = orchestrator.start_run("thesis", args[1], args[2], work_id=work_id)
        return _validated_started_run_result(command, active, work_id=work_id)
    if args[0] == "launch-academic" and len(args) >= 3:
        active = orchestrator.start_run("article", args[1], args[2], work_id=work_id)
        return _validated_started_run_result(command, active, work_id=work_id)
    return {"status": "skipped", "reason": "unsupported-command", "command": command}


def _validated_export_result(
    command: str,
    result: object,
    *,
    work_id: str | None,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "status": "failed",
            "reason": "invalid-export-result",
            "command": command,
        }
    output_path = str(result.get("path") or "").strip()
    result_work_id = str(result.get("work_id") or "").strip()
    if not output_path or not Path(output_path).is_file() or (work_id is not None and result_work_id != work_id):
        return {
            "status": "failed",
            "reason": "invalid-export-result",
            "command": command,
            "export": result,
        }
    return {"status": "completed", "command": command, "export": result}


def _validated_started_run_result(
    command: str,
    result: object,
    *,
    work_id: str | None,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "status": "failed",
            "reason": "invalid-start-result",
            "command": command,
        }
    run_id = str(result.get("run_id") or "").strip()
    result_work_id = str(result.get("work_id") or "").strip()
    if not run_id or (work_id is not None and result_work_id != work_id):
        return {
            "status": "failed",
            "reason": "invalid-start-result",
            "command": command,
            "run": result,
        }
    return {"status": "started-run", "command": command, "run_id": run_id}


def _execute_allowed_command(
    orchestrator: WorkflowOrchestrator,
    command: str,
    *,
    work_id: str,
) -> dict[str, Any]:
    return execute_autonomous_command(orchestrator, command, work_id=work_id)
