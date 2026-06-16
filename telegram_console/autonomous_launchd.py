from __future__ import annotations

import html
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess

DEFAULT_AUTONOMOUS_DAEMON_LABEL = "com.albina.academic-autonomous-daemon"
CommandRunner = Callable[[list[str]], CompletedProcess[str]]


class AutonomousDaemonLaunchdError(RuntimeError):
    """Raised when the autonomous daemon LaunchAgent cannot continue."""


@dataclass(frozen=True)
class AutonomousDaemonLaunchdPaths:
    workspace_root: Path
    label: str
    runtime_dir: Path
    stdout_log: Path
    stderr_log: Path
    launch_agents_dir: Path
    installed_plist: Path


@dataclass(frozen=True)
class AutonomousDaemonLaunchdStatus:
    label: str
    installed: bool
    loaded: bool
    pid: int | None
    works_scope: str
    agent_path: Path
    stdout_log: Path
    stderr_log: Path
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "autonomous-daemon-launchd-status",
            "status": self.runtime_status,
            "label": self.label,
            "installed": self.installed,
            "loaded": self.loaded,
            "pid": self.pid,
            "works_scope": self.works_scope,
            "agent_path": str(self.agent_path),
            "stdout_log": str(self.stdout_log),
            "stderr_log": str(self.stderr_log),
            "note": self.note,
            "readiness_claim": "none",
        }

    @property
    def runtime_status(self) -> str:
        if self.loaded:
            return "loaded"
        if self.installed:
            return "installed"
        return "not-installed"


@dataclass(frozen=True)
class AutonomousDaemonLaunchdResult:
    installed: bool
    status: AutonomousDaemonLaunchdStatus
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "autonomous-daemon-launchd-result",
            "installed": self.installed,
            "note": self.note,
            "status": self.status.to_dict(),
            "readiness_claim": "none",
        }


