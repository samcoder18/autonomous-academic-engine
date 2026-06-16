from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .autonomous_planner import build_autonomous_plan
from .autonomous_runner import execute_autonomous_command
from .autonomous_runtime_store import (
    acquire_runtime_lock,
    autonomous_runtime_dir,
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
from .resource_guards import ResourceGuardError, RunGuards, StuckDetector, TimeoutBudget
from .utils import parse_datetime, utc_now

DAEMON_STATE_VERSION = "v1"
DAEMON_TRACE_LIMIT = 25
DAEMON_TERMINAL_STATUSES = {"blocked", "completed", "failed", "stopped"}
PLACEHOLDER_RE = re.compile(r"<[^>]+>")


def daemon_runtime_dir(root_dir: str | Path) -> Path:
    return autonomous_runtime_dir(root_dir)


def daemon_state_path(root_dir: str | Path, work_id: str) -> Path:
    return runtime_file_path(root_dir, f"{work_id}.daemon.json")


def daemon_lock_path(root_dir: str | Path, work_id: str) -> Path:
    return runtime_file_path(root_dir, f"{work_id}.daemon.lock.json")


def daemon_stop_path(root_dir: str | Path, work_id: str) -> Path:
    return runtime_file_path(root_dir, f"{work_id}.daemon.stop.json")


def daemon_log_path(root_dir: str | Path, work_id: str) -> Path:
    return runtime_file_path(root_dir, f"{work_id}.daemon.log")


def read_daemon_state(root_dir: str | Path, work_id: str) -> dict[str, Any] | None:
    return read_json_payload(daemon_state_path(root_dir, work_id))


def write_daemon_state(root_dir: str | Path, work_id: str, payload: dict[str, Any]) -> Path:
    normalized = _normalize_daemon_state(root_dir, work_id, payload)
    path = daemon_state_path(root_dir, work_id)
    write_json_payload(path, normalized)
    return path


def read_daemon_lock(root_dir: str | Path, work_id: str) -> dict[str, Any] | None:
    return read_json_payload(daemon_lock_path(root_dir, work_id))


def write_daemon_lock(root_dir: str | Path, work_id: str, payload: dict[str, Any]) -> Path:
    extra_fields = {key: payload[key] for key in ("transfer_pending", "launcher_pid") if payload.get(key) is not None}
    lock = build_lock_payload(
        root_dir,
        work_id,
        version=DAEMON_STATE_VERSION,
        mode=_optional_text(payload.get("mode")),
        pid=_optional_int(payload.get("pid")),
        started_at=_optional_text(payload.get("started_at")),
        heartbeat_at=_optional_text(payload.get("heartbeat_at")),
        extra_fields=extra_fields,
    )
    path = daemon_lock_path(root_dir, work_id)
    write_json_payload(path, lock)
    return path


def acquire_daemon_lock(
    root_dir: str | Path,
    work_id: str,
    *,
    mode: str,
    pid: int | None = None,
    stale_after_seconds: int = 300,
) -> dict[str, Any]:
    owner_pid = pid or os.getpid()
    path = daemon_lock_path(root_dir, work_id)
    lock_result = acquire_runtime_lock(path, owner_pid=owner_pid)
    existing = lock_result.get("existing_lock")
    inherited = bool(lock_result.get("inherited"))
    recovered = False
    if not lock_result.get("acquired"):
        _emit_lock_blocked_alert(
            work_id=work_id,
            owner_pid=owner_pid,
            existing=existing,
            mode=mode,
        )
        return _blocked_lock_result(
            work_id=work_id,
            owner_pid=owner_pid,
            existing=existing,
            mode=mode,
        )
    if isinstance(existing, dict):
        existing_pid = _optional_int(existing.get("pid"))
        transfer_pending = bool(existing.get("transfer_pending"))
        if existing_pid == owner_pid and not transfer_pending:
            return heartbeat_daemon_lock(root_dir, work_id, pid=owner_pid) | {
                "acquired": True,
                "recovered_stale_lock": False,
            }
        if not inherited and not _daemon_lock_is_stale(existing, stale_after_seconds=stale_after_seconds):
            release_runtime_lock(path, remove_metadata=False)
            _emit_lock_blocked_alert(
                work_id=work_id,
                owner_pid=owner_pid,
                existing=existing,
                mode=mode,
            )
            return _blocked_lock_result(
                work_id=work_id,
                owner_pid=owner_pid,
                existing=existing,
                mode=mode,
            )
        recovered = not inherited

    now = utc_now()
    lock = {
        "kind": "autonomous-daemon-lock",
        "version": DAEMON_STATE_VERSION,
        "work_id": work_id,
        "mode": mode,
        "root_dir": str(Path(root_dir).resolve()),
        "pid": owner_pid,
        "started_at": now,
        "heartbeat_at": now,
    }
    write_daemon_lock(root_dir, work_id, lock)
    if recovered:
        emit_alert(
            severity=AlertSeverity.WARNING,
            code="daemon/stale-lock-recovered",
            message="Autonomous daemon recovered a stale lock and took ownership.",
            component="autonomous-daemon",
            work_id=work_id,
            details={
                "owner_pid": owner_pid,
                "stale_after_seconds": stale_after_seconds,
                "previous_pid": _optional_int(existing.get("pid")) if isinstance(existing, dict) else None,
                "mode": mode,
            },
        )
    return {
        **lock,
        "acquired": True,
        "recovered_stale_lock": recovered,
        "readiness_claim": "none",
    }


def _blocked_lock_result(
    *,
    work_id: str,
    owner_pid: int,
    existing: object,
    mode: str,
) -> dict[str, Any]:
    return {
        "kind": "autonomous-daemon-lock-result",
        "version": DAEMON_STATE_VERSION,
        "acquired": False,
        "status": "blocked",
        "stop_reason": "daemon-already-running",
        "work_id": work_id,
        "mode": mode,
        "pid": owner_pid,
        "existing_lock": existing if isinstance(existing, dict) else None,
        "readiness_claim": "none",
    }


def _emit_lock_blocked_alert(
    *,
    work_id: str,
    owner_pid: int,
    existing: object,
    mode: str,
) -> None:
    emit_alert(
        severity=AlertSeverity.WARNING,
        code="daemon/lock-blocked",
        message="Autonomous daemon refused to start: lock held by another pid.",
        component="autonomous-daemon",
        work_id=work_id,
        details={
            "owner_pid": owner_pid,
            "existing_pid": _optional_int(existing.get("pid")) if isinstance(existing, dict) else None,
            "mode": mode,
        },
    )


def heartbeat_daemon_lock(root_dir: str | Path, work_id: str, *, pid: int | None = None) -> dict[str, Any]:
    lock = read_daemon_lock(root_dir, work_id) or {}
    owner_pid = pid or _optional_int(lock.get("pid")) or os.getpid()
    updated = build_lock_payload(
        root_dir,
        work_id,
        version=DAEMON_STATE_VERSION,
        mode=_optional_text(lock.get("mode")),
        pid=owner_pid,
        started_at=_optional_text(lock.get("started_at")),
        heartbeat_at=utc_now(),
    )
    write_daemon_lock(root_dir, work_id, updated)
    return updated


def release_daemon_lock(root_dir: str | Path, work_id: str) -> None:
    release_runtime_lock(daemon_lock_path(root_dir, work_id))


def request_daemon_stop(root_dir: str | Path, work_id: str, *, reason: str = "operator-stop") -> Path:
    payload = build_stop_request_payload(work_id, version=DAEMON_STATE_VERSION, reason=reason)
    path = daemon_stop_path(root_dir, work_id)
    write_json_payload(path, payload)
    return path


def read_daemon_stop_request(root_dir: str | Path, work_id: str) -> dict[str, Any] | None:
    return read_json_payload(daemon_stop_path(root_dir, work_id))


def clear_daemon_stop_request(root_dir: str | Path, work_id: str) -> None:
    remove_runtime_file(daemon_stop_path(root_dir, work_id))


def daemon_status_payload(root_dir: str | Path, work_id: str) -> dict[str, Any]:
    state = read_daemon_state(root_dir, work_id)
    if not state:
        state = _normalize_daemon_state(
            root_dir,
            work_id,
            {
                "status": "not-started",
                "mode": "autonomous-full",
                "work_id": work_id,
                "cycle_count": 0,
            },
        )
    lock = read_daemon_lock(root_dir, work_id)
    stop_request = read_daemon_stop_request(root_dir, work_id)
    state["lock"] = lock if isinstance(lock, dict) else None
    state["stop_request"] = stop_request if isinstance(stop_request, dict) else None
    state["readiness_claim"] = "none"
    state["assessment_scope"] = _assessment_scope()
    return state


def evaluate_daemon_action(
    *,
    work_state: dict[str, Any],
    action: dict[str, Any] | None,
    mode: str,
) -> dict[str, Any]:
    payload = dict(action) if isinstance(action, dict) else {}
    policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
    command = _optional_text(payload.get("command")) or _optional_text(policy.get("command"))
    intent = _optional_text(payload.get("intent")) or _optional_text(policy.get("intent")) or _infer_intent(command)
    lane = _optional_text(payload.get("lane")) or _optional_text(policy.get("lane"))
    target = _optional_text(payload.get("target")) or _optional_text(policy.get("target"))
    action_id = _optional_text(payload.get("action_id")) or _optional_text(policy.get("action_id"))

    base = {
        "kind": "autonomous-daemon-decision",
        "version": DAEMON_STATE_VERSION,
        "mode": mode,
        "command": command,
        "safe_command": None,
        "intent": intent,
        "lane": lane,
        "target": target,
        "action_id": action_id,
        "blocking_categories": [],
        "blocking_gate_ids": [],
        "readiness_claim": "none",
    }
    if not command:
        return {
            **base,
            "decision": "blocked",
            "stop_reason": "no-safe-concrete-action",
            "reason": "No command was available.",
        }
    if _contains_placeholder(command) or _contains_placeholder(target):
        return {
            **base,
            "decision": "blocked",
            "stop_reason": "manual-target-required",
            "reason": "Daemon requires a concrete canonical target.",
            "blocking_categories": ["manual-target"],
        }
    if _is_noncanonical_action(payload):
        return {
            **base,
            "decision": "blocked",
            "stop_reason": "noncanonical-target",
            "reason": "Daemon requires canonical normalized targets.",
            "blocking_categories": ["noncanonical-target"],
        }
    if intent == "standards-refresh":
        return {
            **base,
            "decision": "blocked",
            "stop_reason": "standards-refresh-not-autonomous",
            "reason": "Standards refresh is not autonomous in P8.",
            "blocking_categories": ["standards-consistency"],
        }

    blockers = _known_blockers(work_state)
    runtime_categories = _blocker_categories(blockers, category="runtime")
    if runtime_categories:
        return {
            **base,
            "decision": "blocked",
            "stop_reason": "runtime-blocker",
            "reason": "Runtime blockers require operator review.",
            "blocking_categories": ["runtime"],
        }

    if intent == "export":
        gate_ids = _blocking_gate_ids(blockers)
        standards = _blocker_categories(blockers, category="standards-consistency")
        if standards:
            return {
                **base,
                "decision": "blocked",
                "stop_reason": "standards-block-export",
                "reason": "Standards blockers prevent daemon export.",
                "blocking_categories": ["standards-consistency"],
            }
        if gate_ids:
            return {
                **base,
                "decision": "blocked",
                "stop_reason": "contract-gate-block-export",
                "reason": "Contract gates prevent daemon export.",
                "blocking_categories": ["contract-gate"],
                "blocking_gate_ids": gate_ids,
            }
        finalization_check = payload.get("finalization_check")
        if not _finalization_export_ready(finalization_check):
            return {
                **base,
                "decision": "blocked",
                "stop_reason": "finalization-check-required",
                "reason": "Daemon export requires deterministic finalization check pass.",
                "blocking_categories": ["finalization-check"],
            }
        return {**base, "decision": "allowed", "safe_command": command, "reason": "Export is gated and concrete."}

    if intent == "repair":
        if not _has_repair_eligibility(work_state, lane=lane):
            return {
                **base,
                "decision": "blocked",
                "stop_reason": "repair-plan-required",
                "reason": "Daemon repair requires repair planner metadata.",
                "blocking_categories": ["repair-plan"],
            }
        return {
            **base,
            "decision": "allowed",
            "safe_command": command,
            "reason": "Repair planner metadata allows repair.",
        }

    lane_categories = _lane_blocker_categories(blockers, lane)
    if "dynamic-material" in lane_categories and intent not in {"verify", "review"}:
        return {
            **base,
            "decision": "blocked",
            "stop_reason": "dynamic-material-verification-required",
            "reason": "Dynamic legal material must be verified before drafting.",
            "blocking_categories": ["dynamic-material"],
        }
    if "primary-support" in lane_categories and intent not in {"review", "verify", "repair"}:
        return {
            **base,
            "decision": "blocked",
            "stop_reason": "primary-support-repair-required",
            "reason": "Primary support blockers require a repair or verification path.",
            "blocking_categories": ["primary-support"],
        }

    if intent in {"review", "verify", "draft", "write-section", "article", "finalize", "finalize-checklist"}:
        return {**base, "decision": "allowed", "safe_command": command, "reason": "Concrete daemon action is allowed."}

    return {
        **base,
        "decision": "blocked",
        "stop_reason": "unsupported-daemon-action",
        "reason": "Action is not supported by the daemon policy.",
        "blocking_categories": ["unsupported-action"],
    }


def run_daemon_tick(
    *,
    root_dir: str | Path,
    work_id: str,
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
        lock_result = acquire_daemon_lock(root_dir, work_id, mode=mode, pid=owner_pid)
        if not lock_result.get("acquired"):
            return _write_daemon_cycle_state(
                root_dir=root_dir,
                work_id=work_id,
                mode=mode,
                status="blocked",
                pid=owner_pid,
                max_cycles=max_cycles,
                poll_seconds=poll_seconds,
                max_runtime_minutes=max_runtime_minutes,
                stop_reason="daemon-already-running",
                decision={
                    "decision": "blocked",
                    "stop_reason": "daemon-already-running",
                    "blocking_categories": ["daemon-lock"],
                    "blocking_gate_ids": [],
                    "readiness_claim": "none",
                },
                plan=None,
                command=None,
                result=None,
            )
    else:
        heartbeat_daemon_lock(root_dir, work_id, pid=owner_pid)

    try:
        stop_request = read_daemon_stop_request(root_dir, work_id)
        if isinstance(stop_request, dict):
            stop_reason = _optional_text(stop_request.get("reason")) or "operator-stop"
            clear_daemon_stop_request(root_dir, work_id)
            return _write_daemon_cycle_state(
                root_dir=root_dir,
                work_id=work_id,
                mode=mode,
                status="stopped",
                pid=owner_pid,
                max_cycles=max_cycles,
                poll_seconds=poll_seconds,
                max_runtime_minutes=max_runtime_minutes,
                stop_reason=stop_reason,
                decision={
                    "decision": "blocked",
                    "stop_reason": stop_reason,
                    "blocking_categories": ["operator-stop"],
                    "blocking_gate_ids": [],
                    "readiness_claim": "none",
                },
                plan=None,
                command=None,
                result=None,
            )

        orchestrator = WorkflowOrchestrator(root_dir)
        orchestrator.sync_active_run()
        work_state = orchestrator.get_work_state(work_id=work_id)
        active_run = (
            (work_state.get("runtime") or {}).get("active_run") if isinstance(work_state.get("runtime"), dict) else None
        )
        if isinstance(active_run, dict):
            decision = {
                "kind": "autonomous-daemon-decision",
                "version": DAEMON_STATE_VERSION,
                "decision": "blocked",
                "stop_reason": "active-run",
                "reason": "A workflow run is already active for this work.",
                "command": None,
                "safe_command": None,
                "intent": None,
                "lane": _optional_text(active_run.get("lane")),
                "target": _optional_text(active_run.get("target")),
                "action_id": "wait-active-run",
                "blocking_categories": ["active-run"],
                "blocking_gate_ids": [],
                "readiness_claim": "none",
            }
            return _write_daemon_cycle_state(
                root_dir=root_dir,
                work_id=work_id,
                mode=mode,
                status="waiting",
                pid=owner_pid,
                max_cycles=max_cycles,
                poll_seconds=poll_seconds,
                max_runtime_minutes=max_runtime_minutes,
                stop_reason="active-run",
                decision=decision,
                plan=None,
                command=None,
                result={"status": "waiting", "active_run": active_run},
                observed_readiness_status=_observed_readiness_status(work_state),
            )

        plan = build_autonomous_plan(work_state=work_state, mode=mode, max_steps=1).to_dict()
        step = _first_plan_step(plan)
        action = _action_from_plan_step(step)
        decision = evaluate_daemon_action(work_state=work_state, action=action, mode=mode)
        command = _optional_text(decision.get("safe_command")) or _optional_text(decision.get("command"))
        if decision.get("decision") != "allowed":
            return _write_daemon_cycle_state(
                root_dir=root_dir,
                work_id=work_id,
                mode=mode,
                status="blocked",
                pid=owner_pid,
                max_cycles=max_cycles,
                poll_seconds=poll_seconds,
                max_runtime_minutes=max_runtime_minutes,
                stop_reason=_optional_text(decision.get("stop_reason")) or "no-safe-concrete-action",
                decision=decision,
                plan=plan,
                command=command,
                result=None,
                observed_readiness_status=_observed_readiness_status(work_state),
            )

        result = execute_autonomous_command(orchestrator, str(command), work_id=work_id)
        result_status = _optional_text(result.get("status"))
        if result_status == "started-run":
            status = "waiting"
            stop_reason = "step-started"
        elif result_status == "completed" and str(command).startswith("export-"):
            status = "completed"
            stop_reason = "terminal-export"
        elif result_status == "completed":
            status = "running"
            stop_reason = "step-completed"
        else:
            status = "failed" if result_status not in {"skipped", None} else "blocked"
            stop_reason = _optional_text(result.get("reason")) or "execution-stopped"

        return _write_daemon_cycle_state(
            root_dir=root_dir,
            work_id=work_id,
            mode=mode,
            status=status,
            pid=owner_pid,
            max_cycles=max_cycles,
            poll_seconds=poll_seconds,
            max_runtime_minutes=max_runtime_minutes,
            stop_reason=stop_reason,
            decision=decision,
            plan=plan,
            command=command,
            result=result,
            observed_readiness_status=_observed_readiness_status(work_state),
        )
    finally:
        if not keep_lock and not lock_already_acquired and lock_result and lock_result.get("acquired"):
            release_daemon_lock(root_dir, work_id)


def run_daemon_foreground(
    *,
    root_dir: str | Path,
    work_id: str,
    mode: str = "autonomous-full",
    poll_seconds: int = 30,
    max_cycles: int = 50,
    max_runtime_minutes: int = 240,
    pid: int | None = None,
    sleep_between_cycles: bool = True,
    stuck_after_minutes: int | None = None,
) -> dict[str, Any]:
    owner_pid = pid or os.getpid()
    lock = acquire_daemon_lock(root_dir, work_id, mode=mode, pid=owner_pid)
    if not lock.get("acquired"):
        return _write_daemon_cycle_state(
            root_dir=root_dir,
            work_id=work_id,
            mode=mode,
            status="blocked",
            pid=owner_pid,
            max_cycles=max_cycles,
            poll_seconds=poll_seconds,
            max_runtime_minutes=max_runtime_minutes,
            stop_reason="daemon-already-running",
            decision={
                "decision": "blocked",
                "stop_reason": "daemon-already-running",
                "blocking_categories": ["daemon-lock"],
                "blocking_gate_ids": [],
                "readiness_claim": "none",
            },
            plan=None,
            command=None,
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
        state = _write_daemon_cycle_state(
            root_dir=root_dir,
            work_id=work_id,
            mode=mode,
            status="running",
            pid=owner_pid,
            max_cycles=max_cycles,
            poll_seconds=poll_seconds,
            max_runtime_minutes=max_runtime_minutes,
            stop_reason=None,
            decision=None,
            plan=None,
            command=None,
            result=None,
            increment_cycle=False,
        )
    except Exception as exc:  # noqa: BLE001 — acquired locks must be released on setup failures
        emit_alert(
            severity=AlertSeverity.CRITICAL,
            code="daemon/unhandled-exception",
            message=f"Autonomous daemon crashed during setup: {type(exc).__name__}: {exc}",
            component="autonomous-daemon",
            work_id=work_id,
            details={"mode": mode, "traceback": traceback.format_exc(limit=6)},
        )
        release_daemon_lock(root_dir, work_id)
        raise
    try:
        while True:
            current_cycles = int(state.get("cycle_count") or 0)
            elapsed = datetime.now(UTC) - started_at
            if current_cycles >= max_cycles:
                state = _write_daemon_cycle_state(
                    root_dir=root_dir,
                    work_id=work_id,
                    mode=mode,
                    status="stopped",
                    pid=owner_pid,
                    max_cycles=max_cycles,
                    poll_seconds=poll_seconds,
                    max_runtime_minutes=max_runtime_minutes,
                    stop_reason="max-cycles",
                    decision={"decision": "blocked", "stop_reason": "max-cycles", "readiness_claim": "none"},
                    plan=None,
                    command=None,
                    result=None,
                    increment_cycle=False,
                )
                _emit_daemon_terminal_alert(
                    work_id=work_id,
                    reason="max-cycles",
                    details={"mode": mode, "max_cycles": max_cycles, "cycle_count": current_cycles},
                )
                break
            if elapsed.total_seconds() >= max_runtime_minutes * 60:
                state = _write_daemon_cycle_state(
                    root_dir=root_dir,
                    work_id=work_id,
                    mode=mode,
                    status="stopped",
                    pid=owner_pid,
                    max_cycles=max_cycles,
                    poll_seconds=poll_seconds,
                    max_runtime_minutes=max_runtime_minutes,
                    stop_reason="max-runtime",
                    decision={"decision": "blocked", "stop_reason": "max-runtime", "readiness_claim": "none"},
                    plan=None,
                    command=None,
                    result=None,
                    increment_cycle=False,
                )
                _emit_daemon_terminal_alert(
                    work_id=work_id,
                    reason="max-runtime",
                    details={"mode": mode, "max_runtime_minutes": max_runtime_minutes},
                )
                break
            try:
                guards.check()
            except ResourceGuardError as guard_error:
                state = _write_daemon_cycle_state(
                    root_dir=root_dir,
                    work_id=work_id,
                    mode=mode,
                    status="stopped",
                    pid=owner_pid,
                    max_cycles=max_cycles,
                    poll_seconds=poll_seconds,
                    max_runtime_minutes=max_runtime_minutes,
                    stop_reason=guard_error.code,
                    decision={
                        "decision": "blocked",
                        "stop_reason": guard_error.code,
                        "readiness_claim": "none",
                    },
                    plan=None,
                    command=None,
                    result={"status": "stopped", "guard": guard_error.to_dict()},
                    increment_cycle=False,
                )
                emit_alert(
                    severity=AlertSeverity.CRITICAL,
                    code=f"daemon/{guard_error.code}",
                    message=str(guard_error),
                    component="autonomous-daemon",
                    work_id=work_id,
                    details={"mode": mode, **guard_error.details},
                )
                break
            state = run_daemon_tick(
                root_dir=root_dir,
                work_id=work_id,
                mode=mode,
                max_cycles=max_cycles,
                poll_seconds=poll_seconds,
                max_runtime_minutes=max_runtime_minutes,
                pid=owner_pid,
                lock_already_acquired=True,
                keep_lock=True,
            )
            cycle_command = _optional_text(state.get("command"))
            if cycle_command and cycle_command != last_command:
                guards.checkpoint(f"command:{cycle_command}")
                last_command = cycle_command
            if state.get("status") in DAEMON_TERMINAL_STATUSES:
                break
            if sleep_between_cycles and poll_seconds > 0:
                time.sleep(poll_seconds)
    except Exception as exc:  # noqa: BLE001 — long-running loop must not vanish silently
        state = _write_daemon_cycle_state(
            root_dir=root_dir,
            work_id=work_id,
            mode=mode,
            status="failed",
            pid=owner_pid,
            max_cycles=max_cycles,
            poll_seconds=poll_seconds,
            max_runtime_minutes=max_runtime_minutes,
            stop_reason="unhandled-exception",
            decision={
                "decision": "blocked",
                "stop_reason": "unhandled-exception",
                "blocking_categories": ["runtime-error"],
                "blocking_gate_ids": [],
                "readiness_claim": "none",
            },
            plan=None,
            command=last_command,
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
            message=f"Autonomous daemon crashed: {type(exc).__name__}: {exc}",
            component="autonomous-daemon",
            work_id=work_id,
            details={
                "mode": mode,
                "traceback": traceback.format_exc(limit=6),
            },
        )
        raise
    finally:
        release_daemon_lock(root_dir, work_id)
    return state


def _resolve_stuck_after_minutes(explicit: int | None) -> int | None:
    if explicit is not None:
        return explicit if explicit > 0 else None
    raw = os.environ.get("DAEMON_STUCK_AFTER_MINUTES")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _build_foreground_guards(
    *,
    max_runtime_minutes: int,
    stuck_after_minutes: int | None,
) -> RunGuards:
    # `max_runtime_minutes` is already checked inline above, so the guard-level
    # timeout is set generously to avoid double-firing. The guards-level budget
    # exists as a defense-in-depth net for scenarios where the inline check is
    # bypassed (e.g. future refactors of the loop).
    timeout = TimeoutBudget(
        limit=timedelta(minutes=max(max_runtime_minutes + 5, max_runtime_minutes)),
        label="autonomous-daemon-run",
    )
    stuck_minutes = stuck_after_minutes if stuck_after_minutes is not None else max(max_runtime_minutes, 1)
    stuck = StuckDetector(
        stuck_after=timedelta(minutes=stuck_minutes),
        label="autonomous-daemon-progress",
    )
    return RunGuards(timeout=timeout, stuck=stuck)


def _emit_daemon_terminal_alert(*, work_id: str, reason: str, details: dict[str, Any]) -> None:
    emit_alert(
        severity=AlertSeverity.WARNING,
        code=f"daemon/terminal-{reason}",
        message=f"Autonomous daemon reached terminal state: {reason}.",
        component="autonomous-daemon",
        work_id=work_id,
        details=details,
    )


def start_daemon_process(
    *,
    root_dir: str | Path,
    work_id: str,
    mode: str = "autonomous-full",
    poll_seconds: int = 30,
    max_cycles: int = 50,
    max_runtime_minutes: int = 240,
    stuck_after_minutes: int | None = None,
) -> dict[str, Any]:
    launcher_pid = os.getpid()
    lock = acquire_daemon_lock(
        root_dir,
        work_id,
        mode=mode,
        pid=launcher_pid,
        stale_after_seconds=max(60, poll_seconds * 3),
    )
    if not lock.get("acquired"):
        return _normalize_daemon_state(
            root_dir,
            work_id,
            {
                "status": "blocked",
                "mode": mode,
                "pid": launcher_pid,
                "stop_reason": "daemon-already-running",
                "last_decision": {
                    "decision": "blocked",
                    "stop_reason": "daemon-already-running",
                    "blocking_categories": ["daemon-lock"],
                    "blocking_gate_ids": [],
                    "readiness_claim": "none",
                },
            },
        )

    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(repo_root) if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    child_code = (
        "from telegram_console.work_cli import main; "
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
        "--work",
        work_id,
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

    lock_path = daemon_lock_path(root_dir, work_id)
    lock_fd = runtime_lock_fd(lock_path)
    if lock_fd is None:
        release_daemon_lock(root_dir, work_id)
        raise RuntimeError("Autonomous daemon lock fd is unavailable after acquisition.")
    ready_read_fd, ready_write_fd = os.pipe()
    env.update(inherited_runtime_lock_env(lock_path, ready_fd=ready_read_fd))
    write_daemon_lock(
        root_dir,
        work_id,
        {
            "mode": mode,
            "pid": launcher_pid,
            "started_at": lock.get("started_at"),
            "heartbeat_at": utc_now(),
            "transfer_pending": True,
            "launcher_pid": launcher_pid,
        },
    )
    log_path = daemon_log_path(root_dir, work_id)
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
        write_daemon_lock(
            root_dir,
            work_id,
            {
                "mode": mode,
                "pid": process.pid,
                "started_at": lock.get("started_at"),
                "heartbeat_at": utc_now(),
                "transfer_pending": True,
                "launcher_pid": launcher_pid,
            },
        )
        now = utc_now()
        state = _normalize_daemon_state(
            root_dir,
            work_id,
            {
                "status": "running",
                "mode": mode,
                "work_id": work_id,
                "pid": process.pid,
                "started_at": now,
                "heartbeat_at": now,
                "cycle_count": 0,
                "max_cycles": max_cycles,
                "poll_seconds": poll_seconds,
                "max_runtime_minutes": max_runtime_minutes,
            },
        )
        write_daemon_state(root_dir, work_id, state)
        os.write(ready_write_fd, b"1")
    except Exception:
        release_daemon_lock(root_dir, work_id)
        raise
    finally:
        os.close(ready_read_fd)
        os.close(ready_write_fd)
    detach_runtime_lock(lock_path)
    return state


def _write_daemon_cycle_state(
    *,
    root_dir: str | Path,
    work_id: str,
    mode: str,
    status: str,
    pid: int,
    max_cycles: int,
    poll_seconds: int,
    max_runtime_minutes: int,
    stop_reason: str | None,
    decision: dict[str, Any] | None,
    plan: dict[str, Any] | None,
    command: str | None,
    result: dict[str, Any] | None,
    observed_readiness_status: str | None = None,
    increment_cycle: bool = True,
) -> dict[str, Any]:
    previous = read_daemon_state(root_dir, work_id) or {}
    cycle_count = int(previous.get("cycle_count") or 0) + (1 if increment_cycle else 0)
    now = utc_now()
    trace_item = {
        "cycle": cycle_count,
        "timestamp": now,
        "status": status,
        "stop_reason": stop_reason,
        "command": command,
        "decision": decision,
        "result": result,
        "readiness_claim": "none",
    }
    trace = previous.get("cycle_trace") if isinstance(previous.get("cycle_trace"), list) else []
    if increment_cycle:
        trace = [*trace, trace_item][-DAEMON_TRACE_LIMIT:]
    payload = {
        **previous,
        "kind": "autonomous-daemon-state",
        "version": DAEMON_STATE_VERSION,
        "status": status,
        "work_id": work_id,
        "mode": mode,
        "pid": pid,
        "started_at": _optional_text(previous.get("started_at")) or now,
        "heartbeat_at": now,
        "finished_at": now if status in DAEMON_TERMINAL_STATUSES else None,
        "cycle_count": cycle_count,
        "max_cycles": max_cycles,
        "poll_seconds": poll_seconds,
        "max_runtime_minutes": max_runtime_minutes,
        "last_plan": plan,
        "last_decision": decision,
        "last_command": command,
        "last_result": result,
        "cycle_trace": trace,
        "stop_reason": stop_reason,
        "blocking_categories": list(decision.get("blocking_categories") or []) if isinstance(decision, dict) else [],
        "blocking_gate_ids": list(decision.get("blocking_gate_ids") or []) if isinstance(decision, dict) else [],
        "observed_readiness_status": observed_readiness_status,
        "assessment_scope": _assessment_scope(),
        "readiness_claim": "none",
    }
    write_daemon_state(root_dir, work_id, payload)
    return payload


def _normalize_daemon_state(root_dir: str | Path, work_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    status = _optional_text(payload.get("status")) or "not-started"
    normalized = {
        **payload,
        "kind": "autonomous-daemon-state",
        "version": DAEMON_STATE_VERSION,
        "status": status,
        "work_id": _optional_text(payload.get("work_id")) or work_id,
        "mode": _optional_text(payload.get("mode")) or "autonomous-full",
        "pid": _optional_int(payload.get("pid")),
        "started_at": _optional_text(payload.get("started_at")) or now,
        "heartbeat_at": _optional_text(payload.get("heartbeat_at")) or now,
        "finished_at": _optional_text(payload.get("finished_at")),
        "cycle_count": _optional_int(payload.get("cycle_count")) or 0,
        "max_cycles": _optional_int(payload.get("max_cycles")),
        "poll_seconds": _optional_int(payload.get("poll_seconds")),
        "max_runtime_minutes": _optional_int(payload.get("max_runtime_minutes")),
        "last_plan": payload.get("last_plan") if isinstance(payload.get("last_plan"), dict) else None,
        "last_decision": payload.get("last_decision") if isinstance(payload.get("last_decision"), dict) else None,
        "last_command": _optional_text(payload.get("last_command")),
        "last_result": payload.get("last_result") if isinstance(payload.get("last_result"), dict) else None,
        "cycle_trace": (payload.get("cycle_trace") if isinstance(payload.get("cycle_trace"), list) else [])[
            -DAEMON_TRACE_LIMIT:
        ],
        "stop_reason": _optional_text(payload.get("stop_reason")),
        "blocking_categories": list(payload.get("blocking_categories") or [])
        if isinstance(payload.get("blocking_categories"), list)
        else [],
        "blocking_gate_ids": list(payload.get("blocking_gate_ids") or [])
        if isinstance(payload.get("blocking_gate_ids"), list)
        else [],
        "assessment_scope": _assessment_scope(),
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


def _first_plan_step(plan: dict[str, Any]) -> dict[str, Any] | None:
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    for step in steps:
        if isinstance(step, dict):
            return step
    return None


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


def _daemon_lock_is_stale(lock: dict[str, Any], *, stale_after_seconds: int) -> bool:
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


def _contains_placeholder(value: str | None) -> bool:
    return bool(value and PLACEHOLDER_RE.search(value))


def _is_noncanonical_action(action: dict[str, Any]) -> bool:
    resolution = action.get("target_resolution")
    if not isinstance(resolution, dict):
        return False
    warning_code = _optional_text(resolution.get("warning_code"))
    resolution_mode = _optional_text(resolution.get("resolution_mode"))
    return warning_code == "legacy-root-target" or resolution_mode == "legacy-root"


def _finalization_export_ready(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return payload.get("status") == "pass" and payload.get("finalization_status") in {"export-ready", "exported"}


def _known_blockers(work_state: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = work_state.get("known_blockers")
    return [item for item in blockers if isinstance(item, dict)] if isinstance(blockers, list) else []


def _blocker_categories(blockers: list[dict[str, Any]], *, category: str) -> set[str]:
    return {category for blocker in blockers if blocker.get("category") == category}


def _lane_blocker_categories(blockers: list[dict[str, Any]], lane: str | None) -> set[str]:
    if not lane:
        return set()
    categories: set[str] = set()
    for blocker in blockers:
        if blocker.get("lane") != lane:
            continue
        category = _optional_text(blocker.get("category"))
        if category:
            categories.add(category)
    return categories


def _blocking_gate_ids(blockers: list[dict[str, Any]]) -> list[str]:
    gate_ids: list[str] = []
    for blocker in blockers:
        if blocker.get("category") != "contract-gate":
            continue
        details = blocker.get("details") if isinstance(blocker.get("details"), dict) else {}
        if not details.get("blocks_export") and not details.get("blocks_submission_ready"):
            continue
        gate_id = _optional_text(details.get("gate_id"))
        if gate_id and gate_id not in gate_ids:
            gate_ids.append(gate_id)
    return gate_ids


def _has_repair_eligibility(work_state: dict[str, Any], *, lane: str | None) -> bool:
    runtime = work_state.get("runtime") if isinstance(work_state.get("runtime"), dict) else {}
    recent = runtime.get("recent") if isinstance(runtime.get("recent"), list) else []
    for item in recent:
        if not isinstance(item, dict):
            continue
        if lane and item.get("lane") != lane:
            continue
        repair_decision = item.get("repair_decision") if isinstance(item.get("repair_decision"), dict) else {}
        if repair_decision.get("action") == "repair":
            return True
        thesis_plan = item.get("thesis_repair_plan") if isinstance(item.get("thesis_repair_plan"), dict) else {}
        if thesis_plan.get("eligible"):
            return True
    return False


def _observed_readiness_status(work_state: dict[str, Any]) -> str | None:
    article = work_state.get("article") if isinstance(work_state.get("article"), dict) else {}
    for bundle in article.get("bundles") or []:
        if not isinstance(bundle, dict):
            continue
        status = _optional_text(bundle.get("readiness_status"))
        if status:
            return status
    return None


def _infer_intent(command: str | None) -> str | None:
    if not command:
        return None
    if command.startswith("export-"):
        return "export"
    if " review" in command or "review-section" in command:
        return "review"
    if " verify" in command:
        return "verify"
    if " repair" in command:
        return "repair"
    if " finalize" in command:
        return "finalize"
    if " write-section" in command or " article" in command:
        return "draft"
    if command.startswith("standards-refresh"):
        return "standards-refresh"
    return None


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
