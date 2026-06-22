from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .autonomous_daemon import (
    DAEMON_STATE_VERSION,
    DAEMON_TERMINAL_STATUSES,
    _build_foreground_guards,
    _emit_daemon_terminal_alert,
    _resolve_stuck_after_minutes,
    evaluate_daemon_action,
    read_daemon_lock,
)
from .autonomous_planner import build_autonomous_plan
from .autonomous_runner import execute_autonomous_command
from .autonomous_runtime_errors import SchedulerWorkCandidateError, classify_scheduler_candidate_error
from .autonomous_runtime_store import (
    acquire_runtime_lock,
    build_lock_payload,
    build_stop_request_payload,
    detach_runtime_lock,
    inherited_runtime_lock_env,
    read_json_payload,
    release_runtime_lock,
    remove_runtime_file,
    runtime_file_path,
    runtime_lock_fd,
    write_json_payload,
)
from .ops_alerts import AlertSeverity, emit_alert
from .orchestrator import WorkflowOrchestrator
from .orchestrator_support import WorkflowError
from .resource_guards import ResourceGuardError
from .utils import parse_datetime, utc_now
from .workspace import WorkspaceConfig, WorkspaceConfigError, load_workspace_config

MULTI_WORK_DAEMON_ID = "multi-work"
MULTI_WORK_STATE_KIND = "autonomous-multi-work-daemon-state"
SCHEDULE_KIND = "autonomous-daemon-schedule"
SCHEDULER_TRACE_LIMIT = 25


def resolve_works_scope(workspace: WorkspaceConfig, works_scope: str | None) -> list[str]:
    scope = _optional_text(works_scope) or "active"
    if scope == "all":
        return sorted(workspace.works)
    if scope == "active":
        return [workspace.default_work]

    result: list[str] = []
    for raw_item in scope.split(","):
        work_id = raw_item.strip()
        if not work_id:
            continue
        if work_id not in workspace.works:
            raise WorkspaceConfigError(f"Не найден work `{work_id}` в {workspace.workspace_file}.")
        if work_id not in result:
            result.append(work_id)
    if not result:
        raise WorkspaceConfigError("Параметр --works не содержит ни одной work.")
    return result


def build_multi_work_schedule(
    *,
    root_dir: str | Path,
    work_ids: list[str] | tuple[str, ...],
    mode: str,
    works_scope: str = "custom",
    orchestrator: WorkflowOrchestrator | None = None,
    round_robin_cursor: str | None = None,
) -> dict[str, Any]:
    root_path = Path(root_dir).resolve()
    unique_work_ids = _unique_work_ids(work_ids)
    rotated_work_ids = _rotated_work_ids(unique_work_ids, round_robin_cursor)
    runner = orchestrator or WorkflowOrchestrator(root_path)
    runner.sync_active_run()
    active_runs = runner.store.list_active_runs()
    active_by_work = {
        str(item.get("work_id") or "").strip(): item for item in active_runs if str(item.get("work_id") or "").strip()
    }
    concurrency_available = len(active_runs) < 2

    candidates: list[dict[str, Any]] = []
    for work_id in unique_work_ids:
        active_run = active_by_work.get(work_id)
        if active_run is not None:
            candidates.append(_waiting_candidate(work_id, active_run=active_run, mode=mode))
            continue
        if not concurrency_available:
            candidates.append(
                _waiting_candidate(
                    work_id,
                    active_run=active_runs[0],
                    mode=mode,
                )
                | {"stop_reason": "workflow-concurrency-limit"}
            )
            continue
        try:
            candidate = _build_work_candidate(root_dir=root_path, orchestrator=runner, work_id=work_id, mode=mode)
        except Exception as exc:  # noqa: BLE001 — isolate one work without collapsing the whole schedule pass
            error = classify_scheduler_candidate_error(exc, work_id=work_id, stage="candidate-build")
            candidate = _candidate_from_runtime_error(work_id=work_id, mode=mode, error=error)
        candidates.append(candidate)
    ready = [candidate for candidate in candidates if candidate.get("status") == "ready"]
    selected = (
        sorted(ready, key=lambda candidate: _candidate_sort_key(candidate, rotated_work_ids=rotated_work_ids))[0]
        if ready
        else None
    )
    if selected is not None:
        status = "ready"
        stop_reason = None
    elif any(candidate.get("status") == "waiting" for candidate in candidates):
        status = "waiting"
        stop_reason = "no-work-available"
    else:
        status = "blocked"
        stop_reason = "no-safe-concrete-action"
    payload = _schedule_payload(
        mode=mode,
        works_scope=works_scope,
        work_ids=unique_work_ids,
        rotated_work_ids=rotated_work_ids,
        round_robin_cursor=round_robin_cursor,
        candidates=sorted(
            candidates, key=lambda candidate: _candidate_sort_key(candidate, rotated_work_ids=rotated_work_ids)
        ),
        selected=selected,
        status=status,
        stop_reason=stop_reason,
        active_run=active_runs[0] if active_runs else None,
    )
    payload["active_runs"] = active_runs
    payload["active_run_count"] = len(active_runs)
    payload["workflow_concurrency_limit"] = 2
    return payload


def multi_daemon_state_path(root_dir: str | Path) -> Path:
    return runtime_file_path(root_dir, f"{MULTI_WORK_DAEMON_ID}.daemon.json")


def multi_daemon_lock_path(root_dir: str | Path) -> Path:
    return runtime_file_path(root_dir, f"{MULTI_WORK_DAEMON_ID}.daemon.lock.json")


def multi_daemon_stop_path(root_dir: str | Path) -> Path:
    return runtime_file_path(root_dir, f"{MULTI_WORK_DAEMON_ID}.daemon.stop.json")


def multi_daemon_log_path(root_dir: str | Path) -> Path:
    return runtime_file_path(root_dir, f"{MULTI_WORK_DAEMON_ID}.daemon.log")


