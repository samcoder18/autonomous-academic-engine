from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import shlex
import tempfile

from .autonomous_planner import AutonomousPlan
from .orchestrator import WorkflowOrchestrator
from .utils import utc_now


AUTONOMOUS_STATE_VERSION = "v1"


def autonomous_state_path(root_dir: str | Path, work_id: str) -> Path:
    return Path(root_dir).resolve() / "output" / "telegram" / "runtime" / "autonomous" / f"{work_id}.json"


def write_autonomous_state(root_dir: str | Path, work_id: str, payload: dict[str, Any]) -> Path:
    path = autonomous_state_path(root_dir, work_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent)) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)
    return path


def read_autonomous_state(root_dir: str | Path, work_id: str) -> dict[str, Any] | None:
    path = autonomous_state_path(root_dir, work_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


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
            result = _execute_allowed_command(orchestrator, command)
            executed.append(result)
            if result.get("status") == "started-run":
                stop_reason = "step-started"
                break
            if result.get("status") == "completed":
                stop_reason = "step-completed"
                break
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


def _execute_allowed_command(orchestrator: WorkflowOrchestrator, command: str) -> dict[str, Any]:
    args = shlex.split(command)
    if not args:
        return {"status": "skipped", "reason": "empty-command"}
    if args[0] == "work-status":
        return {"status": "completed", "command": command}
    if args[0] == "standards-status":
        return {"status": "completed", "command": command}
    if args[0] == "export-thesis-docx":
        result = orchestrator.export_docx("thesis")
        return {"status": "completed", "command": command, "export": result}
    if args[0] == "export-article-docx" and len(args) >= 2:
        article_slug = Path(args[1]).stem
        result = orchestrator.export_docx(f"article:{article_slug}")
        return {"status": "completed", "command": command, "export": result}
    if args[0] == "launch-thesis" and len(args) >= 3:
        active = orchestrator.start_run("thesis", args[1], args[2])
        return {"status": "started-run", "command": command, "run_id": active.get("run_id")}
    if args[0] == "launch-academic" and len(args) >= 3:
        active = orchestrator.start_run("article", args[1], args[2])
        return {"status": "started-run", "command": command, "run_id": active.get("run_id")}
    return {"status": "skipped", "reason": "unsupported-command", "command": command}
