from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import sys
import traceback

from .agent_chat import AgentBusyError, AgentChatError, AgentChatService, AgentTurnNotification
from .config import TelegramConsoleConfig
from .email_delivery import EmailDeliveryError, SmtpDocxSender
from .launchd_service import LaunchdServiceError, LaunchdServiceManager
from .orchestrator import WorkflowError
from .projects import ProjectRecord, ProjectRegistrationResult, ProjectService
from .telegram_api import TelegramApiError, TelegramBotApi
from .utils import shorten_text, split_message


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
            [{"text": text, "callback_data": callback_data} for text, callback_data in row]
            for row in rows
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
                except Exception:
                    print(traceback.format_exc(), file=sys.stderr)
                    self.safe_send(
                        self.config.allowed_chat_id,
                        "Ой, я споткнулась ⚠️\nПроверь `output/telegram/runtime/` и stderr процесса.",
                        reply_markup=self.main_menu_markup,
                    )
            self.tick()

    def tick(self) -> None:
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
            [(self._project_button_label(project, current), f"project:use:{project.id}")]
            for project in available
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
                    self._work_button_label(project.id, work.slug, current_work.slug if current_work else None, work.title),
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
    return TelegramConsoleBot(config, api, projects, chat, mailer=mailer)


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

    bot = build_bot(args.bot_home)
    return bot.run_forever()