def read_multi_daemon_state(root_dir: str | Path) -> dict[str, Any] | None:
    return read_json_payload(multi_daemon_state_path(root_dir))


def write_multi_daemon_state(root_dir: str | Path, payload: dict[str, Any]) -> Path:
    normalized = _normalize_multi_daemon_state(root_dir, payload)
    path = multi_daemon_state_path(root_dir)
    write_json_payload(path, normalized)
    return path


def read_multi_daemon_lock(root_dir: str | Path) -> dict[str, Any] | None:
    return read_json_payload(multi_daemon_lock_path(root_dir))


def write_multi_daemon_lock(root_dir: str | Path, payload: dict[str, Any]) -> Path:
    extra_fields = {
        "works_scope": _optional_text(payload.get("works_scope")) or "all",
        **{key: payload[key] for key in ("transfer_pending", "launcher_pid") if payload.get(key) is not None},
    }
    lock = build_lock_payload(
        root_dir,
        MULTI_WORK_DAEMON_ID,
        version=DAEMON_STATE_VERSION,
        mode=_optional_text(payload.get("mode")),
        pid=_optional_int(payload.get("pid")),
        started_at=_optional_text(payload.get("started_at")),
        heartbeat_at=_optional_text(payload.get("heartbeat_at")),
        extra_fields=extra_fields,
    )
    path = multi_daemon_lock_path(root_dir)
    write_json_payload(path, lock)
    return path


def acquire_multi_daemon_lock(
    root_dir: str | Path,
    *,
    works_scope: str,
    mode: str,
    pid: int | None = None,
    stale_after_seconds: int = 300,
) -> dict[str, Any]:
    owner_pid = pid or os.getpid()
    path = multi_daemon_lock_path(root_dir)
    lock_result = acquire_runtime_lock(path, owner_pid=owner_pid)
    existing = lock_result.get("existing_lock")
    inherited = bool(lock_result.get("inherited"))
    recovered = False
    if not lock_result.get("acquired"):
        _emit_multi_lock_blocked_alert(
            owner_pid=owner_pid,
            existing=existing,
            mode=mode,
            works_scope=works_scope,
        )
        return _blocked_multi_lock_result(
            owner_pid=owner_pid,
            existing=existing,
            mode=mode,
            works_scope=works_scope,
        )
    if isinstance(existing, dict):
        existing_pid = _optional_int(existing.get("pid"))
        transfer_pending = bool(existing.get("transfer_pending"))
        if existing_pid == owner_pid and not transfer_pending:
            return heartbeat_multi_daemon_lock(root_dir, works_scope=works_scope, pid=owner_pid) | {
                "acquired": True,
                "recovered_stale_lock": False,
            }
        if not inherited and not _multi_daemon_lock_is_stale(existing, stale_after_seconds=stale_after_seconds):
            release_runtime_lock(path, remove_metadata=False)
            _emit_multi_lock_blocked_alert(
                owner_pid=owner_pid,
                existing=existing,
                mode=mode,
                works_scope=works_scope,
            )
            return _blocked_multi_lock_result(
                owner_pid=owner_pid,
                existing=existing,
                mode=mode,
                works_scope=works_scope,
            )
        recovered = not inherited

    now = utc_now()
    lock = {
        "kind": "autonomous-daemon-lock",
        "version": DAEMON_STATE_VERSION,
        "work_id": MULTI_WORK_DAEMON_ID,
        "works_scope": works_scope,
        "mode": mode,
        "root_dir": str(Path(root_dir).resolve()),
        "pid": owner_pid,
        "started_at": now,
        "heartbeat_at": now,
    }
    write_multi_daemon_lock(root_dir, lock)
    if recovered:
        emit_alert(
            severity=AlertSeverity.WARNING,
            code="daemon/stale-lock-recovered",
            message="Autonomous multi-work daemon recovered a stale lock and took ownership.",
            component="autonomous-daemon",
            work_id=MULTI_WORK_DAEMON_ID,
            details={
                "owner_pid": owner_pid,
                "stale_after_seconds": stale_after_seconds,
                "previous_pid": _optional_int(existing.get("pid")) if isinstance(existing, dict) else None,
                "mode": mode,
                "works_scope": works_scope,
            },
        )
    return {**lock, "acquired": True, "recovered_stale_lock": recovered, "readiness_claim": "none"}


def _blocked_multi_lock_result(
    *,
    owner_pid: int,
    existing: object,
    mode: str,
    works_scope: str,
) -> dict[str, Any]:
    return {
        "kind": "autonomous-daemon-lock-result",
        "version": DAEMON_STATE_VERSION,
        "acquired": False,
        "status": "blocked",
        "stop_reason": "daemon-already-running",
        "work_id": MULTI_WORK_DAEMON_ID,
        "works_scope": works_scope,
        "mode": mode,
        "pid": owner_pid,
        "existing_lock": existing if isinstance(existing, dict) else None,
        "readiness_claim": "none",
    }


def _emit_multi_lock_blocked_alert(
    *,
    owner_pid: int,
    existing: object,
    mode: str,
    works_scope: str,
) -> None:
    emit_alert(
        severity=AlertSeverity.WARNING,
        code="daemon/lock-blocked",
        message="Autonomous multi-work daemon refused to start: lock held by another pid.",
        component="autonomous-daemon",
        work_id=MULTI_WORK_DAEMON_ID,
        details={
            "owner_pid": owner_pid,
            "existing_pid": _optional_int(existing.get("pid")) if isinstance(existing, dict) else None,
            "mode": mode,
            "works_scope": works_scope,
        },
    )


def heartbeat_multi_daemon_lock(root_dir: str | Path, *, works_scope: str, pid: int | None = None) -> dict[str, Any]:
    lock = read_multi_daemon_lock(root_dir) or {}
    owner_pid = pid or _optional_int(lock.get("pid")) or os.getpid()
    updated = build_lock_payload(
        root_dir,
        MULTI_WORK_DAEMON_ID,
        version=DAEMON_STATE_VERSION,
        mode=_optional_text(lock.get("mode")),
        pid=owner_pid,
        started_at=_optional_text(lock.get("started_at")),
        heartbeat_at=utc_now(),
        extra_fields={"works_scope": works_scope},
    )
    write_multi_daemon_lock(root_dir, updated)
    return updated