class AutonomousDaemonLaunchdManager:
    def __init__(
        self,
        workspace_root: str | Path,
        *,
        label: str = DEFAULT_AUTONOMOUS_DAEMON_LABEL,
        home_dir: str | Path | None = None,
        uid: int | None = None,
        command_runner: CommandRunner | None = None,
        python_executable: str | None = None,
    ):
        root = Path(workspace_root).expanduser().resolve()
        user_home = Path(home_dir or Path.home()).expanduser().resolve()
        launch_agents_dir = user_home / "Library" / "LaunchAgents"
        runtime_dir = root / "output" / "telegram" / "runtime" / "autonomous"
        self.paths = AutonomousDaemonLaunchdPaths(
            workspace_root=root,
            label=label,
            runtime_dir=runtime_dir,
            stdout_log=runtime_dir / "multi-work.launchd.stdout.log",
            stderr_log=runtime_dir / "multi-work.launchd.stderr.log",
            launch_agents_dir=launch_agents_dir,
            installed_plist=launch_agents_dir / f"{label}.plist",
        )
        self.uid = uid if uid is not None else os.getuid()
        self.command_runner = command_runner or self._default_command_runner
        self.python_executable = python_executable or sys.executable
        self._last_works_scope = "all"

    def install(
        self,
        *,
        works_scope: str = "all",
        mode: str = "autonomous-full",
        poll_seconds: int = 30,
        max_cycles: int = 50,
        max_runtime_minutes: int = 240,
    ) -> AutonomousDaemonLaunchdResult:
        self._last_works_scope = works_scope
        self._ensure_directories()
        self._render_plist(
            works_scope=works_scope,
            mode=mode,
            poll_seconds=poll_seconds,
            max_cycles=max_cycles,
            max_runtime_minutes=max_runtime_minutes,
        )
        self._bootout_if_loaded()
        self._run_launchctl(["bootstrap", self._launch_domain(), str(self.paths.installed_plist)])
        self._run_launchctl(["kickstart", "-k", self._service_target()])
        status = self.status(works_scope=works_scope, note="Autonomous daemon LaunchAgent installed and started.")
        return AutonomousDaemonLaunchdResult(
            installed=True,
            status=status,
            note="Autonomous daemon LaunchAgent installed and started.",
        )

    def start(self, *, works_scope: str = "all") -> AutonomousDaemonLaunchdStatus:
        self._last_works_scope = works_scope
        self._require_installed()
        if not self.status(works_scope=works_scope).loaded:
            self._run_launchctl(["bootstrap", self._launch_domain(), str(self.paths.installed_plist)])
        self._run_launchctl(["kickstart", self._service_target()])
        return self.status(works_scope=works_scope, note="Autonomous daemon LaunchAgent started.")

    def stop(self, *, works_scope: str = "all") -> AutonomousDaemonLaunchdStatus:
        self._last_works_scope = works_scope
        if self.paths.installed_plist.exists():
            self._bootout_if_loaded()
        return self.status(works_scope=works_scope, note="Autonomous daemon LaunchAgent stopped.")

    def restart(self, *, works_scope: str = "all") -> AutonomousDaemonLaunchdStatus:
        self._last_works_scope = works_scope
        self._require_installed()
        current = self.status(works_scope=works_scope)
        if current.loaded:
            self._run_launchctl(["kickstart", "-k", self._service_target()])
        else:
            self._run_launchctl(["bootstrap", self._launch_domain(), str(self.paths.installed_plist)])
            self._run_launchctl(["kickstart", self._service_target()])
        return self.status(works_scope=works_scope, note="Autonomous daemon LaunchAgent restarted.")

    def uninstall(self, *, works_scope: str = "all") -> AutonomousDaemonLaunchdStatus:
        self._last_works_scope = works_scope
        if self.paths.installed_plist.exists():
            self._bootout_if_loaded()
            self.paths.installed_plist.unlink()
        return self.status(works_scope=works_scope, note="Autonomous daemon LaunchAgent uninstalled.")

    def status(self, *, works_scope: str | None = None, note: str | None = None) -> AutonomousDaemonLaunchdStatus:
        scope = works_scope or self._last_works_scope
        installed = self.paths.installed_plist.exists()
        loaded = False
        pid = None
        if installed:
            result = self.command_runner(["launchctl", "print", self._service_target()])
            if result.returncode == 0:
                loaded = True
                pid = self._parse_pid(result.stdout or "")
        return AutonomousDaemonLaunchdStatus(
            label=self.paths.label,
            installed=installed,
            loaded=loaded,
            pid=pid,
            works_scope=scope,
            agent_path=self.paths.installed_plist,
            stdout_log=self.paths.stdout_log,
            stderr_log=self.paths.stderr_log,
            note=note,
        )

    def format_status(self, status: AutonomousDaemonLaunchdStatus) -> str:
        lines = [
            "Autonomous daemon LaunchAgent",
            f"Label: {status.label}",
            f"Works: {status.works_scope}",
            f"Installed: {'yes' if status.installed else 'no'}",
            f"Loaded: {'yes' if status.loaded else 'no'}",
            f"Plist: {status.agent_path}",
            f"Stdout: {status.stdout_log}",
            f"Stderr: {status.stderr_log}",
        ]
        if status.pid is not None:
            lines.append(f"PID: {status.pid}")
        if status.note:
            lines.extend(["", status.note])
        return "\n".join(lines)

    def format_result(self, result: AutonomousDaemonLaunchdResult) -> str:
        return "\n".join(["Autonomous daemon LaunchAgent installed", "", self.format_status(result.status)])

    def _ensure_directories(self) -> None:
        self.paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.paths.launch_agents_dir.mkdir(parents=True, exist_ok=True)

    def _render_plist(
        self,
        *,
        works_scope: str,
        mode: str,
        poll_seconds: int,
        max_cycles: int,
        max_runtime_minutes: int,
    ) -> None:
        args = [
            self.python_executable,
            "-m",
            "telegram_console.work_cli",
            "autonomous",
            "daemon",
            "run",
            "--works",
            works_scope,
            "--mode",
            mode,
            "--poll-seconds",
            str(poll_seconds),
            "--max-cycles",
            str(max_cycles),
            "--max-runtime-minutes",
            str(max_runtime_minutes),
            "--json",
        ]
        array = "\n".join(f"    <string>{html.escape(item)}</string>" for item in args)
        content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{html.escape(self.paths.label)}</string>
  <key>WorkingDirectory</key>
  <string>{html.escape(str(self.paths.workspace_root))}</string>
  <key>ProgramArguments</key>
  <array>
{array}
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>{max(10, poll_seconds)}</integer>
  <key>StandardOutPath</key>
  <string>{html.escape(str(self.paths.stdout_log))}</string>
  <key>StandardErrorPath</key>
  <string>{html.escape(str(self.paths.stderr_log))}</string>
</dict>
</plist>
"""
        self.paths.installed_plist.write_text(content, encoding="utf-8")

    def _require_installed(self) -> None:
        if not self.paths.installed_plist.exists():
            raise AutonomousDaemonLaunchdError(
                "\n".join(
                    [
                        "Autonomous daemon LaunchAgent is not installed.",
                        f"Expected plist: {self.paths.installed_plist}",
                        "Run `python3 -m telegram_console.work_cli autonomous daemon launchd install "
                        "--works all` first.",
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
            raise AutonomousDaemonLaunchdError(f"Could not unload autonomous daemon LaunchAgent.\n{detail}")

    def _run_launchctl(self, args: list[str]) -> CompletedProcess[str]:
        result = self.command_runner(["launchctl", *args])
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or f"launchctl {' '.join(args)} exit code {result.returncode}"
            raise AutonomousDaemonLaunchdError(detail)
        return result

    def _launch_domain(self) -> str:
        return f"gui/{self.uid}"

    def _service_target(self) -> str:
        return f"{self._launch_domain()}/{self.paths.label}"

    @staticmethod
    def _parse_pid(raw: str) -> int | None:
        for token in raw.replace("=", " ").split():
            if token.isdigit():
                return int(token)
        return None

    @staticmethod
    def _default_command_runner(command: list[str]) -> CompletedProcess[str]:
        return subprocess.run(command, text=True, capture_output=True, check=False)
