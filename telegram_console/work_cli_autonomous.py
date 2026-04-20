from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .autonomous_daemon import (
    daemon_status_payload,
    read_daemon_stop_request,
    request_daemon_stop,
    run_daemon_foreground,
    run_daemon_tick,
    start_daemon_process,
)
from .autonomous_launchd import (
    DEFAULT_AUTONOMOUS_DAEMON_LABEL,
    AutonomousDaemonLaunchdError,
    AutonomousDaemonLaunchdManager,
)
from .autonomous_planner import build_autonomous_plan, format_autonomous_plan
from .autonomous_runner import (
    autonomous_status_payload,
    run_autonomous_plan,
    stop_autonomous_run,
)
from .autonomous_scheduler import (
    multi_daemon_status_payload,
    read_multi_daemon_stop_request,
    request_multi_daemon_stop,
    resolve_works_scope,
    run_multi_work_daemon_foreground,
    run_multi_work_daemon_tick,
    start_multi_work_daemon_process,
)
from .orchestrator import WorkflowOrchestrator
from .work_cli_output import emit_cli_error, print_payload
from .workspace import WorkspaceConfigError, load_workspace_config, resolve_work_config


def handle_autonomous_cli(root_dir: Path, args: Any) -> int:
    as_json = bool(getattr(args, "as_json", False))
    try:
        if args.autonomous_command in {"plan", "explain"}:
            return autonomous_plan(root_dir, args.work_id, args.mode, args.max_steps, as_json=as_json)
        if args.autonomous_command == "run":
            return autonomous_run(
                root_dir,
                args.work_id,
                args.mode,
                args.max_steps,
                dry_run=args.dry_run,
                execute=args.execute,
                as_json=as_json,
            )
        if args.autonomous_command == "status":
            return autonomous_status(root_dir, args.work_id, as_json=as_json)
        if args.autonomous_command == "stop":
            return autonomous_stop(root_dir, args.work_id, args.reason, as_json=as_json)
        if args.autonomous_command == "daemon":
            return autonomous_daemon_cli(root_dir, args)
    except WorkspaceConfigError as exc:
        return emit_cli_error(
            str(exc),
            as_json=as_json,
            kind="autonomous-cli-error",
            stop_reason="workspace-config-error",
        )
    return 1


def autonomous_plan(root_dir: Path, work_id: str | None, mode: str, max_steps: int, *, as_json: bool = False) -> int:
    state = WorkflowOrchestrator(root_dir).get_work_state(work_id=work_id)
    plan = build_autonomous_plan(work_state=state, mode=mode, max_steps=max_steps)
    if as_json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_autonomous_plan(plan))
    return 0


def autonomous_run(
    root_dir: Path,
    work_id: str | None,
    mode: str,
    max_steps: int,
    *,
    dry_run: bool,
    execute: bool,
    as_json: bool = False,
) -> int:
    state = WorkflowOrchestrator(root_dir).get_work_state(work_id=work_id)
    plan = build_autonomous_plan(work_state=state, mode=mode, max_steps=max_steps)
    run_state = run_autonomous_plan(root_dir=root_dir, plan=plan, dry_run=dry_run or not execute, execute=execute)
    print_payload(run_state, as_json=as_json, formatter=_format_autonomous_run_state)
    return 0


def autonomous_status(root_dir: Path, work_id: str | None, *, as_json: bool = False) -> int:
    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id)
    payload = autonomous_status_payload(root_dir, work.slug)
    print_payload(payload, as_json=as_json, formatter=_format_autonomous_run_state)
    return 0


def autonomous_stop(root_dir: Path, work_id: str | None, reason: str, *, as_json: bool = False) -> int:
    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id)
    state = stop_autonomous_run(root_dir, work.slug, reason=reason)
    print_payload(state, as_json=as_json, formatter=_format_autonomous_run_state)
    return 0


def autonomous_daemon_cli(root_dir: Path, args: Any) -> int:
    as_json = bool(getattr(args, "as_json", False))
    try:
        if args.daemon_command == "launchd":
            return autonomous_daemon_launchd_cli(root_dir, args)
        if _optional_text(getattr(args, "works_scope", None)):
            return autonomous_multi_daemon_cli(root_dir, args)
        work_id = _resolve_daemon_work_id(root_dir, args.work_id)
        if args.daemon_command == "tick":
            payload = run_daemon_tick(
                root_dir=root_dir,
                work_id=work_id,
                mode=args.mode,
                poll_seconds=args.poll_seconds,
                max_cycles=args.max_cycles,
                max_runtime_minutes=args.max_runtime_minutes,
            )
            _print_daemon_payload(payload, as_json=as_json)
            return 0
        if args.daemon_command == "run":
            payload = run_daemon_foreground(
                root_dir=root_dir,
                work_id=work_id,
                mode=args.mode,
                poll_seconds=args.poll_seconds,
                max_cycles=args.max_cycles,
                max_runtime_minutes=args.max_runtime_minutes,
                stuck_after_minutes=getattr(args, "stuck_after_minutes", None),
            )
            _print_daemon_payload(payload, as_json=as_json)
            return 1 if payload.get("stop_reason") == "daemon-already-running" else 0
        if args.daemon_command == "start":
            payload = start_daemon_process(
                root_dir=root_dir,
                work_id=work_id,
                mode=args.mode,
                poll_seconds=args.poll_seconds,
                max_cycles=args.max_cycles,
                max_runtime_minutes=args.max_runtime_minutes,
            )
            _print_daemon_payload(payload, as_json=as_json)
            return 1 if payload.get("status") == "blocked" else 0
        if args.daemon_command == "status":
            payload = daemon_status_payload(root_dir, work_id)
            _print_daemon_payload(payload, as_json=as_json)
            return 0
        if args.daemon_command == "stop":
            request_daemon_stop(root_dir, work_id, reason=args.reason)
            payload = read_daemon_stop_request(root_dir, work_id) or {}
            _print_daemon_payload(payload, as_json=as_json)
            return 0
    except WorkspaceConfigError as exc:
        return emit_cli_error(
            str(exc),
            as_json=as_json,
            kind="autonomous-daemon-error",
            stop_reason="workspace-config-error",
        )
    return 1


