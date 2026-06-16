"""Launch/target helpers for WorkflowOrchestrator (mixin)."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .orchestrator_support import (
    ARTICLE_ACTIONS,
    THESIS_ACTIONS,
    RunBusyError,
    WorkflowError,
    action_title,
    lane_title,
    slugify,
)
from .utils import utc_now
from .workspace import (
    WorkConfig,
    WorkspaceConfigError,
    discover_article_slugs,
    list_targets_for_action,
    resolve_target_for_action,
    resolve_target_path,
)


class OrchestratorLaunchMixin:
    """Launcher construction, target resolution and active-run descriptions."""

    root_dir: Path
    package_root: Path
    store: Any
    codex_bin: str | None
    codex_model: str | None
    python_executable: str
    project_id: str
    project_title: str

    def list_targets(self, lane: str, action: str, *, work_id: str | None = None) -> list[str]:
        lane = lane.strip().lower()
        action = action.strip().lower()
        work = self._work(work_id)
        try:
            targets = list_targets_for_action(work, lane, action, self._workspace_config())
        except WorkspaceConfigError as exc:
            raise WorkflowError(str(exc)) from exc
        return [self._display_target(target, lane, work) for target in targets]

    def list_article_slugs(self, *, work_id: str | None = None) -> list[str]:
        work = self._work(work_id)
        try:
            return discover_article_slugs(work)
        except WorkspaceConfigError as exc:
            raise WorkflowError(str(exc)) from exc

    def list_thesis_sections(self, *, work_id: str | None = None) -> list[str]:
        work = self._work(work_id)
        if not work.thesis:
            raise WorkflowError(f"Work `{work.slug}` не поддерживает thesis lane.")
        return self.list_targets("thesis", "write-section", work_id=work.slug)

    def start_run(
        self,
        lane: str,
        action: str,
        target_or_topic: str,
        notes: str | None = None,
        search_override: bool | None = None,
        model_override: str | None = None,
        profile_override: str | None = None,
        work_id: str | None = None,
    ) -> dict[str, Any]:
        work = self._work(work_id, target_or_topic if lane == "thesis" else None)
        self.sync_active_run(work_id=work.slug)
        active = self.store.get_active_run(work.slug)
        if active:
            raise RunBusyError(self.describe_active_run(active))

        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        run_token = f"{timestamp}-{slugify(self.project_id)}-{lane}-{slugify(action)}"
        record_id = f"{self.project_id}:{timestamp}-{lane}-{slugify(action)}"
        launcher_cmd, request_metadata = self._build_launch_command(
            lane=lane,
            action=action,
            target_or_topic=target_or_topic,
            notes=notes,
            search_override=search_override,
            model_override=model_override,
            profile_override=profile_override,
            work_id=work.slug,
        )
        launcher_cmd.extend(["--workflow-id", run_token])
        run_dir = self.store.runs_dir / run_token
        run_dir.mkdir(parents=True, exist_ok=True)

        request_payload = {
            "run_id": record_id,
            "run_token": run_token,
            "workflow_id": run_token,
            "run_dir": str(run_dir),
            "lane": lane,
            "action": action,
            "started_at": utc_now(),
            "project_id": self.project_id,
            "project_title": self.project_title,
            "project_root": str(self.root_dir),
            "work_id": work.slug,
            "work_title": work.title,
            "notes": notes.strip() if notes and notes.strip() else None,
            "search_override": search_override,
            "model_override": model_override,
            "profile_override": profile_override,
            "launcher_command": launcher_cmd,
            **request_metadata,
        }
        self.store.write_json(run_dir / "request.json", request_payload)

        env = os.environ.copy()
        env["PYTHONPATH"] = self._build_pythonpath(env.get("PYTHONPATH"))
        if self.codex_bin and not env.get("CODEX_BIN"):
            env["CODEX_BIN"] = self.codex_bin
        if (model_override or self.codex_model) and not env.get("CODEX_MODEL"):
            env["CODEX_MODEL"] = model_override or self.codex_model or ""

        wrapper_cmd = [
            self.python_executable,
            "-m",
            "telegram_console.run_wrapper",
            "--run-dir",
            str(run_dir),
            "--cwd",
            str(self.root_dir),
            "--",
            *launcher_cmd,
        ]

        process = subprocess.Popen(
            wrapper_cmd,
            cwd=self.root_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if process.poll() is None:
            process.returncode = 0

        active_payload = {
            "run_id": record_id,
            "workflow_id": run_token,
            "status": "queued",
            "run_dir": str(run_dir),
            "pid": process.pid,
            "lane": lane,
            "action": action,
            "started_at": request_payload["started_at"],
            "project_id": self.project_id,
            "project_title": self.project_title,
            "project_root": str(self.root_dir),
            "work_id": work.slug,
            "work_title": work.title,
            "target": request_payload.get("target"),
            "topic": request_payload.get("topic"),
        }
        self.store.set_active_run(active_payload)
        return active_payload

    def describe_active_run(self, active: dict[str, Any] | None = None) -> str:
        current = active or self.store.get_active_run()
        if not current:
            return "Сейчас активных запусков нет."
        subject = current.get("target") or current.get("topic") or "объект не указан"
        lines = ["Сейчас уже идет другой запуск ⏳"]
        project_title = current.get("project_title")
        if project_title:
            lines.append(f"📚 Проект: {project_title}")
        work_title = current.get("work_title")
        if work_title:
            lines.append(f"🗂 Работа: {work_title} (`{current.get('work_id')}`)")
        lines.append(f"{lane_title(current['lane']).capitalize()} • {action_title(current['action'])}")
        lines.append(f"Объект: {subject}")
        return "\n".join(lines)

    def _build_launch_command(
        self,
        *,
        lane: str,
        action: str,
        target_or_topic: str,
        notes: str | None,
        search_override: bool | None,
        model_override: str | None,
        profile_override: str | None,
        work_id: str,
    ) -> tuple[list[str], dict[str, Any]]:
        lane = lane.strip().lower()
        action = action.strip().lower()
        notes_clean = notes.strip() if notes and notes.strip() else None

        if lane == "thesis":
            if action not in THESIS_ACTIONS:
                raise WorkflowError(f"Для диплома пока не поддерживается действие: {action}")
            target_resolution = self._resolve_target_for_action("thesis", action, target_or_topic, work_id=work_id)
            target = target_resolution.normalized_path
            cmd = ["bash", "scripts/codex_thesis.sh", action, target, "--work", work_id]
            if notes_clean:
                cmd.extend(["--notes", notes_clean])
            if search_override is True:
                cmd.append("--search")
            elif search_override is False:
                cmd.append("--no-search")
            if model_override:
                cmd.extend(["--model", model_override])
            work = self._work(work_id)
            return cmd, {
                "target": target,
                "target_resolution": target_resolution.to_dict(),
                "work_id": work.slug,
                "work_title": work.title,
            }

        if lane == "article":
            if action not in ARTICLE_ACTIONS:
                raise WorkflowError(f"Для статьи пока не поддерживается действие: {action}")
            base = ["bash", "scripts/codex_academic.sh", action, "--work", work_id]
            metadata: dict[str, Any] = {}
            if action == "article":
                target_mode, target_value = self._resolve_article_input(target_or_topic)
                if target_mode == "brief":
                    brief_resolution = self._resolve_target_for_action(
                        "article", "article-brief", target_value, work_id=work_id
                    )
                    brief = brief_resolution.normalized_path
                    base.extend(["--brief", brief])
                    metadata["target"] = brief
                    metadata["target_resolution"] = brief_resolution.to_dict()
                    metadata["input_mode"] = "brief"
                else:
                    topic = target_value.strip()
                    if not topic:
                        raise WorkflowError("Тема статьи не может быть пустой.")
                    base.extend(["--topic", topic])
                    metadata["topic"] = topic
                    metadata["input_mode"] = "topic"
            else:
                target_resolution = self._resolve_target_for_action("article", action, target_or_topic, work_id=work_id)
                target = target_resolution.normalized_path
                base.append(target)
                metadata["target"] = target
                metadata["target_resolution"] = target_resolution.to_dict()

            if notes_clean:
                base.extend(["--notes", notes_clean])
            if search_override is True:
                base.append("--search")
            elif search_override is False:
                base.append("--no-search")
            if model_override:
                base.extend(["--model", model_override])
            if profile_override:
                base.extend(["--profile", profile_override])
            work = self._work(work_id)
            metadata["work_id"] = work.slug
            metadata["work_title"] = work.title
            return base, metadata

        raise WorkflowError(f"Не понимаю такой контур работы: {lane}")

    def _validate_target(self, lane: str, action: str, target: str, *, work_id: str | None = None) -> str:
        return self._resolve_target_for_action(lane, action, target, work_id=work_id).normalized_path

    def _resolve_target_for_action(
        self,
        lane: str,
        action: str,
        target: str,
        *,
        work_id: str | None = None,
    ) -> Any:
        work = self._work(work_id, target)
        try:
            return resolve_target_for_action(
                self._workspace_config(), work, lane, action, target, work_source="explicit"
            )
        except WorkspaceConfigError as exc:
            raise WorkflowError(str(exc)) from exc

    def _normalize_relative_path(self, raw: str, *, work_id: str | None = None) -> str:
        return self._resolve_relative_path(raw, work_id=work_id).normalized_path

    def _resolve_relative_path(self, raw: str, *, work_id: str | None = None) -> Any:
        work = self._work(work_id, raw)
        try:
            return resolve_target_path(self._workspace_config(), work, raw, work_source="explicit")
        except WorkspaceConfigError as exc:
            message = str(exc)
            if message.startswith("Не найден файл:"):
                raise WorkflowError(f"Не нашла файл: {raw}") from exc
            raise WorkflowError(message) from exc

    def _relative_to_root(self, path: Path) -> str:
        return path.resolve().relative_to(self.root_dir).as_posix()

    def _display_target(self, target: str, lane: str, work: WorkConfig) -> str:
        target_path = self.root_dir / target
        if lane == "thesis" and work.thesis:
            try:
                return target_path.resolve().relative_to(work.thesis.paths.root_dir).as_posix()
            except ValueError:
                return target
        if lane == "article" and work.article:
            try:
                rel = target_path.resolve().relative_to(work.article.paths.root_dir).as_posix()
            except ValueError:
                return target
            return f"articles/{rel}"
        return target

    def _build_pythonpath(self, current: str | None) -> str:
        paths = [str(self.package_root), str(self.root_dir)]
        if current:
            paths.append(current)
        return os.pathsep.join(paths)
