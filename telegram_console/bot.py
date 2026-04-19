from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from .agent_chat import AgentBusyError, AgentChatError, AgentChatService, AgentTurnNotification
from .config import TelegramConsoleConfig
from .email_delivery import EmailDeliveryError, SmtpDocxSender
from .launchd_service import LaunchdServiceError, LaunchdServiceManager
from .ops_alerts import OpsAlertSink, configure_default_sink
from .orchestrator import RunRecord, WorkflowError, action_title, lane_title
from .projects import ProjectRecord, ProjectRegistrationResult, ProjectService
from .runtime_status import load_runtime_record
from .telegram_api import TelegramApiError, TelegramBotApi
from .utils import shorten_text, split_message
from .work_state import format_work_state_dashboard_lines

MAIN_MENU = (
    ("📚 Проекты",),
    ("🗂 Работы",),
    ("📦 Экспорт",),
)

PROJECT_CAPABILITY_LABELS = {
    "thesis": "🎓 диплом",
    "article": "📝 статья",
}


def reply_keyboard(rows: tuple[tuple[str, ...], ...]) -> dict[str, Any]:
    return {
        "keyboard": [[{"text": label} for label in row] for row in rows],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": callback_data} for text, callback_data in row] for row in rows
        ]
    }


def default_bot_home() -> Path:
    return Path(__file__).resolve().parents[1]