def release_multi_daemon_lock(root_dir: str | Path) -> None:
    release_runtime_lock(multi_daemon_lock_path(root_dir))


def request_multi_daemon_stop(root_dir: str | Path, *, works_scope: str = "all", reason: str = "operator-stop") -> Path:
    payload = build_stop_request_payload(
        MULTI_WORK_DAEMON_ID,
        version=DAEMON_STATE_VERSION,
        reason=reason,
        extra_fields={"works_scope": works_scope},
    )
    path = multi_daemon_stop_path(root_dir)
    write_json_payload(path, payload)
    return path


def read_multi_daemon_stop_request(root_dir: str | Path) -> dict[str, Any] | None:
    return read_json_payload(multi_daemon_stop_path(root_dir))


def clear_multi_daemon_stop_request(root_dir: str | Path) -> None:
    remove_runtime_file(multi_daemon_stop_path(root_dir))


def multi_daemon_status_payload(root_dir: str | Path, *, works_scope: str = "all") -> dict[str, Any]:
    state = read_multi_daemon_state(root_dir)
    if not state:
        state = _normalize_multi_daemon_state(
            root_dir,
            {
                "status": "not-started",
                "mode": "autonomous-full",
                "works_scope": works_scope,
                "work_ids": [],
                "work_count": 0,
                "cycle_count": 0,
            },
        )
    lock = read_multi_daemon_lock(root_dir)
    stop_request = read_multi_daemon_stop_request(root_dir)
    state["lock"] = lock if isinstance(lock, dict) else None
    state["stop_request"] = stop_request if isinstance(stop_request, dict) else None
    state["assessment_scope"] = _assessment_scope()
    state["readiness_claim"] = "none"
    return state


def run_multi_work_daemon_tick(
    *,
    root_dir: str | Path,
    work_ids: list[str] | tuple[str, ...],
    works_scope: str = "all",
    mode: str = "autonomous-full",
    max_cycles: int = 50,
    poll_seconds: int = 30,
    max_runtime_minutes: int = 240,
    pid: int | None = None,
    lock_already_acquired: bool = False,
    keep_lock: bool = False,
) -> dict[str, Any]:
    owner_pid = pid or os.getpid()
    lock_result: dict[str, Any] | None = None
    if not lock_already_acquired:
        lock_result = acquire_multi_daemon_lock(root_dir, works_scope=works_scope, mode=mode, pid=owner_pid)
        if not lock_result.get("acquired"):
            return _write_multi_daemon_cycle_state(
                root_dir=root_dir,
                mode=mode,
                works_scope=works_scope,
                work_ids=list(work_ids),
                status="blocked",
                pid=owner_pid,
                max_cycles=max_cycles,
                poll_seconds=poll_seconds,
                max_runtime_minutes=max_runtime_minutes,
                stop_reason="daemon-already-running",
                schedule=None,
                result=None,
                increment_cycle=True,
            )
    else:
        heartbeat_multi_daemon_lock(root_dir, works_scope=works_scope, pid=owner_pid)

    try:
        stop_request = read_multi_daemon_stop_request(root_dir)
        if isinstance(stop_request, dict):
            stop_reason = _optional_text(stop_request.get("reason")) or "operator-stop"
            clear_multi_daemon_stop_request(root_dir)
            return _write_multi_daemon_cycle_state(
                root_dir=root_dir,
                mode=mode,
                works_scope=works_scope,
                work_ids=list(work_ids),
                status="stopped",
                pid=owner_pid,
                max_cycles=max_cycles,
                poll_seconds=poll_seconds,
                max_runtime_minutes=max_runtime_minutes,
                stop_reason=stop_reason,
                schedule=None,
                result=None,
            )

        orchestrator = WorkflowOrchestrator(root_dir)
        previous = read_multi_daemon_state(root_dir) or {}
        schedule = build_multi_work_schedule(
            root_dir=root_dir,
            work_ids=list(work_ids),
            mode=mode,
            works_scope=works_scope,
            orchestrator=orchestrator,
            round_robin_cursor=_optional_text(previous.get("round_robin_cursor")),
        )
        selected_work_id = _optional_text(schedule.get("selected_work_id"))
        selected_command = _optional_text(schedule.get("selected_command"))
        selected_decision = (
            schedule.get("selected_decision") if isinstance(schedule.get("selected_decision"), dict) else None
        )
        if schedule.get("status") == "waiting":
            return _write_multi_daemon_cycle_state(
                root_dir=root_dir,
                mode=mode,
                works_scope=works_scope,
                work_ids=list(work_ids),
                status="waiting",
                pid=owner_pid,
                max_cycles=max_cycles,
                poll_seconds=poll_seconds,
                max_runtime_minutes=max_runtime_minutes,
                stop_reason=_optional_text(schedule.get("stop_reason")) or "waiting",
                schedule=schedule,
                result={"status": "waiting", "active_run": schedule.get("active_run")},
            )
        if (
            not selected_work_id
            or not selected_command
            or not selected_decision
            or selected_decision.get("decision") != "allowed"
        ):
            return _write_multi_daemon_cycle_state(
                root_dir=root_dir,
                mode=mode,
                works_scope=works_scope,
                work_ids=list(work_ids),
                status="blocked",
                pid=owner_pid,
                max_cycles=max_cycles,
                poll_seconds=poll_seconds,
                max_runtime_minutes=max_runtime_minutes,
                stop_reason=_optional_text(schedule.get("stop_reason")) or "no-safe-concrete-action",
                schedule=schedule,
                result=None,
            )

        result = execute_autonomous_command(orchestrator, selected_command, work_id=selected_work_id)
        result_status = _optional_text(result.get("status"))
        if result_status == "started-run":
            status = "waiting"
            stop_reason = "step-started"
        elif result_status == "completed" and selected_command.startswith("export-"):
            status = "completed"
            stop_reason = "terminal-export"
        elif result_status == "completed":
            status = "running"
            stop_reason = "step-completed"
        else:
            status = "failed" if result_status not in {"skipped", None} else "blocked"
            stop_reason = _optional_text(result.get("reason")) or "execution-stopped"

        return _write_multi_daemon_cycle_state(
            root_dir=root_dir,
            mode=mode,
            works_scope=works_scope,
            work_ids=list(work_ids),
            status=status,
            pid=owner_pid,
            max_cycles=max_cycles,
            poll_seconds=poll_seconds,
            max_runtime_minutes=max_runtime_minutes,
            stop_reason=stop_reason,
            schedule=schedule,
            result=result,
        )
    finally:
        if not keep_lock and not lock_already_acquired and lock_result and lock_result.get("acquired"):
            release_multi_daemon_lock(root_dir)


