from __future__ import annotations

import html
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess

DEFAULT_SERVICE_LABEL = "com.albina.telegram-console"
REQUIRED_ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_ID")
_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_PID_RE = re.compile(r"\bpid\s*=\s*(\d+)\b")


class LaunchdServiceError(RuntimeError):
    """Raised when the local launchd integration cannot continue."""


@dataclass(frozen=True)
class LaunchdPaths:
    bot_home: Path
    label: str
    env_file: Path
    runtime_dir: Path
    stdout_log: Path
    stderr_log: Path
    wrapper_script: Path
    template_file: Path
    launch_agents_dir: Path
    installed_plist: Path


@dataclass(frozen=True)
class LaunchdStatus:
    label: str
    installed: bool
    loaded: bool
    pid: int | None
    env_configured: bool
    agent_path: Path
    env_file: Path
    stdout_log: Path
    stderr_log: Path
    note: str | None = None


@dataclass(frozen=True)
class LaunchdInstallResult:
    env_template_created: bool
    installed: bool
    status: LaunchdStatus
    note: str


CommandRunner = Callable[[list[str]], CompletedProcess[str]]


class LaunchdServiceManager:
    def __init__(
        self,
        bot_home: str | Path,
        *,
        label: str = DEFAULT_SERVICE_LABEL,
        home_dir: str | Path | None = None,
        uid: int | None = None,
        command_runner: CommandRunner | None = None,
    ):
        resolved_home = Path(bot_home).resolve()
        user_home = Path(home_dir or Path.home()).expanduser().resolve()
        launch_agents_dir = user_home / "Library" / "LaunchAgents"
        installed_plist = launch_agents_dir / f"{label}.plist"
        runtime_dir = resolved_home / "output" / "telegram" / "runtime"
        self.paths = LaunchdPaths(
            bot_home=resolved_home,
            label=label,
            env_file=resolved_home / "output" / "telegram" / ".env.launchd",
            runtime_dir=runtime_dir,
            stdout_log=runtime_dir / "bot.stdout.log",
            stderr_log=runtime_dir / "bot.stderr.log",
            wrapper_script=resolved_home / "scripts" / "run_academic_engine_launchd.sh",
            template_file=resolved_home / "deploy" / "local-academic-engine.plist",
            launch_agents_dir=launch_agents_dir,
            installed_plist=installed_plist,
        )
        self.uid = uid if uid is not None else os.getuid()
        self.command_runner = command_runner or self._default_command_runner

    def install(self) -> LaunchdInstallResult:
        self._ensure_directories()
        if not self.paths.env_file.exists():
            self._write_env_template()
            status = self.status(note="Создан шаблон env-файла. Заполни его и снова выполни `service install`.")
            return LaunchdInstallResult(
                env_template_created=True,
                installed=False,
                status=status,
                note="Создан шаблон env-файла для LaunchAgent.",
            )

        missing = self._missing_env_keys()
        if missing:
            raise LaunchdServiceError(
                "\n".join(
                    [
                        "Файл `.env.launchd` найден, но заполнен не до конца ⚠️",
                        f"Путь: {self.paths.env_file}",
                        f"Не хватает: {', '.join(missing)}",
                    ]
                )
            )

        self._render_plist()
        self._bootout_if_loaded()
        self._run_launchctl(["bootstrap", self._launch_domain(), str(self.paths.installed_plist)])
        self._run_launchctl(["kickstart", "-k", self._service_target()])
        status = self.status(note="LaunchAgent установлен и запущен.")
        return LaunchdInstallResult(
            env_template_created=False,
            installed=True,
            status=status,
            note="LaunchAgent установлен и запущен.",
        )

    def start(self) -> LaunchdStatus:
        self._require_installed()
        self._require_env_configured()
        if not self.status().loaded:
            self._run_launchctl(["bootstrap", self._launch_domain(), str(self.paths.installed_plist)])
        self._run_launchctl(["kickstart", self._service_target()])
        return self.status(note="LaunchAgent запущен.")

    def stop(self) -> LaunchdStatus:
        if self.paths.installed_plist.exists():
            self._bootout_if_loaded()
        return self.status(note="LaunchAgent остановлен.")

    def restart(self) -> LaunchdStatus:
        self._require_installed()
        self._require_env_configured()
        current = self.status()
        if current.loaded:
            self._run_launchctl(["kickstart", "-k", self._service_target()])
        else:
            self._run_launchctl(["bootstrap", self._launch_domain(), str(self.paths.installed_plist)])
            self._run_launchctl(["kickstart", self._service_target()])
        return self.status(note="LaunchAgent перезапущен.")

    def uninstall(self) -> LaunchdStatus:
        if self.paths.installed_plist.exists():
            self._bootout_if_loaded()
            self.paths.installed_plist.unlink()
        return self.status(note="LaunchAgent удален. Env-файл сохранен.")

    def status(self, note: str | None = None) -> LaunchdStatus:
        installed = self.paths.installed_plist.exists()
        env_configured = self.paths.env_file.exists() and not self._missing_env_keys()
        loaded = False
        pid = None
        if installed:
            result = self.command_runner(["launchctl", "print", self._service_target()])
            if result.returncode == 0:
                loaded = True
                pid = self._parse_pid(result.stdout)
        return LaunchdStatus(
            label=self.paths.label,
            installed=installed,
            loaded=loaded,
            pid=pid,
            env_configured=env_configured,
            agent_path=self.paths.installed_plist,
            env_file=self.paths.env_file,
            stdout_log=self.paths.stdout_log,
            stderr_log=self.paths.stderr_log,
            note=note,
        )

    def format_status(self, status: LaunchdStatus) -> str:
        lines = [
            "Статус локального LaunchAgent",
            f"Label: {status.label}",
            f"Установлен: {'да' if status.installed else 'нет'}",
            f"Загружен в launchd: {'да' if status.loaded else 'нет'}",
            f"Env готов: {'да' if status.env_configured else 'нет'}",
            f"Plist: {status.agent_path}",
            f"Env: {status.env_file}",
            f"Stdout: {status.stdout_log}",
            f"Stderr: {status.stderr_log}",
        ]
        if status.pid is not None:
            lines.append(f"PID: {status.pid}")
        if status.note:
            lines.extend(["", status.note])
        return "\n".join(lines)

    def format_install_result(self, result: LaunchdInstallResult) -> str:
        headline = "LaunchAgent установлен ✅" if result.installed else "Шаблон env-файла создан ✅"
        return "\n".join([headline, "", self.format_status(result.status)])

    def _ensure_directories(self) -> None:
        self.paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.paths.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.launch_agents_dir.mkdir(parents=True, exist_ok=True)

    def _write_env_template(self) -> None:
        template = "\n".join(
            [
                "# Локальные секреты и настройки для launchd-запуска Telegram Console",
                "TELEGRAM_BOT_TOKEN=",
                "TELEGRAM_ALLOWED_CHAT_ID=",
                "",
                "# Опционально",
                "# CODEX_BIN=",
                "# CODEX_MODEL=",
                "# TELEGRAM_POLL_TIMEOUT=30",
                "# SMTP_HOST=",
                "# SMTP_PORT=587",
                "# SMTP_USERNAME=",
                "# SMTP_PASSWORD=",
                "# SMTP_SECURITY=starttls",
                "# SMTP_FROM_EMAIL=",
                "# SMTP_FROM_NAME=Академический штурман",
                "# SMTP_TO_EMAIL=",
                "# SMTP_TIMEOUT_SECONDS=30",
                "",
            ]
        )
        self.paths.env_file.write_text(template, encoding="utf-8")

    def _parse_env(self) -> dict[str, str]:
        if not self.paths.env_file.exists():
            return {}
        result: dict[str, str] = {}
        for raw_line in self.paths.env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _ENV_LINE_RE.match(raw_line)
            if not match:
                continue
            key, value = match.groups()
            cleaned = value.strip()
            if len(cleaned) >= 2 and cleaned[:1] == cleaned[-1:] and cleaned[0] in {"'", '"'}:
                cleaned = cleaned[1:-1]
            result[key] = cleaned
        return result

    def _missing_env_keys(self) -> list[str]:
        values = self._parse_env()
        return [key for key in REQUIRED_ENV_KEYS if not values.get(key, "").strip()]

    def _render_plist(self) -> None:
        if not self.paths.template_file.exists():
            raise LaunchdServiceError(f"Не найден plist template: {self.paths.template_file}")
        if not self.paths.wrapper_script.exists():
            raise LaunchdServiceError(f"Не найден launchd runner script: {self.paths.wrapper_script}")
        values = {
            "__LABEL__": html.escape(self.paths.label),
            "__WORKDIR__": html.escape(str(self.paths.bot_home)),
            "__SHELL__": "/bin/bash",
            "__PROGRAM__": html.escape(str(self.paths.wrapper_script)),
            "__STDOUT__": html.escape(str(self.paths.stdout_log)),
            "__STDERR__": html.escape(str(self.paths.stderr_log)),
        }
        content = self.paths.template_file.read_text(encoding="utf-8")
        for placeholder, value in values.items():
            content = content.replace(placeholder, value)
        self.paths.installed_plist.write_text(content, encoding="utf-8")

    def _parse_pid(self, raw: str) -> int | None:
        match = _PID_RE.search(raw)
        if not match:
            return None
        return int(match.group(1))

    def _launch_domain(self) -> str:
        return f"gui/{self.uid}"

    def _service_target(self) -> str:
        return f"{self._launch_domain()}/{self.paths.label}"

    def _require_installed(self) -> None:
        if not self.paths.installed_plist.exists():
            raise LaunchdServiceError(
                "\n".join(
                    [
                        "LaunchAgent пока не установлен.",
                        f"Ожидаемый plist: {self.paths.installed_plist}",
                        "Сначала выполни `python3 scripts/academic_engine.py service install`.",
                    ]
                )
            )

    def _require_env_configured(self) -> None:
        missing = self._missing_env_keys()
        if missing:
            raise LaunchdServiceError(
                "\n".join(
                    [
                        "Локальный env-файл для LaunchAgent не готов.",
                        f"Путь: {self.paths.env_file}",
                        f"Не хватает: {', '.join(missing)}",
                    ]
                )
            )

    def _bootout_if_loaded(self) -> None:
        if not self.paths.installed_plist.exists():
            return
        result = self.command_runner(["launchctl", "bootout", self._launch_domain(), str(self.paths.installed_plist)])
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        benign_failure = result.returncode in {0, 5, 36, 113}
        benign_failure = benign_failure or "service not loaded" in stderr.casefold()
        if not benign_failure:
            detail = stderr or stdout or f"launchctl bootout exit code {result.returncode}"
            raise LaunchdServiceError(f"Не получилось выгрузить LaunchAgent.\n{detail}")

    def _run_launchctl(self, args: list[str]) -> CompletedProcess[str]:
        result = self.command_runner(["launchctl", *args])
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or f"launchctl {' '.join(args)} exit code {result.returncode}"
            raise LaunchdServiceError(detail)
        return result

    @staticmethod
    def _default_command_runner(command: list[str]) -> CompletedProcess[str]:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