class TelegramConsoleBot:
    def __init__(
        self,
        config: TelegramConsoleConfig,
        api: TelegramBotApi,
        projects: ProjectService,
        chat: AgentChatService,
        mailer: SmtpDocxSender | None = None,
    ):
        self.config = config
        self.api = api
        self.projects = projects
        self.chat = chat
        self.mailer = mailer
        self.store = projects.store
        self.main_menu_markup = reply_keyboard(MAIN_MENU)

    def run_forever(self) -> int:
        while True:
            self.tick()
            offset = self.store.get_last_update_id()
            try:
                updates = self.api.get_updates(offset=offset, timeout=self.config.poll_timeout)
            except TelegramApiError as exc:
                print(f"Telegram polling error: {exc}", file=sys.stderr)
                continue

            if not updates:
                continue

            for update in updates:
                update_id = int(update["update_id"])
                self.store.set_last_update_id(update_id + 1)
                try:
                    self.handle_update(update)
                except Exception as exc:
                    print(traceback.format_exc(), file=sys.stderr)
                    if not isinstance(
                        exc,
                        (TelegramApiError, KeyError, TypeError, ValueError, OSError),
                    ):
                        print(
                            f"(unexpected exception in handle_update: {type(exc).__name__})",
                            file=sys.stderr,
                        )
                    self.safe_send(
                        self.config.allowed_chat_id,
                        "Ой, я споткнулась ⚠️\nПроверь `output/telegram/runtime/` и stderr процесса.",
                        reply_markup=self.main_menu_markup,
                    )
            self.tick()

    def tick(self) -> None:
        self.projects.sync_active_run()
        for notification in self.projects.drain_notifications():
            self._send_workflow_notification(notification)
        self.chat.sync_active_task()
        for notification in self.chat.drain_notifications():
            self._send_chat_notification(notification)

    def handle_update(self, update: dict[str, Any]) -> None:
        if "message" in update:
            self._handle_message(update["message"])
            return
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])

    def safe_send(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        try:
            self.api.send_message(chat_id, text, reply_markup=reply_markup)
        except TelegramApiError as exc:
            print(f"Telegram sendMessage error: {exc}", file=sys.stderr)

    def safe_send_long(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        chunks = split_message(text)
        if not chunks:
            return
        for index, chunk in enumerate(chunks):
            markup = reply_markup if index == len(chunks) - 1 else None
            self.safe_send(chat_id, chunk, reply_markup=markup)

    def _handle_message(self, message: dict[str, Any]) -> None:
        chat_id = int(message["chat"]["id"])
        text = (message.get("text") or "").strip()
        if not text:
            return
        if not self._is_authorized(chat_id):
            self.safe_send(chat_id, "⛔ Этот бот работает только в разрешенном чате.")
            return

        if text.startswith("/start"):
            self._show_dashboard(chat_id)
            return
        if text == "📚 Проекты":
            self._show_projects_menu(chat_id)
            return
        if text == "🗂 Работы":
            self._show_works_menu(chat_id)
            return
        if text == "📦 Экспорт":
            self._export_active_project(chat_id)
            return

        self._handle_chat_prompt(chat_id, text)

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback["id"]
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id", callback["from"]["id"]))
        if not self._is_authorized(chat_id):
            self.api.answer_callback_query(callback_id, "⛔ Нет доступа")
            return

        data = callback.get("data", "")
        try:
            self._dispatch_callback(chat_id, data)
            self.api.answer_callback_query(callback_id)
        except WorkflowError as exc:
            self.api.answer_callback_query(callback_id, "Упс")
            self.safe_send(chat_id, str(exc), reply_markup=self.main_menu_markup)
        except TelegramApiError as exc:
            print(f"Telegram callback error: {exc}", file=sys.stderr)

    def _dispatch_callback(self, chat_id: int, data: str) -> None:
        if data == "nav:projects":
            self._show_projects_menu(chat_id)
            return
        if data == "nav:works":
            self._show_works_menu(chat_id)
            return
        if data == "nav:export":
            self._export_active_project(chat_id)
            return
        if data.startswith("project:use:"):
            self._select_project(chat_id, data.split(":", 2)[2])
            return
        if data.startswith("work:use:"):
            _, _, project_id, work_id = data.split(":", 3)
            self._select_work(chat_id, project_id, work_id)
            return
        self.safe_send(
            chat_id,
            "Это старое меню уже устарело. Открываю актуальную панель ✨",
            reply_markup=self.main_menu_markup,
        )
        self._show_dashboard(chat_id)

    def _handle_chat_prompt(self, chat_id: int, text: str) -> None:
        project = self._ensure_active_project(chat_id)
        if not project:
            return
        try:
            active = self.chat.start_turn(project.id, text)
        except AgentBusyError as exc:
            self.safe_send(chat_id, str(exc), reply_markup=self.main_menu_markup)
            return
        except AgentChatError as exc:
            self.safe_send(chat_id, str(exc), reply_markup=self.main_menu_markup)
            return
        except WorkflowError as exc:
            self.safe_send(chat_id, str(exc), reply_markup=self.main_menu_markup)
            return

        self.safe_send(
            chat_id,
            "\n".join(
                [
                    "⏳ Беру это в работу",
                    f"📚 Проект: {project.title} (`{project.id}`)",
                    f"🗂 Работа: {active.get('work_title') or 'по умолчанию'} (`{active.get('work_id') or 'default'}`)",
                    f"🧭 Режим: {active.get('detected_intent') or 'ответ'}",
                    f"🎯 Ожидаю: {active.get('expected_output') or 'содержательный результат по запросу'}",
                    f"🧠 Запрос: {shorten_text(active.get('prompt'), limit=160)}",
                    "",
                    "Ответ пришлю отдельным сообщением, как только закончу ✨",
                ]
            ),
            reply_markup=self.main_menu_markup,
        )

    def _show_dashboard(self, chat_id: int, intro: str | None = None) -> None:
        projects = self.projects.list_projects()
        current = self.projects.get_active_project()
        available = [project for project in projects if project.available]

        lines = [
            "🌿 Удаленный Codex",
            "Пиши мне обычным сообщением, и я продолжу работу внутри активного проекта.",
        ]
        if intro:
            lines.extend(["", intro])

        if current:
            try:
                active_work = self.projects.get_active_work(current.id)
                active_work_line = f"🗂 Активная работа: {active_work.title} (`{active_work.slug}`)"
            except WorkflowError:
                active_work = None
                active_work_line = "🗂 Активная работа пока не определена."
            lines.extend(
                [
                    "",
                    f"📚 Активный проект: {current.title} (`{current.id}`)",
                    active_work_line,
                    f"Статус агента: {self._agent_status_label(current)}",
                    f"Что сейчас в разработке: {self.chat.describe_project_focus(current.id)}",
                ]
            )
            if active_work:
                try:
                    lines.extend(format_work_state_dashboard_lines(self.projects.get_work_state(current.id)))
                except WorkflowError:
                    pass
        else:
            lines.extend(
                [
                    "",
                    "📚 Активный проект пока не выбран.",
                    "Нажми `📚 Проекты`, чтобы переключить контекст.",
                ]
            )

        lines.extend(["", "Действующие проекты:"])
        if available:
            for project in available:
                lines.extend(self._project_card_lines(project, current))
        else:
            lines.append("Пока не вижу ни одного рабочего проекта.")
            lines.append(f"Проверь реестр: {self.projects.projects_file}")

        lines.extend(["", "Снизу всегда доступны `📚 Проекты`, `🗂 Работы` и `📦 Экспорт`."])
        self.safe_send(chat_id, "\n".join(lines), reply_markup=self.main_menu_markup)

    def _show_projects_menu(self, chat_id: int, intro: str | None = None) -> None:
        projects = self.projects.list_projects()
        current = self.projects.get_active_project()
        available = [project for project in projects if project.available]
        unavailable = [project for project in projects if not project.available]

        lines = ["📚 Проекты"]
        if intro:
            lines.extend([intro, ""])
        if current:
            lines.append(f"Сейчас активен: {current.title} (`{current.id}`)")
        else:
            lines.append("Активный проект пока не выбран.")

        if available:
            lines.extend(["", "Что можно открыть прямо сейчас:"])
            for project in available:
                lines.extend(self._project_card_lines(project, current))
        else:
            lines.extend(["", "Пока здесь пусто 🌿", f"Проверь реестр: {self.projects.projects_file}"])

        if unavailable:
            lines.extend(["", "Что сейчас недоступно:"])
            for project in unavailable:
                lines.append(f"⚠️ {project.title} — `{project.id}`")
                for problem in project.problems[:2]:
                    lines.append(f"— {problem}")

        buttons = [
            [(self._project_button_label(project, current), f"project:use:{project.id}")] for project in available
        ]
        if available:
            buttons.append([("📦 Экспорт активного проекта", "nav:export")])
        reply_markup = inline_keyboard(buttons) if buttons else self.main_menu_markup
        self.safe_send(chat_id, "\n".join(lines), reply_markup=reply_markup)

    def _show_works_menu(self, chat_id: int, intro: str | None = None) -> None:
        project = self._ensure_active_project(chat_id)
        if not project:
            return
        works = self.projects.list_works(project.id)
        try:
            current_work = self.projects.get_active_work(project.id)
        except WorkflowError:
            current_work = None

        lines = ["🗂 Работы"]
        if intro:
            lines.extend([intro, ""])
        lines.append(f"📚 Проект: {project.title} (`{project.id}`)")
        lines.append(
            f"Сейчас активна: {current_work.title} (`{current_work.slug}`)"
            if current_work
            else "Активная работа пока не выбрана."
        )

        if works:
            lines.extend(["", "Что можно открыть прямо сейчас:"])
            for work in works:
                marker = "✅" if current_work and current_work.slug == work.slug else "📘"
                lines.append(f"{marker} {work.title} — `{work.slug}`")
                lines.append(f"Тип: {work.artifact_type}")
                lines.append(f"Контуры: {', '.join(work.active_lanes)}")
                lines.append("")
        else:
            lines.extend(["", "Пока не вижу ни одного work bundle для этого проекта."])

        buttons = [
            [
                (
                    self._work_button_label(
                        project.id, work.slug, current_work.slug if current_work else None, work.title
                    ),
                    f"work:use:{project.id}:{work.slug}",
                )
            ]
            for work in works
        ]
        if works:
            buttons.append([("📦 Экспорт активной работы", "nav:export")])
        reply_markup = inline_keyboard(buttons) if buttons else self.main_menu_markup
        self.safe_send(chat_id, "\n".join(lines), reply_markup=reply_markup)

    def _ensure_active_project(self, chat_id: int) -> ProjectRecord | None:
        project = self.projects.get_active_project()
        if project:
            return project
        self._show_projects_menu(chat_id, "Сначала выбери активный проект 📚")
        return None

    def _select_project(self, chat_id: int, project_id: str) -> None:
        try:
            project = self.projects.set_active_project(project_id)
            work = self.projects.get_active_work(project.id)
        except WorkflowError as exc:
            self.safe_send(chat_id, str(exc), reply_markup=self.main_menu_markup)
            return
        self._show_dashboard(
            chat_id,
            intro="\n".join(
                [
                    "✅ Проект переключен",
                    f"Теперь работаю в контексте: {project.title} (`{project.id}`)",
                    f"Активная работа: {work.title} (`{work.slug}`)",
                ]
            ),
        )

    def _select_work(self, chat_id: int, project_id: str, work_id: str) -> None:
        try:
            project = self.projects.require_project(project_id)
            work = self.projects.set_active_work(project.id, work_id)
            self.projects.set_active_project(project.id)
            self.chat.mark_work_switch(project.id)
        except WorkflowError as exc:
            self.safe_send(chat_id, str(exc), reply_markup=self.main_menu_markup)
            return
        self._show_dashboard(
            chat_id,
            intro="\n".join(
                [
                    "✅ Активная работа переключена",
                    f"📚 Проект: {project.title} (`{project.id}`)",
                    f"🗂 Теперь активна: {work.title} (`{work.slug}`)",
                ]
            ),
        )

    def _export_active_project(self, chat_id: int) -> None:
        project = self._ensure_active_project(chat_id)
        if not project:
            return
        try:
            subject = self._resolve_export_subject(project)
        except WorkflowError as exc:
            self.safe_send(chat_id, str(exc), reply_markup=self.main_menu_markup)
            return
        self._run_export(chat_id, project.id, subject)

    def _resolve_export_subject(self, project: ProjectRecord) -> str:
        active_work = self.projects.get_active_work(project.id)
        if active_work.supports("thesis"):
            return "thesis"
        if not active_work.supports("article"):
            raise WorkflowError("Для этого проекта пока не найден понятный сценарий экспорта.")

        ready_slugs = [
            slug
            for slug in self.projects.list_article_slugs(project.id)
            if self.projects.get_artifact_status(project.id, f"article:{slug}")["files"]["final"]["exists"]
        ]
        if len(ready_slugs) == 1:
            return f"article:{ready_slugs[0]}"
        if len(ready_slugs) > 1:
            raise WorkflowError(
                "В проекте несколько готовых статей. Для этой версии `📦 Экспорт` нужен один главный итоговый файл."
            )
        raise WorkflowError("Пока не вижу готового итогового файла для экспорта.")

    def _run_export(self, chat_id: int, project_id: str, subject: str) -> None:
        try:
            project = self.projects.require_project(project_id)
            work = self.projects.get_active_work(project.id)
            result = self.projects.export_docx(project.id, subject)
        except WorkflowError as exc:
            self.safe_send(chat_id, str(exc), reply_markup=self.main_menu_markup)
            return

        path = Path(result["path"])
        self.chat.record_export(project.id, path)
        artifact_kind = "диплом" if subject == "thesis" else "статья"
        self.safe_send(
            chat_id,
            "\n".join(
                [
                    "📦 Экспорт готов",
                    f"📚 Проект: {project.title}",
                    f"🗂 Работа: {work.title} (`{work.slug}`)",
                    f"📄 Файл: {path}",
                    "Сейчас отправлю его отдельным сообщением 👇",
                ]
            ),
            reply_markup=self.main_menu_markup,
        )
        if path.exists():
            try:
                self.api.send_document(chat_id, path, caption=f"📄 Готовый файл: {path.name}")
            except TelegramApiError as exc:
                self.safe_send(chat_id, f"Не получилось отправить файл 😔\n{exc}")
                return
            if self.mailer:
                try:
                    self.mailer.send_export(path, artifact_kind)
                except EmailDeliveryError as exc:
                    self.safe_send(
                        chat_id,
                        f"⚠️ Копия на почту пока не отправилась.\n{exc}",
                        reply_markup=self.main_menu_markup,
                    )
                else:
                    recipient = getattr(self.mailer, "recipient_email", None)
                    suffix = f" на {recipient}" if recipient else ""
                    self.safe_send(
                        chat_id,
                        f"📮 Копия DOCX еще и ушла на почту{suffix} ✨",
                        reply_markup=self.main_menu_markup,
                    )

    def _send_chat_notification(self, notification: AgentTurnNotification) -> None:
        if notification.status == "success":
            header = [
                "💬 Ответ готов",
                f"📚 Проект: {notification.project_title}",
            ]
            if notification.work_title:
                header.append(f"🗂 Работа: {notification.work_title} (`{notification.work_id}`)")
            if notification.reset_session:
                header.append("⚠️ Контекст чата пришлось пересобрать, но разговор уже продолжился в новой сессии.")
            self.safe_send(self.config.allowed_chat_id, "\n".join(header))
            response_text = notification.response_text or "Ответ пустой, но задача завершилась без ошибки."
            self.safe_send_long(
                self.config.allowed_chat_id,
                response_text,
                reply_markup=self.main_menu_markup,
            )
            return

        lines = [
            "⚠️ Не получилось получить ответ Codex",
            f"📚 Проект: {notification.project_title}",
        ]
        if notification.work_title:
            lines.append(f"🗂 Работа: {notification.work_title} (`{notification.work_id}`)")
        if notification.reset_session:
            lines.append("Похоже, старая сессия сломалась. Следующее сообщение начнет новый контекст.")
        if notification.error:
            lines.append(f"Причина: {notification.error}")
        lines.append("Можешь просто отправить запрос еще раз, и я попробую снова.")
        self.safe_send(self.config.allowed_chat_id, "\n".join(lines), reply_markup=self.main_menu_markup)

    def _send_workflow_notification(self, notification: RunRecord) -> None:
        lines = [
            "✅ Workflow завершен" if notification.status == "success" else "⚠️ Workflow завершился с проблемой",
            f"📚 Проект: {notification.project_title or notification.project_id or 'не указан'}",
        ]
        if notification.work_title:
            lines.append(f"🗂 Работа: {notification.work_title} (`{notification.work_id}`)")
        lines.append(f"Контур: {lane_title(notification.lane)} · {action_title(notification.action)}")
        runtime_record = self._load_workflow_runtime_record(notification)
        if runtime_record is not None:
            runtime_summary = _format_runtime_lane_summary(runtime_record)
            if runtime_summary:
                lines.append(runtime_summary)
            resolution_warning = _format_target_resolution_line(runtime_record)
            if resolution_warning:
                lines.append(resolution_warning)
        if notification.summary:
            lines.append(f"Summary: {notification.summary}")
        self.safe_send(self.config.allowed_chat_id, "\n".join(lines), reply_markup=self.main_menu_markup)

    def _load_workflow_runtime_record(self, notification: RunRecord) -> Any | None:
        if not notification.runtime_run_dir:
            return None
        return load_runtime_record(Path(notification.runtime_run_dir), "workflow-run")

    def _project_card_lines(
        self,
        project: ProjectRecord,
        current: ProjectRecord | None,
    ) -> list[str]:
        mark = "✅" if current and current.id == project.id else "📘"
        work_label = "не выбрана"
        try:
            active_work = self.projects.get_active_work(project.id)
        except WorkflowError:
            active_work = None
        if active_work:
            work_label = f"{active_work.title} (`{active_work.slug}`)"
        return [
            f"{mark} {project.title} — `{project.id}`",
            f"Работа: {work_label}",
            f"Контуры: {self._project_capabilities(project)}",
            f"Статус: {self._agent_status_label(project)}",
            f"В работе: {self.chat.describe_project_focus(project.id)}",
            "",
        ]

    def _agent_status_label(self, project: ProjectRecord) -> str:
        if not project.available:
            return "⚠️ недоступен"
        state = self.chat.get_project_state(project.id)
        return "⏳ занят" if state.busy else "✅ свободен"

    def _project_button_label(self, project: ProjectRecord, current: ProjectRecord | None) -> str:
        if current and current.id == project.id:
            return f"✅ {project.title} · {project.id}"
        return f"📚 {project.title} · {project.id}"

    def _work_button_label(
        self,
        project_id: str,
        work_id: str,
        current_work_id: str | None,
        title: str,
    ) -> str:
        if current_work_id == work_id:
            return f"✅ {title} · {work_id}"
        return f"🗂 {title} · {work_id}"

    def _project_capabilities(self, project: ProjectRecord) -> str:
        labels = [PROJECT_CAPABILITY_LABELS.get(item, item) for item in project.capabilities]
        return " + ".join(labels) if labels else "без сценариев"

    def _is_authorized(self, chat_id: int) -> bool:
        return chat_id == self.config.allowed_chat_id


def build_bot(root_dir: str | Path | None = None) -> TelegramConsoleBot:
    config = TelegramConsoleConfig.from_env(root_dir)
    projects = ProjectService(
        config.bot_home_dir,
        codex_bin=config.codex_bin,
        codex_model=config.codex_model,
    )
    chat = AgentChatService(
        projects,
        codex_bin=config.codex_bin,
        codex_model=config.codex_model,
    )
    api = TelegramBotApi(config.token)
    mailer = SmtpDocxSender(config.smtp_settings) if config.smtp_settings else None
    _configure_ops_alerts_sink(api)
    return TelegramConsoleBot(config, api, projects, chat, mailer=mailer)


def _configure_ops_alerts_sink(api: TelegramBotApi) -> None:
    """Wire the Telegram API into the ops-alerts sink.

    The ops channel is intentionally separate from user-facing notifications:
    it honours ``OPS_ALERT_CHAT_ID`` (Telegram chat id) and
    ``OPS_ALERT_LOG_PATH`` (file path) env vars. If neither is set, alerts
    fall back to stderr — so local runs stay quiet.
    """
    import os

    chat_id_raw = os.environ.get("OPS_ALERT_CHAT_ID")
    chat_id: str | int | None
    if chat_id_raw:
        try:
            chat_id = int(chat_id_raw)
        except ValueError:
            chat_id = chat_id_raw
    else:
        chat_id = None

    log_path_raw = os.environ.get("OPS_ALERT_LOG_PATH")
    log_path = Path(log_path_raw).expanduser() if log_path_raw else None

    def _telegram_sender(target_chat_id: str | int, text: str) -> None:
        try:
            numeric_chat_id = int(target_chat_id)
        except (TypeError, ValueError):
            return
        try:
            api.send_message(numeric_chat_id, text)
        except TelegramApiError:
            # Best-effort delivery; ops_alerts already logs the failure.
            return

    sink = OpsAlertSink(
        chat_id=chat_id,
        log_path=log_path,
        sender=_telegram_sender if chat_id else None,
    )
    configure_default_sink(sink)


def handle_project_add(
    *,
    bot_home: str | Path | None,
    title: str,
    project_root: str | Path,
) -> int:
    service = ProjectService(Path(bot_home or default_bot_home()).resolve())
    try:
        result = service.register_project(title, project_root)
    except WorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(_format_project_registration_result(result))
    return 0


def _format_project_registration_result(result: ProjectRegistrationResult) -> str:
    headline = "Проект добавлен ✅" if result.created else "Проект уже есть в реестре ✅"
    return "\n".join(
        [
            headline,
            f"Название: {result.project.title}",
            f"ID: {result.project.id}",
            f"Путь: {result.project.root_dir}",
            f"Возможности: {', '.join(result.project.capabilities)}",
            f"Работы: {', '.join(result.project.works) if result.project.works else 'не найдены'}",
            f"Default work: {result.project.default_work or 'не указан'}",
        ]
    )


def handle_service_command(*, bot_home: str | Path | None, action: str) -> int:
    manager = LaunchdServiceManager(Path(bot_home or default_bot_home()).resolve())
    try:
        if action == "install":
            print(manager.format_install_result(manager.install()))
            return 0
        if action == "start":
            print(manager.format_status(manager.start()))
            return 0
        if action == "stop":
            print(manager.format_status(manager.stop()))
            return 0
        if action == "restart":
            print(manager.format_status(manager.restart()))
            return 0
        if action == "status":
            print(manager.format_status(manager.status()))
            return 0
        if action == "uninstall":
            print(manager.format_status(manager.uninstall()))
            return 0
    except LaunchdServiceError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Неизвестная service-команда: {action}", file=sys.stderr)
    return 1


def handle_runtime_command(
    *,
    bot_home: str | Path | None,
    action: str,
    project_id: str | None = None,
    kind: str = "all",
    limit: int = 8,
    record_id: str | None = None,
    attachment: str | None = None,
    as_json: bool = False,
) -> int:
    service = ProjectService(Path(bot_home or default_bot_home()).resolve())
    try:
        if action == "status":
            records = service.list_runtime_records(project_id=project_id, kind=kind, limit=limit)
            if as_json:
                print(json.dumps({"records": [record.to_dict() for record in records]}, ensure_ascii=False, indent=2))
                return 0
            print(_format_runtime_records(records))
            return 0

        if action == "show":
            if not record_id:
                print("Нужен record-id для runtime show.", file=sys.stderr)
                return 1
            record = service.find_runtime_record(record_id, project_id=project_id)
            if not record:
                print(f"Не найден runtime record: {record_id}", file=sys.stderr)
                return 1
            if as_json:
                print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
                return 0
            print(_format_runtime_record(record))
            return 0

        if action == "path":
            if not record_id or not attachment:
                print("Нужны record-id и attachment для runtime path.", file=sys.stderr)
                return 1
            path = service.get_runtime_attachment(record_id, attachment, project_id=project_id)
            if not path:
                print(f"Не найден attachment `{attachment}` для runtime record `{record_id}`.", file=sys.stderr)
                return 1
            print(str(path))
            return 0
    except WorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Неизвестная runtime-команда: {action}", file=sys.stderr)
    return 1


def _format_runtime_records(records: list[Any]) -> str:
    if not records:
        return "Runtime records не найдены."
    lines: list[str] = []
    for record in records:
        lane_summary = _format_runtime_lane_summary(record)
        resolution_warning = _format_target_resolution_line(record)
        lines.extend(
            [
                f"{record.record_id} [{record.entity_kind}]",
                f"Статус: {record.status} · stage={record.stage}",
                f"Проект: {record.project_title or record.project_id or 'не указан'}",
                f"Работа: {record.work_title or record.work_id or 'не указана'}",
                lane_summary,
                resolution_warning or "Resolution warning: none",
                f"Summary: {record.summary or 'нет'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _format_runtime_record(record: Any) -> str:
    lines = [
        f"Record: {record.record_id}",
        f"Kind: {record.entity_kind}",
        f"Status: {record.status}",
        f"Stage: {record.stage}",
        f"Project: {record.project_title or 'не указан'} ({record.project_id or 'n/a'})",
        f"Project root: {record.project_root or 'n/a'}",
        f"Work: {record.work_title or 'не указана'} ({record.work_id or 'n/a'})",
        f"Lane: {record.lane or 'n/a'}",
        f"Profile: {record.profile or 'n/a'}",
        f"Action: {record.action or 'n/a'}",
        f"Started: {record.started_at or 'n/a'}",
        f"Finished: {record.finished_at or 'n/a'}",
        f"Summary: {record.summary or 'нет'}",
    ]
    lane_summary = _format_runtime_lane_summary(record)
    if lane_summary:
        lines.append(lane_summary)
    resolution_warning = _format_target_resolution_line(record)
    if resolution_warning:
        lines.append(resolution_warning)
    if record.failure:
        lines.append(f"Failure: {json.dumps(record.failure, ensure_ascii=False)}")
    else:
        lines.append("Failure: none")
    if record.blockers:
        lines.append(f"Blockers: {json.dumps(list(record.blockers), ensure_ascii=False)}")
    else:
        lines.append("Blockers: none")
    if record.repair_decision:
        lines.append(f"Repair decision: {json.dumps(record.repair_decision, ensure_ascii=False)}")
    else:
        lines.append("Repair decision: none")
    gate_summary = _contract_gate_summary(getattr(record, "contract_gates", None))
    if gate_summary["total_count"]:
        lines.append(f"Contract gates: blocks={gate_summary['block_count']} warnings={gate_summary['warn_count']}")
    else:
        lines.append("Contract gates: none")
    lines.append(f"Repair iteration: {record.repair_iteration if record.repair_iteration is not None else 'n/a'}")
    lines.append(f"Terminal reason: {record.terminal_reason or 'n/a'}")
    lines.append("Attachments:")
    if record.attachments:
        for name, payload in sorted(record.attachments.items()):
            path = payload.get("path") if isinstance(payload, dict) else None
            exists = payload.get("exists") if isinstance(payload, dict) else None
            lines.append(f"- {name}: {path} (exists={exists})")
    else:
        lines.append("- none")
    return "\n".join(lines)


def _format_runtime_lane_summary(record: Any) -> str:
    if getattr(record, "entity_kind", None) != "workflow-run":
        return (
            "Repair: "
            f"iteration={getattr(record, 'repair_iteration', None) or 0}, "
            f"terminal_reason={getattr(record, 'terminal_reason', None) or 'n/a'}, "
            f"blockers={len(getattr(record, 'blockers', ()) or ())}"
        )
    summary_block = _load_runtime_summary_block(record)
    if isinstance(summary_block, dict):
        line = _format_summary_block_line(summary_block)
        gate_summary = _contract_gate_summary(getattr(record, "contract_gates", None))
        if gate_summary["total_count"]:
            line = f"{line} · gates={gate_summary['block_count']}/{gate_summary['warn_count']}"
        return line
    parts = [
        f"lane={getattr(record, 'lane', None) or 'n/a'}",
        f"action={getattr(record, 'action', None) or 'n/a'}",
        f"blockers={len(getattr(record, 'blockers', ()) or ())}",
        f"terminal_reason={getattr(record, 'terminal_reason', None) or 'n/a'}",
    ]
    repair_decision = getattr(record, "repair_decision", None)
    if isinstance(repair_decision, dict):
        parts.append(
            f"repair={repair_decision.get('action') or 'n/a'}@{getattr(record, 'repair_iteration', None) or 0}"
        )
    gate_summary = _contract_gate_summary(getattr(record, "contract_gates", None))
    if gate_summary["total_count"]:
        parts.append(f"gates={gate_summary['block_count']}/{gate_summary['warn_count']}")
    return "Lane summary: " + " · ".join(parts)


def _contract_gate_summary(gates: Any) -> dict[str, int]:
    if not isinstance(gates, (list, tuple)):
        return {"total_count": 0, "block_count": 0, "warn_count": 0}
    total = {"total_count": 0, "block_count": 0, "warn_count": 0}
    for item in gates:
        if not isinstance(item, dict):
            continue
        total["total_count"] += 1
        status = str(item.get("status") or "").strip()
        if status == "block":
            total["block_count"] += 1
        elif status == "warn":
            total["warn_count"] += 1
    return total


def _load_runtime_summary_block(record: Any) -> dict[str, Any] | None:
    payload = _load_runtime_resolution_payload(record)
    if not isinstance(payload, dict):
        return None
    for key in ("thesis_runtime", "article_runtime"):
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        summary_block = value.get("summary_block")
        if isinstance(summary_block, dict):
            return summary_block
    return None


def _load_runtime_resolution_payload(record: Any) -> dict[str, Any] | None:
    attachments = getattr(record, "attachments", None)
    if not isinstance(attachments, dict):
        return None
    resolution = attachments.get("resolution")
    if not isinstance(resolution, dict):
        return None
    raw_path = resolution.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path)
    if not candidate.exists():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _load_target_resolution(record: Any) -> dict[str, Any] | None:
    direct = getattr(record, "target_resolution", None)
    if isinstance(direct, dict):
        return direct
    payload = _load_runtime_resolution_payload(record)
    if not isinstance(payload, dict):
        return None
    target_resolution = payload.get("target_resolution")
    return target_resolution if isinstance(target_resolution, dict) else None


def _format_target_resolution_line(record: Any) -> str | None:
    target_resolution = _load_target_resolution(record)
    if not isinstance(target_resolution, dict):
        return None
    message = target_resolution.get("warning_message")
    if isinstance(message, str) and message.strip():
        return f"Resolution warning: {message.strip()}"
    return None


def _format_summary_block_line(summary_block: dict[str, Any]) -> str:
    kind = summary_block.get("kind")
    if kind == "thesis-section-summary":
        last_a = summary_block.get("last_run_action") or "n/a"
        last_s = summary_block.get("last_run_status") or "n/a"
        return (
            "Lane summary: "
            f"thesis target={summary_block.get('target') or 'n/a'}"
            f" · review={'yes' if summary_block.get('review_present') else 'no'}"
            f" · last_run={last_a}/{last_s}"
            f" · blockers={summary_block.get('blocker_count') or 0}"
            f" · terminal_reason={summary_block.get('terminal_reason') or 'n/a'}"
            f" · next={summary_block.get('suggested_next_action') or 'n/a'}"
        )
    if kind == "article-bundle-summary":
        return (
            "Lane summary: "
            f"article slug={summary_block.get('slug') or 'n/a'}"
            f" · phase={summary_block.get('current_phase') or 'n/a'}"
            f" · status={summary_block.get('current_status') or 'n/a'}"
            f" · blockers={summary_block.get('blocker_count') or 0}"
            f" · repair={summary_block.get('repair_action') or 'n/a'}@{summary_block.get('repair_iteration') or 0}"
            f" · review={'yes' if summary_block.get('review_present') else 'no'}"
            f" · checklist={'yes' if summary_block.get('checklist_present') else 'no'}"
            f" · next={summary_block.get('suggested_next_action') or 'n/a'}"
        )
    return "Lane summary: " + json.dumps(summary_block, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Telegram remote chat console for Codex.")
    parser.add_argument(
        "--root",
        "--bot-home",
        dest="bot_home",
        default=None,
        help="Bot home directory. Defaults to the repository root.",
    )
    subparsers = parser.add_subparsers(dest="command")
    project_parser = subparsers.add_parser("project", help="Manage the local project registry.")
    project_subparsers = project_parser.add_subparsers(dest="project_command")
    project_add_parser = project_subparsers.add_parser("add", help="Register an existing project in the bot registry.")
    project_add_parser.add_argument("--title", required=True, help="Human-readable project title.")
    project_add_parser.add_argument("--root", dest="project_root", required=True, help="Absolute path to the project.")
    runtime_parser = subparsers.add_parser("runtime", help="Inspect workflow/chat runtime artifacts.")
    runtime_subparsers = runtime_parser.add_subparsers(dest="runtime_command")
    runtime_status_parser = runtime_subparsers.add_parser("status", help="List recent runtime records.")
    runtime_status_parser.add_argument("--project", dest="project_id")
    runtime_status_parser.add_argument("--kind", choices=("workflow", "chat", "all"), default="all")
    runtime_status_parser.add_argument("--limit", type=int, default=8)
    runtime_status_parser.add_argument("--json", action="store_true", dest="as_json")
    runtime_show_parser = runtime_subparsers.add_parser("show", help="Show one runtime record.")
    runtime_show_parser.add_argument("record_id")
    runtime_show_parser.add_argument("--project", dest="project_id")
    runtime_show_parser.add_argument("--json", action="store_true", dest="as_json")
    runtime_path_parser = runtime_subparsers.add_parser("path", help="Print an attachment path for a runtime record.")
    runtime_path_parser.add_argument("record_id")
    runtime_path_parser.add_argument("attachment")
    runtime_path_parser.add_argument("--project", dest="project_id")
    service_parser = subparsers.add_parser("service", help="Manage the local macOS LaunchAgent.")
    service_subparsers = service_parser.add_subparsers(dest="service_command")
    for command, help_text in (
        ("install", "Install the local LaunchAgent and start it."),
        ("start", "Start the installed LaunchAgent."),
        ("stop", "Stop the LaunchAgent if it is running."),
        ("restart", "Restart the LaunchAgent."),
        ("status", "Show LaunchAgent status and log paths."),
        ("uninstall", "Unload and remove the LaunchAgent plist."),
    ):
        service_subparsers.add_parser(command, help=help_text)
    args = parser.parse_args(argv)
    if args.command == "project":
        if args.project_command == "add":
            return handle_project_add(
                bot_home=args.bot_home,
                title=args.title,
                project_root=args.project_root,
            )
        project_parser.print_help()
        return 1
    if args.command == "service":
        if args.service_command:
            return handle_service_command(
                bot_home=args.bot_home,
                action=args.service_command,
            )
        service_parser.print_help()
        return 1
    if args.command == "runtime":
        if args.runtime_command:
            return handle_runtime_command(
                bot_home=args.bot_home,
                action=args.runtime_command,
                project_id=getattr(args, "project_id", None),
                kind=getattr(args, "kind", "all"),
                limit=getattr(args, "limit", 8),
                record_id=getattr(args, "record_id", None),
                attachment=getattr(args, "attachment", None),
                as_json=getattr(args, "as_json", False),
            )
        runtime_parser.print_help()
        return 1

    bot = build_bot(args.bot_home)
    return bot.run_forever()
