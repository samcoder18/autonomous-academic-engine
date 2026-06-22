from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


class RuntimeStore:
    def __init__(self, bot_home_dir: str | Path):
        self.root_dir = Path(bot_home_dir).resolve()
        self.runtime_dir = self.root_dir / "output" / "telegram" / "runtime"
        self.runs_dir = self.runtime_dir / "runs"
        self.agent_tasks_dir = self.runtime_dir / "agent_tasks"
        self.active_run_file = self.runtime_dir / "active_run.json"
        self.active_runs_dir = self.runtime_dir / "active_runs"
        self.active_agent_task_file = self.runtime_dir / "active_agent_task.json"
        self.notifications_file = self.runtime_dir / "notifications.json"
        self.chat_notifications_file = self.runtime_dir / "chat_notifications.json"
        self.project_chats_file = self.runtime_dir / "project_chats.json"
        self.bot_state_file = self.runtime_dir / "bot_state.json"
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

    def append_chat_notification(self, payload: dict[str, Any]) -> None:
        items = self.read_json(self.chat_notifications_file, default=[])
        items.append(payload)
        self.write_json(self.chat_notifications_file, items)

    def pop_chat_notifications(self) -> list[dict[str, Any]]:
        items = self.read_json(self.chat_notifications_file, default=[])
        if self.chat_notifications_file.exists():
            self.chat_notifications_file.unlink()
        return items

    def get_last_update_id(self) -> int | None:
        state = self.read_json(self.bot_state_file, default={})
        value = state.get("last_update_id")
        return int(value) if value is not None else None

    def set_last_update_id(self, update_id: int) -> None:
        state = self.read_json(self.bot_state_file, default={})
        state["last_update_id"] = int(update_id)
        self.write_json(self.bot_state_file, state)

    def get_active_project_id(self) -> str | None:
        state = self.read_json(self.bot_state_file, default={})
        value = state.get("active_project")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def set_active_project_id(self, project_id: str) -> None:
        state = self.read_json(self.bot_state_file, default={})
        state["active_project"] = project_id
        self.write_json(self.bot_state_file, state)

    def clear_active_project_id(self) -> None:
        state = self.read_json(self.bot_state_file, default={})
        if "active_project" in state:
            state.pop("active_project", None)
            self.write_json(self.bot_state_file, state)

    def get_active_work_id(self, project_id: str) -> str | None:
        state = self.read_json(self.bot_state_file, default={})
        raw = state.get("active_work_by_project")
        if not isinstance(raw, dict):
            return None
        value = raw.get(project_id)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def set_active_work_id(self, project_id: str, work_id: str) -> None:
        state = self.read_json(self.bot_state_file, default={})
        mapping = state.get("active_work_by_project")
        if not isinstance(mapping, dict):
            mapping = {}
        mapping[project_id] = work_id
        state["active_work_by_project"] = mapping
        self.write_json(self.bot_state_file, state)

    def clear_active_work_id(self, project_id: str) -> None:
        state = self.read_json(self.bot_state_file, default={})
        mapping = state.get("active_work_by_project")
        if not isinstance(mapping, dict):
            return
        if project_id in mapping:
            mapping.pop(project_id, None)
            state["active_work_by_project"] = mapping
            self.write_json(self.bot_state_file, state)

    def get_last_chat_project_id(self) -> str | None:
        state = self.read_json(self.bot_state_file, default={})
        value = state.get("last_chat_project")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def set_last_chat_project_id(self, project_id: str) -> None:
        state = self.read_json(self.bot_state_file, default={})
        state["last_chat_project"] = project_id
        self.write_json(self.bot_state_file, state)

    def get_project_chats(self) -> dict[str, dict[str, Any]]:
        payload = self.read_json(self.project_chats_file, default={})
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    def get_project_chat(self, project_id: str) -> dict[str, Any]:
        chats = self.get_project_chats()
        payload = chats.get(project_id)
        return dict(payload) if isinstance(payload, dict) else {}

    def set_project_chat(self, project_id: str, payload: dict[str, Any]) -> None:
        chats = self.get_project_chats()
        chats[project_id] = payload
        self.write_json(self.project_chats_file, chats)

    def delete_project_chat(self, project_id: str) -> None:
        chats = self.get_project_chats()
        if project_id in chats:
            chats.pop(project_id, None)
            self.write_json(self.project_chats_file, chats)

    def list_run_dirs(self) -> list[Path]:
        return sorted((path for path in self.runs_dir.iterdir() if path.is_dir()), reverse=True)

    def list_agent_task_dirs(self) -> list[Path]:
        return sorted((path for path in self.agent_tasks_dir.iterdir() if path.is_dir()), reverse=True)
