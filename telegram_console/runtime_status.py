from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import parse_datetime

RUNTIME_STATUS_VERSION = "v2"
FAILURE_CATEGORIES = ("config", "input", "process", "codex", "artifact", "runtime", "external")

WORKFLOW_ATTACHMENTS = ("status", "request", "result", "log", "manifest", "trace", "resolution")
CHAT_ATTACHMENTS = ("status", "request", "result", "response", "stdout", "stderr")


@dataclass(frozen=True)
class RuntimeRecord:
    record_id: str
    entity_kind: str
    status: str
    stage: str
    project_id: str | None = None
    project_title: str | None = None
    project_root: str | None = None
    work_id: str | None = None
    work_title: str | None = None
    lane: str | None = None
    profile: str | None = None
    action: str | None = None
    workflow_id: str | None = None
    readiness_status: str | None = None
    promotion_status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    summary: str | None = None
    failure: dict[str, Any] | None = None
    blockers: tuple[dict[str, Any], ...] = ()
    repair_decision: dict[str, Any] | None = None
    repair_iteration: int | None = None
    terminal_reason: str | None = None
    thesis_repair_plan: dict[str, Any] | None = None
    contract_gates: tuple[dict[str, Any], ...] = ()
    finalization_check: dict[str, Any] | None = None
    target_resolution: dict[str, Any] | None = None
    checkpoints: tuple[dict[str, Any], ...] = ()
    role_runs: tuple[dict[str, Any], ...] = ()
    gate_summary: dict[str, Any] = field(default_factory=dict)
    attachments: dict[str, dict[str, Any]] = field(default_factory=dict)
    runtime_dir: str | None = None
    status_path: str | None = None
    source: str = "status"

    @property
    def sort_key(self) -> tuple[float, str]:
        stamp = self.finished_at or self.started_at
        return (parse_datetime(stamp).timestamp(), self.record_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "entity_kind": self.entity_kind,
            "status": self.status,
            "stage": self.stage,
            "project_id": self.project_id,
            "project_title": self.project_title,
            "project_root": self.project_root,
            "work_id": self.work_id,
            "work_title": self.work_title,
            "lane": self.lane,
            "profile": self.profile,
            "action": self.action,
            "workflow_id": self.workflow_id,
            "readiness_status": self.readiness_status,
            "promotion_status": self.promotion_status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.summary,
            "failure": self.failure,
            "blockers": list(self.blockers),
            "repair_decision": self.repair_decision,
            "repair_iteration": self.repair_iteration,
            "terminal_reason": self.terminal_reason,
            "thesis_repair_plan": self.thesis_repair_plan,
            "contract_gates": list(self.contract_gates),
            "finalization_check": self.finalization_check,
            "target_resolution": self.target_resolution,
            "checkpoints": list(self.checkpoints),
            "role_runs": list(self.role_runs),
            "gate_summary": dict(self.gate_summary),
            "attachments": self.attachments,
            "runtime_dir": self.runtime_dir,
            "status_path": self.status_path,
            "source": self.source,
        }


def build_failure(
    category: str,
    code: str,
    message: str,
    *,
    retryable: bool | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": category if category in FAILURE_CATEGORIES else "runtime",
        "code": code,
        "message": message,
    }
    if retryable is not None:
        payload["retryable"] = retryable
    if details:
        payload["details"] = details
    return payload


def build_checkpoint(
    name: str,
    *,
    status: str,
    stage: str,
    timestamp: str,
    message: str | None = None,
    failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "status": status,
        "stage": stage,
        "timestamp": timestamp,
    }
    if message:
        payload["message"] = message
    if failure:
        payload["failure"] = failure
    return payload


def build_attachments(paths: dict[str, str | Path | None]) -> dict[str, dict[str, Any]]:
    attachments: dict[str, dict[str, Any]] = {}
    for name, raw_path in paths.items():
        if raw_path is None:
            continue
        path = Path(raw_path)
        attachments[name] = {
            "path": str(path),
            "exists": path.exists(),
        }
    return attachments