def run_multi_work_daemon_foreground(
    *,
    root_dir: str | Path,
    work_ids: list[str] | tuple[str, ...],
    works_scope: str = "all",
    mode: str = "autonomous-full",
    poll_seconds: int = 30,
    max_cycles: int = 50,
    max_runtime_minutes: int = 240,
    pid: int | None = None,
    sleep_between_cycles: bool = True,
    stuck_after_minutes: int | None = None,
) -> dict[str, Any]:
    owner_pid = pid or os.getpid()
    lock = acquire_multi_daemon_lock(root_dir, works_scope=works_scope, mode=mode, pid=owner_pid)
    if not lock.get("acquired"):
        return _write_multi_daemon_cycle_state(
            root_dir=root_dir,
            mode=mode,
            works_scope=works_scope,
            work_ids=list(work_ids),
            status="blocked",
            pid=owner_pid,
            max_cycles=max_cycles,
            poll_seconds=poll_seconds,
            max_runtime_minutes=max_runtime_minutes,
            stop_reason="daemon-already-running",
            schedule=None,
            result=None,
        )

    try:
        started_at = datetime.now(UTC)
        guards = _build_foreground_guards(
            max_runtime_minutes=max_runtime_minutes,
            stuck_after_minutes=_resolve_stuck_after_minutes(stuck_after_minutes),
        )
        guards.start()
        last_command: str | None = None
        state = _write_multi_daemon_cycle_state(
            root_dir=root_dir,
            mode=mode,
            works_scope=works_scope,
            work_ids=list(work_ids),
            status="running",
            pid=owner_pid,
            max_cycles=max_cycles,
            poll_seconds=poll_seconds,
            max_runtime_minutes=max_runtime_minutes,
            stop_reason=None,
            schedule=None,
            result=None,
            increment_cycle=False,
        )
    except Exception as exc:  # noqa: BLE001 — acquired locks must be released on setup failures
        emit_alert(
            severity=AlertSeverity.CRITICAL,
            code="daemon/unhandled-exception",
            message=f"Autonomous multi-work daemon crashed during setup: {type(exc).__name__}: {exc}",
            component="autonomous-daemon",
            work_id=MULTI_WORK_DAEMON_ID,
            details={
                "mode": mode,
                "works_scope": works_scope,
                "traceback": traceback.format_exc(limit=6),
            },
        )
        release_multi_daemon_lock(root_dir)
        raise
    try:
        while True:
            current_cycles = int(state.get("cycle_count") or 0)
            elapsed = datetime.now(UTC) - started_at
            if current_cycles >= max_cycles:
                state = _write_multi_daemon_cycle_state(
                    root_dir=root_dir,
                    mode=mode,
                    works_scope=works_scope,
                    work_ids=list(work_ids),
                    status="stopped",
                    pid=owner_pid,
                    max_cycles=max_cycles,
                    poll_seconds=poll_seconds,
                    max_runtime_minutes=max_runtime_minutes,
                    stop_reason="max-cycles",
                    schedule=None,
                    result=None,
                    increment_cycle=False,
                )
                _emit_daemon_terminal_alert(
                    work_id=MULTI_WORK_DAEMON_ID,
                    reason="max-cycles",
                    details={
                        "mode": mode,
                        "works_scope": works_scope,
                        "max_cycles": max_cycles,
                        "cycle_count": current_cycles,
                    },
                )
                break
            if elapsed.total_seconds() >= max_runtime_minutes * 60:
                state = _write_multi_daemon_cycle_state(
                    root_dir=root_dir,
                    mode=mode,
                    works_scope=works_scope,
                    work_ids=list(work_ids),
                    status="stopped",
                    pid=owner_pid,
                    max_cycles=max_cycles,
                    poll_seconds=poll_seconds,
                    max_runtime_minutes=max_runtime_minutes,
                    stop_reason="max-runtime",
                    schedule=None,
                    result=None,
                    increment_cycle=False,
                )
                _emit_daemon_terminal_alert(
                    work_id=MULTI_WORK_DAEMON_ID,
                    reason="max-runtime",
                    details={
                        "mode": mode,
                        "works_scope": works_scope,
                        "max_runtime_minutes": max_runtime_minutes,
                    },
                )
                break
            try:
                guards.check()
            except ResourceGuardError as guard_error:
                state = _write_multi_daemon_cycle_state(
                    root_dir=root_dir,
                    mode=mode,
                    works_scope=works_scope,
                    work_ids=list(work_ids),
                    status="stopped",
                    pid=owner_pid,
                    max_cycles=max_cycles,
                    poll_seconds=poll_seconds,
                    max_runtime_minutes=max_runtime_minutes,
                    stop_reason=guard_error.code,
                    schedule=None,
                    result={"status": "stopped", "guard": guard_error.to_dict()},
                    increment_cycle=False,
                )
                emit_alert(
                    severity=AlertSeverity.CRITICAL,
                    code=f"daemon/{guard_error.code}",
                    message=str(guard_error),
                    component="autonomous-daemon",
                    work_id=MULTI_WORK_DAEMON_ID,
                    details={"mode": mode, "works_scope": works_scope, **guard_error.details},
                )
                break
            state = run_multi_work_daemon_tick(
                root_dir=root_dir,
                work_ids=list(work_ids),
                works_scope=works_scope,
                mode=mode,
                max_cycles=max_cycles,
                poll_seconds=poll_seconds,
                max_runtime_minutes=max_runtime_minutes,
                pid=owner_pid,
                lock_already_acquired=True,
                keep_lock=True,
            )
            cycle_command = _optional_text(state.get("selected_command"))
            if cycle_command and cycle_command != last_command:
                guards.checkpoint(f"command:{cycle_command}")
                last_command = cycle_command
            if state.get("status") in DAEMON_TERMINAL_STATUSES:
                break
            if sleep_between_cycles and poll_seconds > 0:
                time.sleep(poll_seconds)
    except Exception as exc:  # noqa: BLE001 — long-running loop must not vanish silently
        state = _write_multi_daemon_cycle_state(
            root_dir=root_dir,
            mode=mode,
            works_scope=works_scope,
            work_ids=list(work_ids),
            status="failed",
            pid=owner_pid,
            max_cycles=max_cycles,
            poll_seconds=poll_seconds,
            max_runtime_minutes=max_runtime_minutes,
            stop_reason="unhandled-exception",
            schedule=None,
            result={
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            increment_cycle=False,
        )
        emit_alert(
            severity=AlertSeverity.CRITICAL,
            code="daemon/unhandled-exception",
            message=f"Autonomous multi-work daemon crashed: {type(exc).__name__}: {exc}",
            component="autonomous-daemon",
            work_id=MULTI_WORK_DAEMON_ID,
            details={
                "mode": mode,
                "works_scope": works_scope,
                "traceback": traceback.format_exc(limit=6),
            },
        )
        raise
    finally:
        release_multi_daemon_lock(root_dir)
    return state


def start_multi_work_daemon_process(
    *,
    root_dir: str | Path,
    works_scope: str = "all",
    mode: str = "autonomous-full",
    poll_seconds: int = 30,
    max_cycles: int = 50,
    max_runtime_minutes: int = 240,
    stuck_after_minutes: int | None = None,
) -> dict[str, Any]:
    launcher_pid = os.getpid()
    lock = acquire_multi_daemon_lock(
        root_dir,
        works_scope=works_scope,
        mode=mode,
        pid=launcher_pid,
        stale_after_seconds=max(60, poll_seconds * 3),
    )
    if not lock.get("acquired"):
        return _normalize_multi_daemon_state(
            root_dir,
            {
                "status": "blocked",
                "mode": mode,
                "works_scope": works_scope,
                "pid": launcher_pid,
                "stop_reason": "daemon-already-running",
                "last_schedule": None,
            },
        )

    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(repo_root) if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    child_code = (
        "from academic_engine.work_cli import main; "
        "import sys; "
        "raise SystemExit(main(sys.argv[2:], root_dir=sys.argv[1]))"
    )
    command = [
        sys.executable,
        "-c",
        child_code,
        str(Path(root_dir).resolve()),
        "autonomous",
        "daemon",
        "run",
        "--works",
        works_scope,
        "--mode",
        mode,
        "--poll-seconds",
        str(poll_seconds),
        "--max-cycles",
        str(max_cycles),
        "--max-runtime-minutes",
        str(max_runtime_minutes),
    ]
    if stuck_after_minutes is not None:
        command.extend(["--stuck-after-minutes", str(stuck_after_minutes)])
    command.append("--json")

    lock_path = multi_daemon_lock_path(root_dir)
    lock_fd = runtime_lock_fd(lock_path)
    if lock_fd is None:
        release_multi_daemon_lock(root_dir)
        raise RuntimeError("Autonomous multi-work daemon lock fd is unavailable after acquisition.")
    ready_read_fd, ready_write_fd = os.pipe()
    env.update(inherited_runtime_lock_env(lock_path, ready_fd=ready_read_fd))
    write_multi_daemon_lock(
        root_dir,
        {
            "works_scope": works_scope,
            "mode": mode,
            "pid": launcher_pid,
            "started_at": lock.get("started_at"),
            "heartbeat_at": utc_now(),
            "transfer_pending": True,
            "launcher_pid": launcher_pid,
        },
    )
    log_path = multi_daemon_log_path(root_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("ab") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=str(Path(root_dir).resolve()),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=(lock_fd, ready_read_fd),
            )
        write_multi_daemon_lock(
            root_dir,
            {
                "works_scope": works_scope,
                "mode": mode,
                "pid": process.pid,
                "started_at": lock.get("started_at"),
                "heartbeat_at": utc_now(),
                "transfer_pending": True,
                "launcher_pid": launcher_pid,
            },
        )
        now = utc_now()
        work_ids = _resolve_work_ids_for_state(root_dir, works_scope)
        state = _normalize_multi_daemon_state(
            root_dir,
            {
                "status": "running",
                "mode": mode,
                "works_scope": works_scope,
                "work_ids": work_ids,
                "pid": process.pid,
                "started_at": now,
                "heartbeat_at": now,
                "cycle_count": 0,
                "max_cycles": max_cycles,
                "poll_seconds": poll_seconds,
                "max_runtime_minutes": max_runtime_minutes,
            },
        )
        write_multi_daemon_state(root_dir, state)
        os.write(ready_write_fd, b"1")
    except Exception:
        release_multi_daemon_lock(root_dir)
        raise
    finally:
        os.close(ready_read_fd)
        os.close(ready_write_fd)
    detach_runtime_lock(lock_path)
    return state


def _build_work_candidate(
    *,
    root_dir: Path,
    orchestrator: WorkflowOrchestrator,
    work_id: str,
    mode: str,
) -> dict[str, Any]:
    single_lock = read_daemon_lock(root_dir, work_id)
    if isinstance(single_lock, dict):
        decision = _blocked_decision(
            mode=mode,
            stop_reason="single-work-daemon-running",
            reason="A single-work daemon lock exists for this work.",
            blocking_categories=["daemon-lock"],
        )
        return _candidate_payload(
            work_id=work_id,
            status="waiting",
            plan=None,
            decision=decision,
            command=None,
            priority=0,
            stop_reason="single-work-daemon-running",
            known_blocker_categories=[],
            single_work_lock=single_lock,
        )

    handled_errors = (
        WorkflowError,
        WorkspaceConfigError,
        FileNotFoundError,
        NotADirectoryError,
        PermissionError,
        OSError,
        RuntimeError,
        ValueError,
    )

    try:
        work_state = orchestrator.get_work_state(work_id=work_id)
    except handled_errors as exc:
        return _candidate_from_runtime_error(
            work_id=work_id,
            mode=mode,
            error=classify_scheduler_candidate_error(exc, work_id=work_id, stage="work-state"),
        )

    try:
        plan = build_autonomous_plan(work_state=work_state, mode=mode, max_steps=3).to_dict()
        first_blocked: tuple[dict[str, Any], dict[str, Any], int] | None = None
        for step in _plan_steps(plan):
            action = _action_from_plan_step(step)
            decision = evaluate_daemon_action(work_state=work_state, action=action, mode=mode)
            priority = _action_priority(work_state, action)
            command = _optional_text(decision.get("safe_command")) or _optional_text(decision.get("command"))
            if decision.get("decision") == "allowed" and command:
                return _candidate_payload(
                    work_id=work_id,
                    status="ready",
                    plan=plan,
                    decision=decision,
                    command=command,
                    priority=priority,
                    stop_reason=None,
                    known_blocker_categories=_known_blocker_categories(work_state),
                    single_work_lock=None,
                )
            if first_blocked is None:
                first_blocked = (decision, action or {}, priority)
    except handled_errors as exc:
        return _candidate_from_runtime_error(
            work_id=work_id,
            mode=mode,
            error=classify_scheduler_candidate_error(exc, work_id=work_id, stage="candidate-evaluation"),
            known_blocker_categories=_known_blocker_categories(work_state),
        )

    if first_blocked is None:
        decision = _blocked_decision(
            mode=mode,
            stop_reason="no-safe-concrete-action",
            reason=_optional_text(plan.get("stop_reason")) or "No safe concrete action is available.",
            blocking_categories=["no-action"],
        )
        priority = 9999
    else:
        decision, _, priority = first_blocked
    stop_reason = (
        _optional_text(decision.get("stop_reason"))
        or _optional_text(plan.get("stop_reason"))
        or "no-safe-concrete-action"
    )
    status = "waiting" if stop_reason == "active-run" else "blocked"
    return _candidate_payload(
        work_id=work_id,
        status=status,
        plan=plan,
        decision=decision,
        command=_optional_text(decision.get("command")),
        priority=priority,
        stop_reason=stop_reason,
        known_blocker_categories=_known_blocker_categories(work_state),
        single_work_lock=None,
    )


def _candidate_from_runtime_error(
    *,
    work_id: str,
    mode: str,
    error: SchedulerWorkCandidateError,
    known_blocker_categories: list[str] | None = None,
) -> dict[str, Any]:
    decision = _blocked_decision(
        mode=mode,
        stop_reason=error.code,
        reason=str(error),
        blocking_categories=list(error.blocking_categories),
    )
    return _candidate_payload(
        work_id=work_id,
        status="blocked",
        plan=None,
        decision=decision,
        command=None,
        priority=9999,
        stop_reason=error.code,
        known_blocker_categories=known_blocker_categories or [],
        single_work_lock=None,
    )


def _schedule_payload(
    *,
    mode: str,
    works_scope: str,
    work_ids: list[str],
    rotated_work_ids: list[str],
    round_robin_cursor: str | None,
    candidates: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    status: str,
    stop_reason: str | None,
    active_run: dict[str, Any] | None,
) -> dict[str, Any]:
    selected_decision = (
        selected.get("decision") if isinstance(selected, dict) and isinstance(selected.get("decision"), dict) else None
    )
    risk_controls = _risk_controls()
    return {
        "kind": SCHEDULE_KIND,
        "version": "v1",
        "status": status,
        "mode": mode,
        "works_scope": works_scope,
        "work_ids": work_ids,
        "work_count": len(work_ids),
        "candidates": candidates,
        "selected_work_id": selected.get("work_id") if selected else None,
        "selected_command": selected.get("command") if selected else None,
        "selected_decision": selected_decision,
        "round_robin": {
            "cursor_work_id": _optional_text(round_robin_cursor),
            "ordered_work_ids": rotated_work_ids,
            "next_cursor_work_id": selected.get("work_id") if selected else _optional_text(round_robin_cursor),
        },
        "blocked": [item["work_id"] for item in candidates if item.get("status") == "blocked"],
        "waiting": [item["work_id"] for item in candidates if item.get("status") == "waiting"],
        "stop_reason": stop_reason,
        "active_run": active_run,
        "assessment_scope": _assessment_scope(),
        "risk_controls": risk_controls,
        "readiness_claim": "none",
    }


def _candidate_payload(
    *,
    work_id: str,
    status: str,
    plan: dict[str, Any] | None,
    decision: dict[str, Any],
    command: str | None,
    priority: int,
    stop_reason: str | None,
    known_blocker_categories: list[str],
    single_work_lock: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "work_id": work_id,
        "status": status,
        "priority": priority,
        "command": command,
        "decision": decision,
        "stop_reason": stop_reason,
        "plan": plan,
        "known_blocker_categories": known_blocker_categories,
        "single_work_lock": single_work_lock,
        "readiness_claim": "none",
    }


def _waiting_candidate(work_id: str, *, active_run: dict[str, Any], mode: str) -> dict[str, Any]:
    decision = _blocked_decision(
        mode=mode,
        stop_reason="active-run",
        reason="A workflow run is already active in this workspace.",
        blocking_categories=["active-run"],
    )
    return _candidate_payload(
        work_id=work_id,
        status="waiting",
        plan=None,
        decision=decision,
        command=None,
        priority=0,
        stop_reason="active-run",
        known_blocker_categories=[],
        single_work_lock=None,
    ) | {"active_run": active_run}


def _blocked_decision(
    *,
    mode: str,
    stop_reason: str,
    reason: str,
    blocking_categories: list[str],
) -> dict[str, Any]:
    return {
        "kind": "autonomous-daemon-decision",
        "version": DAEMON_STATE_VERSION,
        "mode": mode,
        "decision": "blocked",
        "stop_reason": stop_reason,
        "reason": reason,
        "command": None,
        "safe_command": None,
        "intent": None,
        "lane": None,
        "target": None,
        "action_id": None,
        "blocking_categories": blocking_categories,
        "blocking_gate_ids": [],
        "readiness_claim": "none",
    }


def _write_multi_daemon_cycle_state(
    *,
    root_dir: str | Path,
    mode: str,
    works_scope: str,
    work_ids: list[str],
    status: str,
    pid: int,
    max_cycles: int,
    poll_seconds: int,
    max_runtime_minutes: int,
    stop_reason: str | None,
    schedule: dict[str, Any] | None,
    result: dict[str, Any] | None,
    increment_cycle: bool = True,
) -> dict[str, Any]:
    previous = read_multi_daemon_state(root_dir) or {}
    cycle_count = int(previous.get("cycle_count") or 0) + (1 if increment_cycle else 0)
    now = utc_now()
    selected_work_id = _optional_text(schedule.get("selected_work_id")) if isinstance(schedule, dict) else None
    selected_command = _optional_text(schedule.get("selected_command")) if isinstance(schedule, dict) else None
    selected_decision = (
        schedule.get("selected_decision")
        if isinstance(schedule, dict) and isinstance(schedule.get("selected_decision"), dict)
        else None
    )
    round_robin = (
        schedule.get("round_robin")
        if isinstance(schedule, dict) and isinstance(schedule.get("round_robin"), dict)
        else {}
    )
    round_robin_cursor = _optional_text(round_robin.get("next_cursor_work_id")) or _optional_text(
        previous.get("round_robin_cursor")
    )
    trace_item = {
        "cycle": cycle_count,
        "timestamp": now,
        "status": status,
        "stop_reason": stop_reason,
        "selected_work_id": selected_work_id,
        "selected_command": selected_command,
        "result": result,
        "readiness_claim": "none",
    }
    trace = previous.get("cycle_trace") if isinstance(previous.get("cycle_trace"), list) else []
    if increment_cycle:
        trace = [*trace, trace_item][-SCHEDULER_TRACE_LIMIT:]
    payload = {
        **previous,
        "kind": MULTI_WORK_STATE_KIND,
        "version": DAEMON_STATE_VERSION,
        "status": status,
        "work_id": MULTI_WORK_DAEMON_ID,
        "works_scope": works_scope,
        "work_ids": list(work_ids),
        "work_count": len(work_ids),
        "mode": mode,
        "pid": pid,
        "started_at": _optional_text(previous.get("started_at")) or now,
        "heartbeat_at": now,
        "finished_at": now if status in DAEMON_TERMINAL_STATUSES else None,
        "cycle_count": cycle_count,
        "max_cycles": max_cycles,
        "poll_seconds": poll_seconds,
        "max_runtime_minutes": max_runtime_minutes,
        "selected_work_id": selected_work_id,
        "selected_command": selected_command,
        "selected_decision": selected_decision,
        "round_robin_cursor": round_robin_cursor,
        "last_schedule": schedule,
        "last_result": result,
        "cycle_trace": trace,
        "stop_reason": stop_reason,
        "blocked": schedule.get("blocked", []) if isinstance(schedule, dict) else [],
        "waiting": schedule.get("waiting", []) if isinstance(schedule, dict) else [],
        "assessment_scope": _assessment_scope(),
        "risk_controls": _risk_controls(),
        "readiness_claim": "none",
    }
    write_multi_daemon_state(root_dir, payload)
    return payload


def _normalize_multi_daemon_state(root_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    work_ids = payload.get("work_ids") if isinstance(payload.get("work_ids"), list) else []
    schedule = payload.get("last_schedule") if isinstance(payload.get("last_schedule"), dict) else None
    selected_decision = payload.get("selected_decision") if isinstance(payload.get("selected_decision"), dict) else None
    if selected_decision is None and isinstance(schedule, dict) and isinstance(schedule.get("selected_decision"), dict):
        selected_decision = schedule["selected_decision"]
    normalized = {
        **payload,
        "kind": MULTI_WORK_STATE_KIND,
        "version": DAEMON_STATE_VERSION,
        "status": _optional_text(payload.get("status")) or "not-started",
        "work_id": MULTI_WORK_DAEMON_ID,
        "works_scope": _optional_text(payload.get("works_scope")) or "all",
        "work_ids": work_ids,
        "work_count": _optional_int(payload.get("work_count")) or len(work_ids),
        "mode": _optional_text(payload.get("mode")) or "autonomous-full",
        "pid": _optional_int(payload.get("pid")),
        "started_at": _optional_text(payload.get("started_at")) or now,
        "heartbeat_at": _optional_text(payload.get("heartbeat_at")) or now,
        "finished_at": _optional_text(payload.get("finished_at")),
        "cycle_count": _optional_int(payload.get("cycle_count")) or 0,
        "max_cycles": _optional_int(payload.get("max_cycles")),
        "poll_seconds": _optional_int(payload.get("poll_seconds")),
        "max_runtime_minutes": _optional_int(payload.get("max_runtime_minutes")),
        "selected_work_id": _optional_text(payload.get("selected_work_id"))
        or (_optional_text(schedule.get("selected_work_id")) if isinstance(schedule, dict) else None),
        "selected_command": _optional_text(payload.get("selected_command"))
        or (_optional_text(schedule.get("selected_command")) if isinstance(schedule, dict) else None),
        "selected_decision": selected_decision,
        "round_robin_cursor": _optional_text(payload.get("round_robin_cursor")),
        "last_schedule": schedule,
        "last_result": payload.get("last_result") if isinstance(payload.get("last_result"), dict) else None,
        "cycle_trace": (payload.get("cycle_trace") if isinstance(payload.get("cycle_trace"), list) else [])[
            -SCHEDULER_TRACE_LIMIT:
        ],
        "stop_reason": _optional_text(payload.get("stop_reason")),
        "blocked": list(payload.get("blocked") or []) if isinstance(payload.get("blocked"), list) else [],
        "waiting": list(payload.get("waiting") or []) if isinstance(payload.get("waiting"), list) else [],
        "assessment_scope": _assessment_scope(),
        "risk_controls": payload.get("risk_controls")
        if isinstance(payload.get("risk_controls"), dict)
        else _risk_controls(),
        "readiness_claim": "none",
    }
    return normalized


def _assessment_scope() -> dict[str, Any]:
    return {
        "depth": "signals-only",
        "readiness_claim": "none",
        "does_not_replace": [
            "source-verification",
            "citation-checking",
            "standards-review",
            "human-verdict",
        ],
    }


def _risk_controls() -> dict[str, Any]:
    return {
        "quality_control_mode": "delegated",
        "scheduler_role": "admission-control",
        "quality_authorities": {
            "sources": [
                "source-verifier",
                "academic-source-verifier",
                "primary-support-blockers",
            ],
            "citations": [
                "citation-checker",
                "academic-citation-checker",
                "contract-gates",
            ],
            "text": [
                "argument-critic",
                "academic-counterargument-critic",
                "academic-submission-evaluator",
            ],
        },
        "does_not_judge_directly": [
            "source-quality",
            "citation-quality",
            "text-quality",
        ],
        "manual_target_required": True,
        "automatic_submission_ready": False,
        "single_flight_global": True,
        "max_actions_per_tick": 1,
        "readiness_claim": "none",
    }


def _resolve_work_ids_for_state(root_dir: str | Path, works_scope: str) -> list[str]:
    try:
        workspace = load_workspace_config(root_dir)
        return resolve_works_scope(workspace, works_scope)
    except WorkspaceConfigError:
        return []


def _plan_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    return [step for step in steps if isinstance(step, dict)]


def _action_from_plan_step(step: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(step, dict):
        return None
    policy = step.get("policy") if isinstance(step.get("policy"), dict) else {}
    payload = {
        "command": step.get("command") or policy.get("command"),
        "action_id": step.get("action_id") or policy.get("action_id"),
        "intent": policy.get("intent"),
        "lane": policy.get("lane"),
        "target": policy.get("target"),
        "policy": policy,
    }
    if isinstance(step.get("finalization_check"), dict):
        payload["finalization_check"] = step["finalization_check"]
    return payload


def _action_priority(work_state: dict[str, Any], action: dict[str, Any] | None) -> int:
    action_id = _optional_text((action or {}).get("action_id"))
    command = _optional_text((action or {}).get("command"))
    for candidate in _state_actions(work_state):
        if action_id and candidate.get("action_id") == action_id:
            return _optional_int(candidate.get("priority")) or 999
        if command and candidate.get("command") == command:
            return _optional_int(candidate.get("priority")) or 999
    return 999


def _state_actions(work_state: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in ("suggested_next_action", "work_continuation_action"):
        value = work_state.get(key)
        if isinstance(value, dict):
            result.append(value)
    for value in work_state.get("next_actions") or []:
        if isinstance(value, dict):
            result.append(value)
    return result


def _known_blocker_categories(work_state: dict[str, Any]) -> list[str]:
    blockers = work_state.get("known_blockers") if isinstance(work_state.get("known_blockers"), list) else []
    categories = {
        str(blocker.get("category")).strip()
        for blocker in blockers
        if isinstance(blocker, dict) and str(blocker.get("category") or "").strip()
    }
    return sorted(categories)


def _candidate_sort_key(
    candidate: dict[str, Any], *, rotated_work_ids: list[str] | None = None
) -> tuple[int, int, int, str]:
    status_rank = {"ready": 0, "waiting": 1, "blocked": 2}.get(str(candidate.get("status") or ""), 9)
    work_id = str(candidate.get("work_id") or "")
    if rotated_work_ids and work_id in rotated_work_ids:
        work_rank = rotated_work_ids.index(work_id)
    else:
        work_rank = 9999
    return (status_rank, _optional_int(candidate.get("priority")) or 9999, work_rank, work_id)


def _unique_work_ids(work_ids: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for raw in work_ids:
        work_id = str(raw or "").strip()
        if work_id and work_id not in result:
            result.append(work_id)
    return result


def _rotated_work_ids(work_ids: list[str], cursor: str | None) -> list[str]:
    if not work_ids:
        return []
    cursor_text = _optional_text(cursor)
    if cursor_text not in work_ids:
        return list(work_ids)
    index = work_ids.index(str(cursor_text))
    return [*work_ids[index + 1 :], *work_ids[: index + 1]]


def _multi_daemon_lock_is_stale(lock: dict[str, Any], *, stale_after_seconds: int) -> bool:
    pid = _optional_int(lock.get("pid"))
    if pid is not None and not _pid_is_alive(pid):
        return True
    heartbeat_at = parse_datetime(_optional_text(lock.get("heartbeat_at")))
    age = datetime.now(UTC) - heartbeat_at
    return age.total_seconds() > stale_after_seconds


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
