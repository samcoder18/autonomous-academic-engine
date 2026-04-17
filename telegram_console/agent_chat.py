from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os
import re
import subprocess
import sys

from .orchestrator import slugify, utc_now
from .projects import ProjectService
from .state import RuntimeStore


def parse_datetime(raw: str | None) -> datetime:
    if not raw:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def shorten_text(value: str | None, limit: int = 140) -> str:
    clean = re.sub(r"\s+", " ", (value or "").strip())
    if not clean:
        return ""
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


class AgentChatError(RuntimeError):
    """Raised when the project chat cannot continue."""


class AgentBusyError(AgentChatError):
    """Raised when another project chat turn is still running."""


@dataclass(frozen=True)
class ProjectChatState:
    project_id: str
    session_id: str | None = None
    last_activity_at: str | None = None
    last_user_message: str | None = None
    last_assistant_summary: str | None = None
    busy: bool = False
    last_export_path: str | None = None


@dataclass
class AgentTurnNotification:
    task_id: str
    project_id: str
    project_title: str
    status: str
    started_at: str
    finished_at: str | None = None
    prompt: str | None = None
    response_text: str | None = None
    summary: str | None = None
    session_id: str | None = None
    response_path: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    project_root: str | None = None
    reset_session: bool = False
    error: str | None = None

    @property
    def sort_key(self) -> tuple[float, str]:
        stamp = self.finished_at or self.started_at
        return (parse_datetime(stamp).timestamp(), self.task_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentChatService:
    def __init__(
        self,
        project_service: ProjectService,
        *,
        codex_bin: str | None = None,
        codex_model: str | None = None,
        python_executable: str | None = None,
        store: RuntimeStore | None = None,
    ):
        self.projects = project_service
        self.store = store or project_service.store
        self.bot_home_dir = project_service.bot_home_dir
        self.package_root = Path(__file__).resolve().parents[1]
        self.codex_bin = codex_bin or project_service.codex_bin
        self.codex_model = codex_model or project_service.codex_model
        self.python_executable = python_executable or sys.executable

    def get_project_state(self, project_id: str) -> ProjectChatState:
        payload = self.store.get_project_chat(project_id)
        active = self.store.get_active_agent_task()
        busy = bool(active and str(active.get("project_id") or "").strip() == project_id)
        return ProjectChatState(
            project_id=project_id,
            session_id=self._optional_text(payload.get("session_id")),
            last_activity_at=self._optional_text(payload.get("last_activity_at")),
            last_user_message=self._optional_text(payload.get("last_user_message")),
            last_assistant_summary=self._optional_text(payload.get("last_assistant_summary")),
            busy=busy or bool(payload.get("busy")),
            last_export_path=self._optional_text(payload.get("last_export_path")),
        )

    def describe_project_focus(self, project_id: str) -> str:
        active = self.store.get_active_agent_task()
        if active and str(active.get("project_id") or "").strip() == project_id:
            prompt = self._optional_text(active.get("prompt"))
            if prompt:
                return shorten_text(prompt, limit=140)
        state = self.get_project_state(project_id)
        if state.last_assistant_summary:
            return shorten_text(state.last_assistant_summary, limit=140)
        if state.last_user_message:
            return f"Ждет продолжения: {shorten_text(state.last_user_message, limit=110)}"
        return "Пока без истории. Напиши сообщением, что нужно сделать дальше."

    def start_turn(self, project_id: str, prompt: str) -> dict[str, Any]:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise AgentChatError("Сообщение получилось пустым. Пришли его еще раз одним текстом ✨")

        self.sync_active_task()
        active = self.store.get_active_agent_task()
        if active:
            raise AgentBusyError(self.describe_active_task(active))

        project = self.projects.require_project(project_id)
        state = self.get_project_state(project.id)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        task_token = f"{timestamp}-{slugify(project.id)}-chat"
        task_id = f"{project.id}:{timestamp}-chat"
        task_dir = self.store.agent_tasks_dir / task_token
        task_dir.mkdir(parents=True, exist_ok=True)

        request_payload = {
            "task_id": task_id,
            "project_id": project.id,
            "project_title": project.title,
            "project_root": str(project.root_dir),
            "prompt": clean_prompt,
            "session_id": state.session_id,
            "started_at": utc_now(),
            "codex_bin": self.codex_bin,
            "codex_model": self.codex_model,
        }
        self.store.write_json(task_dir / "request.json", request_payload)

        env = os.environ.copy()
        env["PYTHONPATH"] = self._build_pythonpath(env.get("PYTHONPATH"))
        wrapper_cmd = [
            self.python_executable,
            "-m",
            "telegram_console.chat_wrapper",
            "--task-dir",
            str(task_dir),
        ]
        process = subprocess.Popen(
            wrapper_cmd,
            cwd=self.package_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if process.poll() is None:
            process.returncode = 0

        active_payload = {
            "task_id": task_id,
            "task_dir": str(task_dir),
            "pid": process.pid,
            "project_id": project.id,
            "project_title": project.title,
            "project_root": str(project.root_dir),
            "started_at": request_payload["started_at"],
            "prompt": clean_prompt,
            "session_id": state.session_id,
        }
        self.store.set_active_agent_task(active_payload)
        self._save_project_state(
            project.id,
            {
                "session_id": state.session_id,
                "last_activity_at": request_payload["started_at"],
                "last_user_message": clean_prompt,
                "last_assistant_summary": state.last_assistant_summary,
                "busy": True,
                "last_export_path": state.last_export_path,
            },
        )
        return active_payload

    def sync_active_task(self) -> list[AgentTurnNotification]:
        active = self.store.get_active_agent_task()
        if not active:
            return []

        task_dir = Path(str(active.get("task_dir") or "")).resolve()
        request = self.store.read_json(task_dir / "request.json", default={}) or {}
        result = self.store.read_json(task_dir / "result.json")
        if result is None and self._pid_is_alive(int(active.get("pid", 0))):
            return []

        if result is None:
            result = {
                "started_at": request.get("started_at"),
                "finished_at": utc_now(),
                "returncode": None,
                "status": "failed",
                "stdout_path": str(task_dir / "codex.stdout.jsonl"),
                "stderr_path": str(task_dir / "codex.stderr.log"),
                "error": "Процесс завершился без result.json.",
            }
            self.store.write_json(task_dir / "result.json", result)

        notification = self._finalize_task(request, result)
        self.store.clear_active_agent_task()
        self.store.append_chat_notification(notification.to_dict())
        return [notification]

    def drain_notifications(self) -> list[AgentTurnNotification]:
        return [AgentTurnNotification(**payload) for payload in self.store.pop_chat_notifications()]

    def record_export(self, project_id: str, export_path: str | Path) -> None:
        state = self.get_project_state(project_id)
        self._save_project_state(
            project_id,
            {
                "session_id": state.session_id,
                "last_activity_at": utc_now(),
                "last_user_message": state.last_user_message,
                "last_assistant_summary": state.last_assistant_summary,
                "busy": False,
                "last_export_path": str(Path(export_path).resolve()),
            },
        )

    def reset_project_session(self, project_id: str) -> ProjectChatState:
        state = self.get_project_state(project_id)
        self._save_project_state(
            project_id,
            {
                "session_id": None,
                "last_activity_at": state.last_activity_at,
                "last_user_message": state.last_user_message,
                "last_assistant_summary": state.last_assistant_summary,
                "busy": False,
                "last_export_path": state.last_export_path,
            },
        )
        return self.get_project_state(project_id)

    def describe_active_task(self, payload: dict[str, Any]) -> str:
        project_title = self._optional_text(payload.get("project_title")) or "без названия"
        prompt = shorten_text(self._optional_text(payload.get("prompt")), limit=120)
        lines = [
            "⏳ Я уже отвечаю в другом проекте.",
            f"📚 Проект: {project_title}",
        ]
        if prompt:
            lines.append(f"🧠 Сейчас в работе: {prompt}")
        lines.append("Дай мне закончить этот ответ и потом пришли следующий запрос 🙌")
        return "\n".join(lines)

    def _finalize_task(
        self,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> AgentTurnNotification:
        project_id = self._optional_text(request.get("project_id")) or "default"
        existing = self.get_project_state(project_id)
        response_path = self._optional_text(result.get("response_path"))
        response_text = self._optional_text(result.get("response_text"))
        if not response_text and response_path:
            path = Path(response_path)
            if path.exists():
                response_text = path.read_text(encoding="utf-8").strip() or None

        status = self._optional_text(result.get("status")) or "failed"
        error_text = self._optional_text(result.get("error"))
        summary_source = response_text if status == "success" else error_text or "Не удалось получить ответ."
        summary = shorten_text(summary_source, limit=220) or None
        session_id = self._optional_text(result.get("thread_id"))
        if not session_id and status == "success":
            session_id = existing.session_id

        self._save_project_state(
            project_id,
            {
                "session_id": session_id,
                "last_activity_at": self._optional_text(result.get("finished_at")) or utc_now(),
                "last_user_message": self._optional_text(request.get("prompt")) or existing.last_user_message,
                "last_assistant_summary": summary,
                "busy": False,
                "last_export_path": existing.last_export_path,
            },
        )

        return AgentTurnNotification(
            task_id=self._optional_text(request.get("task_id")) or "chat-task",
            project_id=project_id,
            project_title=self._optional_text(request.get("project_title")) or project_id,
            status=status,
            started_at=self._optional_text(result.get("started_at")) or self._optional_text(request.get("started_at")) or utc_now(),
            finished_at=self._optional_text(result.get("finished_at")),
            prompt=self._optional_text(request.get("prompt")),
            response_text=response_text,
            summary=summary,
            session_id=session_id,
            response_path=response_path,
            stdout_path=self._optional_text(result.get("stdout_path")),
            stderr_path=self._optional_text(result.get("stderr_path")),
            project_root=self._optional_text(request.get("project_root")),
            reset_session=bool(result.get("reset_session")),
            error=error_text,
        )

    def _save_project_state(self, project_id: str, payload: dict[str, Any]) -> None:
        self.store.set_project_chat(project_id, payload)

    def _build_pythonpath(self, existing: str | None) -> str:
        roots = [str(self.package_root)]
        if existing:
            roots.append(existing)
        return os.pathsep.join(roots)

    def _pid_is_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _optional_text(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
