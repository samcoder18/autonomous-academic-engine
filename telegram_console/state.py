from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import tempfile


class RuntimeStore:
    def __init__(self, bot_home_dir: str | Path):
        self.root_dir = Path(bot_home_dir).resolve()
        self.runtime_dir = self.root_dir / "output" / "telegram" / "runtime"
        self.runs_dir = self.runtime_dir / "runs"
        self.agent_tasks_dir = self.runtime_dir / "agent_tasks"
        self.active_run_file = self.runtime_dir / "active_run.json"
        self.active_agent_task_file = self.runtime_dir / "active_agent_task.json"
        self.notifications_file = self.runtime_dir / "notifications.json"
        self.chat_notifications_file = self.runtime_dir / "chat_notifications.json"
        self.project_chats_file = self.runtime_dir / "project_chats.json"
        self.bot_state_file = self.runtime_dir / "bot_state.json"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.agent_tasks_dir.mkdir(parents=True, exist_ok=True)

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

    def get_active_run(self) -> dict[str, Any] | None:
        return self.read_json(self.active_run_file)

    def set_active_run(self, payload: dict[str, Any]) -> None:
        self.write_json(self.active_run_file, payload)

    def clear_active_run(self) -> None:
        if self.active_run_file.exists():
            self.active_run_file.unlink()

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
