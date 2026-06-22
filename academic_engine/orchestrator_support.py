"""Shared types, constants, and helpers for workflow orchestration (split from orchestrator)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .runtime_status import RuntimeRecord
from .utils import parse_datetime

THESIS_ACTIONS = (
    "full-cycle",
    "source-pack",
    "verify",
    "write-section",
    "review-section",
    "style-pass",
    "build-maps",
    "verify-claims",
    "counterargument-pass",
    "draft-author-position",
    "formal-artifacts",
)
ARTICLE_ACTIONS = ("article", "review", "repair", "finalize")

LANE_TITLES = {
    "thesis": "диплом",
    "article": "статья",
}

ACTION_TITLES = {
    "full-cycle": "полный цикл",
    "source-pack": "пакет источников",
    "verify": "проверка",
    "write-section": "написание раздела",
    "review-section": "рецензия раздела",
    "style-pass": "стиль и ритм",
    "build-maps": "карта диссертации",
    "verify-claims": "проверка тезисов диссертации",
    "counterargument-pass": "контраргументы",
    "draft-author-position": "авторская позиция",
    "formal-artifacts": "формальные артефакты",
    "article": "новая статья",
    "review": "рецензирование",
    "repair": "исправление",
    "finalize": "финализация",
}


class WorkflowError(RuntimeError):
    """Raised when the requested workflow action is invalid."""


class RunBusyError(WorkflowError):
    """Raised when another workflow is already running."""


@dataclass
class RunRecord:
    record_id: str
    lane: str
    action: str
    status: str
    started_at: str
    project_id: str | None = None
    project_title: str | None = None
    project_root: str | None = None
    work_id: str | None = None
    work_title: str | None = None
    finished_at: str | None = None
    target: str | None = None
    topic: str | None = None
    manifest_path: str | None = None
    output_file: str | None = None
    log_path: str | None = None
    runtime_run_dir: str | None = None
    source: str = "manifest"
    summary: str | None = None

    @property
    def sort_key(self) -> tuple[float, str]:
        stamp = self.finished_at or self.started_at
        return (parse_datetime(stamp).timestamp(), self.record_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def slugify(text: str) -> str:
    value = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-_")
    value = re.sub(r"-{2,}", "-", value)
    return value[:80] or "run"


def lane_title(lane: str) -> str:
    return LANE_TITLES.get(lane, lane)


def action_title(action: str) -> str:
    return ACTION_TITLES.get(action, action)


def _optional_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _effective_repair_iteration(record: RuntimeRecord) -> int:
    iteration = record.repair_iteration if isinstance(record.repair_iteration, int) else 0
    decision = record.repair_decision if isinstance(record.repair_decision, dict) else {}
    decision_iteration = _optional_int(decision.get("repair_iteration"))
    if decision_iteration is not None:
        iteration = max(iteration, decision_iteration)
    return iteration


def _contract_gate_summary(gates: object) -> dict[str, int]:
    if not isinstance(gates, list):
        return {"total_count": 0, "block_count": 0, "warn_count": 0}
    block_count = 0
    warn_count = 0
    total_count = 0
    for item in gates:
        if not isinstance(item, dict):
            continue
        total_count += 1
        status = _optional_text(item.get("status"))
        if status == "block":
            block_count += 1
        elif status == "warn":
            warn_count += 1
    return {"total_count": total_count, "block_count": block_count, "warn_count": warn_count}