def autonomous_daemon_launchd_cli(root_dir: Path, args: Any) -> int:
    as_json = bool(getattr(args, "as_json", False))
    works_scope = _optional_text(getattr(args, "works_scope", None)) or "all"
    try:
        _resolve_daemon_work_ids(root_dir, works_scope)
        manager = AutonomousDaemonLaunchdManager(
            root_dir,
            label=_optional_text(getattr(args, "label", None)) or DEFAULT_AUTONOMOUS_DAEMON_LABEL,
        )
        if args.daemon_launchd_command == "install":
            result = manager.install(
                works_scope=works_scope,
                mode=args.mode,
                poll_seconds=args.poll_seconds,
                max_cycles=args.max_cycles,
                max_runtime_minutes=args.max_runtime_minutes,
            )
            print_payload(result.to_dict(), as_json=as_json, formatter=manager.format_result)
            return 0
        if args.daemon_launchd_command == "start":
            status = manager.start(works_scope=works_scope)
        elif args.daemon_launchd_command == "restart":
            status = manager.restart(works_scope=works_scope)
        elif args.daemon_launchd_command == "status":
            status = manager.status(works_scope=works_scope)
        elif args.daemon_launchd_command == "stop":
            status = manager.stop(works_scope=works_scope)
        elif args.daemon_launchd_command == "uninstall":
            status = manager.uninstall(works_scope=works_scope)
        else:
            return 1
    except WorkspaceConfigError as exc:
        return emit_cli_error(
            str(exc),
            as_json=as_json,
            kind="autonomous-daemon-launchd-error",
            stop_reason="workspace-config-error",
        )
    except AutonomousDaemonLaunchdError as exc:
        return emit_cli_error(
            str(exc),
            as_json=as_json,
            kind="autonomous-daemon-launchd-error",
            stop_reason="launchd-error",
        )
    print_payload(status.to_dict(), as_json=as_json, formatter=manager.format_status)
    return 0


def autonomous_multi_daemon_cli(root_dir: Path, args: Any) -> int:
    as_json = bool(getattr(args, "as_json", False))
    try:
        if _optional_text(getattr(args, "work_id", None)):
            raise WorkspaceConfigError("Используй только один параметр: --work или --works.")
        works_scope = _optional_text(getattr(args, "works_scope", None)) or "all"
        work_ids = _resolve_daemon_work_ids(root_dir, works_scope)
        if args.daemon_command == "tick":
            payload = run_multi_work_daemon_tick(
                root_dir=root_dir,
                work_ids=work_ids,
                works_scope=works_scope,
                mode=args.mode,
                poll_seconds=args.poll_seconds,
                max_cycles=args.max_cycles,
                max_runtime_minutes=args.max_runtime_minutes,
            )
            _print_daemon_payload(payload, as_json=as_json)
            return 0
        if args.daemon_command == "run":
            payload = run_multi_work_daemon_foreground(
                root_dir=root_dir,
                work_ids=work_ids,
                works_scope=works_scope,
                mode=args.mode,
                poll_seconds=args.poll_seconds,
                max_cycles=args.max_cycles,
                max_runtime_minutes=args.max_runtime_minutes,
            )
            _print_daemon_payload(payload, as_json=as_json)
            return 1 if payload.get("stop_reason") == "daemon-already-running" else 0
        if args.daemon_command == "start":
            payload = start_multi_work_daemon_process(
                root_dir=root_dir,
                works_scope=works_scope,
                mode=args.mode,
                poll_seconds=args.poll_seconds,
                max_cycles=args.max_cycles,
                max_runtime_minutes=args.max_runtime_minutes,
            )
            _print_daemon_payload(payload, as_json=as_json)
            return 1 if payload.get("status") == "blocked" else 0
        if args.daemon_command == "status":
            payload = multi_daemon_status_payload(root_dir, works_scope=works_scope)
            _print_daemon_payload(payload, as_json=as_json)
            return 0
        if args.daemon_command == "stop":
            request_multi_daemon_stop(root_dir, works_scope=works_scope, reason=args.reason)
            payload = read_multi_daemon_stop_request(root_dir) or {}
            _print_daemon_payload(payload, as_json=as_json)
            return 0
    except WorkspaceConfigError as exc:
        return emit_cli_error(
            str(exc),
            as_json=as_json,
            kind="autonomous-daemon-error",
            stop_reason="workspace-config-error",
        )
    return 1


def _resolve_daemon_work_id(root_dir: Path, work_id: str) -> str:
    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id)
    return work.slug


def _resolve_daemon_work_ids(root_dir: Path, works_scope: str) -> list[str]:
    workspace = load_workspace_config(root_dir)
    return resolve_works_scope(workspace, works_scope)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _print_daemon_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    print_payload(payload, as_json=as_json)


def _format_autonomous_run_state(payload: dict[str, Any]) -> str:
    lines = [
        f"Autonomous run: {payload.get('status')}",
        f"Mode: {payload.get('mode')}",
        f"Readiness claim: {payload.get('readiness_claim')}",
    ]
    if payload.get("stop_reason"):
        lines.append(f"Stop reason: {payload.get('stop_reason')}")
    return "\n".join(lines)