def build_runtime_status(
    *,
    record_id: str,
    entity_kind: str,
    status: str,
    stage: str,
    project_id: str | None = None,
    project_title: str | None = None,
    project_root: str | None = None,
    work_id: str | None = None,
    work_title: str | None = None,
    lane: str | None = None,
    profile: str | None = None,
    action: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    summary: str | None = None,
    failure: dict[str, Any] | None = None,
    blockers: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    repair_decision: dict[str, Any] | None = None,
    repair_iteration: int | None = None,
    terminal_reason: str | None = None,
    thesis_repair_plan: dict[str, Any] | None = None,
    contract_gates: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    finalization_check: dict[str, Any] | None = None,
    target_resolution: dict[str, Any] | None = None,
    checkpoints: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    attachments: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "version": RUNTIME_STATUS_VERSION,
        "record_id": record_id,
        "entity_kind": entity_kind,
        "status": status,
        "stage": stage,
        "project_id": project_id,
        "project_title": project_title,
        "project_root": project_root,
        "work_id": work_id,
        "work_title": work_title,
        "lane": lane,
        "profile": profile,
        "action": action,
        "started_at": started_at,
        "finished_at": finished_at,
        "summary": summary,
        "failure": failure,
        "blockers": list(blockers or []),
        "repair_decision": repair_decision,
        "repair_iteration": repair_iteration,
        "terminal_reason": terminal_reason,
        "thesis_repair_plan": thesis_repair_plan,
        "contract_gates": list(contract_gates or []),
        "finalization_check": finalization_check,
        "target_resolution": target_resolution,
        "checkpoints": list(checkpoints or []),
        "attachments": attachments or {},
    }


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def read_status(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def attachment_path(record: RuntimeRecord, attachment: str) -> Path | None:
    payload = record.attachments.get(attachment)
    if not isinstance(payload, dict):
        return None
    raw_path = _optional_text(payload.get("path"))
    return Path(raw_path) if raw_path else None


def load_runtime_record(runtime_dir: Path, entity_kind: str) -> RuntimeRecord | None:
    workflow_path = runtime_dir / "workflow.json"
    if workflow_path.exists():
        workflow_payload = _read_json(workflow_path)
        if isinstance(workflow_payload, dict) and workflow_payload.get("version") == "workflow-run/v1":
            return workflow_v1_record(workflow_payload, runtime_dir=runtime_dir)
    status_path = runtime_dir / "status.json"
    payload = read_status(status_path)
    if isinstance(payload, dict):
        return record_from_payload(payload, runtime_dir=runtime_dir, status_path=status_path, source="status")
    return synthesize_runtime_record(runtime_dir, entity_kind)


def workflow_v1_record(payload: dict[str, Any], *, runtime_dir: Path) -> RuntimeRecord | None:
    workflow_id = _optional_text(payload.get("workflow_id"))
    work_id = _optional_text(payload.get("work_id"))
    lane = _optional_text(payload.get("lane"))
    action = _optional_text(payload.get("action"))
    execution_status = _optional_text(payload.get("execution_status"))
    if not all((workflow_id, work_id, lane, action, execution_status)):
        return None
    role_runs = payload.get("role_runs")
    if not isinstance(role_runs, list):
        role_runs = []
    gates = payload.get("gates")
    if not isinstance(gates, list):
        gates = []
    blockers = payload.get("blockers")
    if not isinstance(blockers, list):
        blockers = []
    gate_summary = payload.get("gate_summary")
    if not isinstance(gate_summary, dict):
        gate_summary = {}
    return RuntimeRecord(
        record_id=_optional_text(payload.get("run_id")) or workflow_id,
        entity_kind="workflow-run",
        status=execution_status,
        stage="completed" if execution_status == "succeeded" else "failed",
        work_id=work_id,
        lane=lane,
        action=action,
        workflow_id=workflow_id,
        readiness_status=_optional_text(payload.get("readiness_status")),
        promotion_status=_optional_text(payload.get("promotion_status")),
        started_at=_optional_text(payload.get("started_at")),
        finished_at=_optional_text(payload.get("finished_at")),
        summary=f"{lane}/{action}: {execution_status}",
        blockers=tuple(item for item in blockers if isinstance(item, dict)),
        contract_gates=tuple(item for item in gates if isinstance(item, dict)),
        role_runs=tuple(item for item in role_runs if isinstance(item, dict)),
        gate_summary={str(key): value for key, value in gate_summary.items()},
        attachments=build_attachments(
            {
                "workflow": runtime_dir / "workflow.json",
                "events": runtime_dir / "events.jsonl",
                "gates": runtime_dir / "gates.json",
                "promotion": runtime_dir / "promotion.json",
            }
        ),
        runtime_dir=str(runtime_dir),
        status_path=str(runtime_dir / "workflow.json"),
        source="workflow-v1",
    )


def record_from_payload(
    payload: dict[str, Any],
    *,
    runtime_dir: Path | None,
    status_path: Path | None,
    source: str,
) -> RuntimeRecord | None:
    record_id = _optional_text(payload.get("record_id"))
    entity_kind = _optional_text(payload.get("entity_kind"))
    status = _optional_text(payload.get("status"))
    stage = _optional_text(payload.get("stage"))
    if record_id is None or entity_kind is None or status is None or stage is None:
        return None
    attachments = payload.get("attachments")
    if not isinstance(attachments, dict):
        attachments = {}
    blockers = payload.get("blockers")
    if not isinstance(blockers, list):
        blockers = []
    checkpoints = payload.get("checkpoints")
    if not isinstance(checkpoints, list):
        checkpoints = []
    contract_gates = payload.get("contract_gates")
    if not isinstance(contract_gates, list):
        contract_gates = []
    role_runs = payload.get("role_runs")
    if not isinstance(role_runs, list):
        role_runs = []
    gate_summary = payload.get("gate_summary")
    if not isinstance(gate_summary, dict):
        gate_summary = {}
    repair_iteration = payload.get("repair_iteration")
    if not isinstance(repair_iteration, int):
        repair_iteration = None
    return RuntimeRecord(
        record_id=record_id,
        entity_kind=entity_kind,
        status=status,
        stage=stage,
        project_id=_optional_text(payload.get("project_id")),
        project_title=_optional_text(payload.get("project_title")),
        project_root=_optional_text(payload.get("project_root")),
        work_id=_optional_text(payload.get("work_id")),
        work_title=_optional_text(payload.get("work_title")),
        lane=_optional_text(payload.get("lane")),
        profile=_optional_text(payload.get("profile")),
        action=_optional_text(payload.get("action")),
        workflow_id=_optional_text(payload.get("workflow_id")),
        readiness_status=_optional_text(payload.get("readiness_status")),
        promotion_status=_optional_text(payload.get("promotion_status")),
        started_at=_optional_text(payload.get("started_at")),
        finished_at=_optional_text(payload.get("finished_at")),
        summary=_optional_text(payload.get("summary")),
        failure=payload.get("failure") if isinstance(payload.get("failure"), dict) else None,
        blockers=tuple(item for item in blockers if isinstance(item, dict)),
        repair_decision=payload.get("repair_decision") if isinstance(payload.get("repair_decision"), dict) else None,
        repair_iteration=repair_iteration,
        terminal_reason=_optional_text(payload.get("terminal_reason")),
        thesis_repair_plan=payload.get("thesis_repair_plan")
        if isinstance(payload.get("thesis_repair_plan"), dict)
        else None,
        contract_gates=tuple(item for item in contract_gates if isinstance(item, dict)),
        finalization_check=payload.get("finalization_check")
        if isinstance(payload.get("finalization_check"), dict)
        else None,
        target_resolution=payload.get("target_resolution")
        if isinstance(payload.get("target_resolution"), dict)
        else None,
        checkpoints=tuple(item for item in checkpoints if isinstance(item, dict)),
        role_runs=tuple(item for item in role_runs if isinstance(item, dict)),
        gate_summary={str(key): value for key, value in gate_summary.items()},
        attachments={str(key): value for key, value in attachments.items() if isinstance(value, dict)},
        runtime_dir=str(runtime_dir) if runtime_dir else None,
        status_path=str(status_path) if status_path else None,
        source=source,
    )


def synthesize_runtime_record(runtime_dir: Path, entity_kind: str) -> RuntimeRecord | None:
    request = _read_json(runtime_dir / "request.json")
    if not isinstance(request, dict):
        return None
    result = _read_json(runtime_dir / "result.json")
    attachments = build_attachments(_synthesized_attachment_paths(runtime_dir, entity_kind))
    if entity_kind == "workflow-run":
        record_id = _optional_text(request.get("run_id")) or runtime_dir.name
        result_status = _optional_text((result or {}).get("status"))
        return RuntimeRecord(
            record_id=record_id,
            entity_kind=entity_kind,
            status=_normalize_status(result_status),
            stage=_synthesized_stage(result_status),
            project_id=_optional_text(request.get("project_id")),
            project_title=_optional_text(request.get("project_title")),
            project_root=_optional_text(request.get("project_root")),
            work_id=_optional_text(request.get("work_id")),
            work_title=_optional_text(request.get("work_title")),
            lane=_optional_text(request.get("lane")),
            action=_optional_text(request.get("action")),
            started_at=_optional_text((result or {}).get("started_at")) or _optional_text(request.get("started_at")),
            finished_at=_optional_text((result or {}).get("finished_at")),
            summary=_optional_text(request.get("target")) or _optional_text(request.get("topic")),
            failure=(result or {}).get("failure") if isinstance((result or {}).get("failure"), dict) else None,
            target_resolution=request.get("target_resolution")
            if isinstance(request.get("target_resolution"), dict)
            else None,
            attachments=attachments,
            runtime_dir=str(runtime_dir),
            status_path=None,
            source="synthetic",
        )

    record_id = _optional_text(request.get("task_id")) or runtime_dir.name
    result_status = _optional_text((result or {}).get("status"))
    return RuntimeRecord(
        record_id=record_id,
        entity_kind=entity_kind,
        status=_normalize_status(result_status),
        stage=_synthesized_stage(result_status),
        project_id=_optional_text(request.get("project_id")),
        project_title=_optional_text(request.get("project_title")),
        project_root=_optional_text(request.get("project_root")),
        work_id=_optional_text(request.get("work_id")),
        work_title=_optional_text(request.get("work_title")),
        profile=_optional_text(request.get("profile")),
        action="chat",
        started_at=_optional_text((result or {}).get("started_at")) or _optional_text(request.get("started_at")),
        finished_at=_optional_text((result or {}).get("finished_at")),
        summary=_optional_text((result or {}).get("response_text")) or _optional_text((result or {}).get("error")),
        failure=(result or {}).get("failure") if isinstance((result or {}).get("failure"), dict) else None,
        attachments=attachments,
        runtime_dir=str(runtime_dir),
        status_path=None,
        source="synthetic",
    )


def _synthesized_attachment_paths(runtime_dir: Path, entity_kind: str) -> dict[str, Path]:
    if entity_kind == "workflow-run":
        return {
            "request": runtime_dir / "request.json",
            "result": runtime_dir / "result.json",
            "log": runtime_dir / "launcher.log",
            "resolution": runtime_dir / "resolution.json",
        }
    return {
        "request": runtime_dir / "request.json",
        "result": runtime_dir / "result.json",
        "response": runtime_dir / "assistant.txt",
        "stdout": runtime_dir / "codex.stdout.jsonl",
        "stderr": runtime_dir / "codex.stderr.log",
    }


def _normalize_status(value: str | None) -> str:
    if value == "success":
        return "succeeded"
    if value == "interrupted":
        return "interrupted"
    if value == "failed":
        return "failed"
    if value == "running":
        return "running"
    return "queued"


def _synthesized_stage(value: str | None) -> str:
    status = _normalize_status(value)
    if status in {"succeeded", "failed", "interrupted"}:
        return "completed"
    if status == "running":
        return "running"
    return "queued"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
