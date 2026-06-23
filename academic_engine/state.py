from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


class RuntimeStore:
    def __init__(self, workspace_root: str | Path):
        self.root_dir = Path(workspace_root).resolve()
        self.runtime_dir = self.root_dir / "output" / "runtime"
        self.runs_dir = self.runtime_dir / "runs"
        self.agent_tasks_dir = self.runtime_dir / "agent_tasks"
        self.active_run_file = self.runtime_dir / "active_run.json"
        self.active_runs_dir = self.runtime_dir / "active_runs"
        self.active_agent_task_file = self.runtime_dir / "active_agent_task.json"
        self.notifications_file = self.runtime_dir / "notifications.json"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.agent_tasks_dir.mkdir(parents=True, exist_ok=True)
        self.active_runs_dir.mkdir(parents=True, exist_ok=True)

    def read_json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

    def write_json(self, path: Path, payload: Any) -> None:
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

    def get_active_run(self, work_id: str | None = None) -> dict[str, Any] | None:
        if work_id:
            payload = self.read_json(self._active_run_path(work_id))
            if isinstance(payload, dict):
                return payload
            legacy = self.read_json(self.active_run_file)
            if isinstance(legacy, dict) and str(legacy.get("work_id") or "").strip() == work_id:
                return legacy
            return None
        legacy = self.read_json(self.active_run_file)
        if isinstance(legacy, dict):
            return legacy
        runs = self.list_active_runs()
        return runs[0] if runs else None

    def set_active_run(self, payload: dict[str, Any]) -> None:
        work_id = str(payload.get("work_id") or "").strip()
        if not work_id:
            self.write_json(self.active_run_file, payload)
            return
        self.write_json(self._active_run_path(work_id), payload)

    def clear_active_run(self, work_id: str | None = None) -> None:
        if work_id:
            path = self._active_run_path(work_id)
            if path.exists():
                path.unlink()
            legacy = self.read_json(self.active_run_file)
            if isinstance(legacy, dict) and str(legacy.get("work_id") or "").strip() == work_id:
                self.active_run_file.unlink()
            return
        if self.active_run_file.exists():
            self.active_run_file.unlink()
        for path in self.active_runs_dir.glob("*.json"):
            path.unlink()

    def list_active_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        legacy = self.read_json(self.active_run_file)
        if isinstance(legacy, dict):
            runs.append(legacy)
        seen = {str(item.get("run_id") or "") for item in runs}
        for path in sorted(self.active_runs_dir.glob("*.json")):
            payload = self.read_json(path)
            if not isinstance(payload, dict):
                continue
            run_id = str(payload.get("run_id") or "")
            if run_id in seen:
                continue
            seen.add(run_id)
            runs.append(payload)
        return runs

    def _active_run_path(self, work_id: str) -> Path:
        safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in work_id)
        return self.active_runs_dir / f"{safe or 'default'}.json"

    def append_notification(self, payload: dict[str, Any]) -> None:
        items = self.read_json(self.notifications_file, default=[])
        items.append(payload)
        self.write_json(self.notifications_file, items)

    def pop_notifications(self) -> list[dict[str, Any]]:
        items = self.read_json(self.notifications_file, default=[])
        if self.notifications_file.exists():
            self.notifications_file.unlink()
        return items

    def get_active_agent_task(self) -> dict[str, Any] | None:
        return self.read_json(self.active_agent_task_file)

    def set_active_agent_task(self, payload: dict[str, Any]) -> None:
        self.write_json(self.active_agent_task_file, payload)

    def clear_active_agent_task(self) -> None:
        if self.active_agent_task_file.exists():
            self.active_agent_task_file.unlink()

    def list_run_dirs(self) -> list[Path]:
        return sorted((path for path in self.runs_dir.iterdir() if path.is_dir()), reverse=True)

    def list_agent_task_dirs(self) -> list[Path]:
        return sorted((path for path in self.agent_tasks_dir.iterdir() if path.is_dir()), reverse=True)
