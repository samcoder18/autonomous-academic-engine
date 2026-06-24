from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .utils import parse_datetime

JOB_INSPECTION_KIND = "job-inspection"
JOB_INSPECTION_VERSION = "v1"
_EPOCH = datetime.fromtimestamp(0, tz=UTC)


def inspect_job(
    root_dir: str | Path,
    job: dict[str, Any],
    *,
    export_blockers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    warnings: list[dict[str, Any]] = []
    workflow_id = job.get("workflow_id")
    workflow_dir = root / "output" / "runs" / workflow_id if isinstance(workflow_id, str) and workflow_id else None
    job_path = _job_path(root, job)
    workflow_path = workflow_dir / "workflow.json" if workflow_dir is not None else None
    events_path = workflow_dir / "events.jsonl" if workflow_dir is not None else None
    gates_path = workflow_dir / "gates.json" if workflow_dir is not None else None
    promotion_path = workflow_dir / "promotion.json" if workflow_dir is not None else None

    workflow = _workflow_payload(workflow_path, warnings)
    role_runs = _role_runs(workflow, workflow_path, warnings)
    events = _read_events(events_path, warnings) if events_path is not None else []
    if gates_path is not None:
        _validate_gates_payload(gates_path, _read_json(gates_path, warnings), warnings)
    if promotion_path is not None:
        _validate_promotion_payload(promotion_path, _read_json(promotion_path, warnings), warnings)
    attachments = _attachments(
        {
            "job": job_path,
            "workflow": workflow_path,
            "events": events_path,
            "gates": gates_path,
            "promotion": promotion_path,
            **_role_output_paths(role_runs),
        }
    )

    return {
        "kind": JOB_INSPECTION_KIND,
        "version": JOB_INSPECTION_VERSION,
        "job": dict(job),
        "timeline": _timeline(job, events),
        "durations": _durations(job, workflow, role_runs),
        "failure": _failure(job, role_runs),
        "blockers": _blockers(workflow, role_runs),
        "changed_files": _changed_files(role_runs),
        "export_blockers": list(export_blockers or []),
        "attachments": attachments,
        "observability_warnings": warnings,
    }


def _read_json(path: Path, warnings: list[dict[str, Any]]) -> Any:
    if not path.exists():
        warnings.append(
            {
                "code": "missing-file",
                "path": str(path),
                "message": f"Missing observability file `{path}`.",
            }
        )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        warnings.append(
            {
                "code": "malformed-json",
                "path": str(path),
                "message": f"Malformed JSON in `{path}`.",
                "line": exc.lineno,
                "column": exc.colno,
            }
        )
    except OSError as exc:
        warnings.append(
            {
                "code": "read-error",
                "path": str(path),
                "message": str(exc),
            }
        )
    return None


def _read_events(path: Path, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not path.exists():
        warnings.append(
            {
                "code": "missing-file",
                "path": str(path),
                "message": f"Missing observability file `{path}`.",
            }
        )
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        warnings.append(
            {
                "code": "read-error",
                "path": str(path),
                "message": str(exc),
            }
        )
        return events
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(
                {
                    "code": "malformed-jsonl",
                    "path": str(path),
                    "line": line_number,
                    "column": exc.colno,
                    "message": f"Malformed JSONL event in `{path}`.",
                }
            )
            continue
        if not isinstance(event, dict):
            warnings.append(
                {
                    "code": "malformed-event",
                    "path": str(path),
                    "line": line_number,
                    "message": "Workflow event must be a JSON object.",
                }
            )
            continue
        if not isinstance(event.get("timestamp"), str) or not isinstance(event.get("event"), str):
            warnings.append(
                {
                    "code": "malformed-event",
                    "path": str(path),
                    "line": line_number,
                    "message": "Workflow event must include string `timestamp` and `event` fields.",
                }
            )
            continue
        events.append(event)
    return events


def _seconds_between(started_at: object, finished_at: object) -> float | None:
    started = _parse_timestamp(started_at)
    finished = _parse_timestamp(finished_at)
    if started is None or finished is None:
        return None
    try:
        return (finished - started).total_seconds()
    except TypeError:
        return None


def _attachments(paths: dict[str, Path | None]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "path": str(path) if path is not None else None,
            "exists": path.exists() if path is not None else False,
        }
        for name, path in paths.items()
    }


def _job_path(root: Path, job: dict[str, Any]) -> Path | None:
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return None
    return root / "output" / "runtime" / "jobs" / f"{job_id}.json"


def _workflow_payload(path: Path | None, warnings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = _read_json(path, warnings)
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload
    warnings.append(
        {
            "code": "malformed-workflow",
            "path": str(path),
            "message": "Workflow payload must be a JSON object.",
        }
    )
    return None


def _validate_gates_payload(path: Path, payload: Any, warnings: list[dict[str, Any]]) -> None:
    if payload is None:
        return
    if not isinstance(payload, dict) or not isinstance(payload.get("gates"), list):
        warnings.append(
            {
                "code": "malformed-artifact",
                "path": str(path),
                "message": "Gates artifact must be an object with a `gates` list.",
            }
        )
        return
    for index, gate in enumerate(payload["gates"]):
        if isinstance(gate, dict):
            continue
        warnings.append(
            {
                "code": "malformed-artifact",
                "path": str(path),
                "message": f"Gates artifact `gates[{index}]` must be a JSON object.",
            }
        )


def _validate_promotion_payload(path: Path, payload: Any, warnings: list[dict[str, Any]]) -> None:
    if payload is None:
        return
    if not isinstance(payload, dict) or not isinstance(payload.get("status"), str) or not payload["status"]:
        warnings.append(
            {
                "code": "malformed-artifact",
                "path": str(path),
                "message": "Promotion artifact must be an object with a non-empty string `status`.",
            }
        )


def _role_runs(
    workflow: dict[str, Any] | None,
    workflow_path: Path | None,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(workflow, dict):
        return []
    role_runs = workflow.get("role_runs")
    warning_path = str(workflow_path) if workflow_path is not None else None
    if not isinstance(role_runs, list):
        warnings.append(
            {
                "code": "malformed-workflow",
                "path": warning_path,
                "message": "Workflow `role_runs` must be a list.",
            }
        )
        return []
    valid_roles: list[dict[str, Any]] = []
    for index, item in enumerate(role_runs):
        if isinstance(item, dict):
            valid_roles.append(item)
            continue
        warnings.append(
            {
                "code": "malformed-workflow",
                "path": warning_path,
                "message": f"Workflow `role_runs[{index}]` must be a JSON object.",
            }
        )
    return valid_roles


def _timeline(job: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = _job_timeline(job)
    timeline.extend(_workflow_timeline(events))
    return sorted(timeline, key=_timeline_sort_key)


def _job_timeline(job: dict[str, Any]) -> list[dict[str, Any]]:
    history = job.get("history")
    if not isinstance(history, list):
        return []
    timeline: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        timestamp = item.get("timestamp")
        event = item.get("event")
        if not isinstance(timestamp, str) or not isinstance(event, str):
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        if isinstance(item.get("status"), str):
            details = {**details, "status": item["status"]}
        timeline.append(
            {
                "timestamp": timestamp,
                "event": event,
                "source": "job",
                "details": details,
            }
        )
    return timeline


def _workflow_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for event_payload in events:
        timestamp = event_payload.get("timestamp")
        event = event_payload.get("event")
        if not isinstance(timestamp, str) or not isinstance(event, str):
            continue
        details = {key: value for key, value in event_payload.items() if key not in {"timestamp", "event"}}
        timeline.append(
            {
                "timestamp": timestamp,
                "event": event,
                "source": "workflow",
                "details": details,
            }
        )
    return timeline


def _timeline_sort_key(item: dict[str, Any]) -> tuple[float, str, str]:
    timestamp = item.get("timestamp")
    parsed = _parse_timestamp(timestamp)
    value = parsed.timestamp() if parsed is not None else 0.0
    return (value, str(item.get("source") or ""), str(item.get("event") or ""))


def _durations(
    job: dict[str, Any],
    workflow: dict[str, Any] | None,
    role_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    total_seconds = None
    if workflow is not None:
        total_seconds = _seconds_between(workflow.get("started_at"), workflow.get("finished_at"))
    if total_seconds is None:
        total_seconds = _seconds_between(job.get("created_at"), job.get("updated_at"))
    return {
        "total_seconds": total_seconds,
        "roles": [
            {
                "role_run_id": role.get("role_run_id"),
                "role_id": role.get("role_id"),
                "started_at": role.get("started_at"),
                "finished_at": role.get("finished_at"),
                "duration_seconds": _seconds_between(role.get("started_at"), role.get("finished_at")),
            }
            for role in role_runs
        ],
    }


def _failure(job: dict[str, Any], role_runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for role in role_runs:
        if role.get("status") == "succeeded":
            continue
        return {
            "source": "role",
            "role_run_id": role.get("role_run_id"),
            "role_id": role.get("role_id"),
            "status": role.get("status"),
            "reported_status": role.get("reported_status"),
            "error": role.get("error"),
            "blockers": _dict_items(role.get("blockers")),
        }
    failure = job.get("failure")
    return dict(failure) if isinstance(failure, dict) else None


def _blockers(workflow: dict[str, Any] | None, role_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers = _dict_items(workflow.get("blockers") if workflow is not None else None)
    for role in role_runs:
        blockers.extend(_dict_items(role.get("blockers")))
    return blockers


def _changed_files(role_runs: list[dict[str, Any]]) -> list[str]:
    changed: set[str] = set()
    for role in role_runs:
        changed_paths = role.get("changed_paths")
        if not isinstance(changed_paths, list):
            continue
        changed.update(item for item in changed_paths if isinstance(item, str))
    return sorted(changed)


def _role_output_paths(role_runs: list[dict[str, Any]]) -> dict[str, Path | None]:
    paths: dict[str, Path | None] = {}
    for role in role_runs:
        role_run_id = role.get("role_run_id")
        output_file = role.get("output_file")
        if isinstance(role_run_id, str) and role_run_id and isinstance(output_file, str) and output_file:
            paths[f"role_output:{role_run_id}"] = Path(output_file)
    return paths


def _dict_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = parse_datetime(value)
    if parsed == _EPOCH and value not in {"1970-01-01T00:00:00+00:00", "19700101-000000"}:
        return None
    return parsed
