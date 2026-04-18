from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os
import re
import subprocess
import sys

from .action_specs import execution_contract_from_payload
from .article_bundle_state import article_bundle_manifest_path, build_article_bundle_state, load_article_bundle_state
from .article_runtime_signals import extract_article_artifact_signals
from .contract_gates import evaluate_contract_gates
from .repair_kernel import Blocker, build_repair_decision, determine_terminal_reason
from .thesis_runtime_signals import extract_thesis_runtime_signals
from .thesis_repair_planner import build_thesis_repair_plan
from .runtime_status import (
    RuntimeRecord,
    build_attachments,
    build_checkpoint,
    build_failure,
    build_runtime_status,
    load_runtime_record,
    write_status,
)
from .standards import resolve_standard_profile
from .state import RuntimeStore
from .utils import parse_datetime, utc_now
from .work_state import build_work_state
from .workspace import (
    WorkConfig,
    WorkspaceConfigError,
    article_bundle_paths,
    derive_review_path,
    list_targets_for_action,
    load_work_config,
    load_workspace_config,
    relative_to_workspace,
    resolve_target_for_action,
    resolve_target_path,
    resolve_work_config,
)


THESIS_ACTIONS = (
    "full-cycle",
    "source-pack",
    "verify",
    "write-section",
    "review-section",
    "style-pass",
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


class WorkflowOrchestrator:
    def __init__(
        self,
        root_dir: str | Path,
        *,
        codex_bin: str | None = None,
        codex_model: str | None = None,
        python_executable: str | None = None,
        store: RuntimeStore | None = None,
        project_id: str | None = None,
        project_title: str | None = None,
    ):
        self.root_dir = Path(root_dir).resolve()
        self.package_root = Path(__file__).resolve().parents[1]
        self.store = store or RuntimeStore(self.root_dir)
        self.codex_bin = codex_bin
        self.codex_model = codex_model
        self.python_executable = python_executable or sys.executable
        self.project_id = (project_id or "default").strip() or "default"
        self.project_title = (project_title or self.root_dir.name or self.project_id).strip()
        self._workspace = None

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
        if not work.article:
            raise WorkflowError(f"Work `{work.slug}` не поддерживает article lane.")
        slugs: set[str] = set()
        folders = (
            work.article.briefs_dir,
            work.article.evidence_dir,
            work.article.claim_maps_dir,
            work.article.drafts_dir,
            work.article.reviews_dir,
            work.article.final_dir,
            work.article.paths.output_docx_dir,
            work.article.paths.root_dir / "runs",
        )
        for folder in folders:
            if not folder.exists():
                continue
            for path in folder.glob("*"):
                if path.name.startswith(".") or path.name == "README.md":
                    continue
                if path.suffix == ".json" and path.name.endswith(".bundle.json"):
                    slugs.add(path.name[: -len(".bundle.json")])
                    continue
                if path.suffix == ".docx":
                    slugs.add(path.stem)
                    continue
                if path.suffix != ".md":
                    continue
                stem = path.stem
                if stem.endswith("-checklist"):
                    stem = stem[: -len("-checklist")]
                slugs.add(stem)
        return sorted(slugs)

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
        work_id: str | None = None,
    ) -> dict[str, Any]:
        self.sync_active_run()
        active = self.store.get_active_run()
        if active:
            raise RunBusyError(self.describe_active_run(active))

        work = self._work(work_id, target_or_topic if lane == "thesis" else None)

        launcher_cmd, request_metadata = self._build_launch_command(
            lane=lane,
            action=action,
            target_or_topic=target_or_topic,
            notes=notes,
            search_override=search_override,
            model_override=model_override,
            work_id=work.slug,
        )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_token = f"{timestamp}-{slugify(self.project_id)}-{lane}-{slugify(action)}"
        record_id = f"{self.project_id}:{timestamp}-{lane}-{slugify(action)}"
        run_dir = self.store.runs_dir / run_token
        run_dir.mkdir(parents=True, exist_ok=True)

        request_payload = {
            "run_id": record_id,
            "run_token": run_token,
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

    def sync_active_run(self) -> list[RunRecord]:
        active = self.store.get_active_run()
        if not active or not self._active_run_matches(active):
            return []

        run_dir = Path(active["run_dir"])
        request = self.store.read_json(run_dir / "request.json", default={}) or {}
        result = self.store.read_json(run_dir / "result.json")
        if result is None and self._pid_is_alive(int(active.get("pid", 0))):
            return []

        if result is None:
            result = {
                "started_at": request.get("started_at"),
                "finished_at": utc_now(),
                "returncode": None,
                "status": "interrupted",
                "log_path": str(run_dir / "launcher.log"),
                "error": "Process exited without result.json",
            }
            self.store.write_json(run_dir / "result.json", result)

        record = self._finalize_runtime_run(run_dir, request, result)
        self.store.clear_active_run()
        self.store.append_notification(record.to_dict())
        return [record]

    def drain_notifications(self) -> list[RunRecord]:
        items = self.store.pop_notifications()
        return [RunRecord(**item) for item in items]

    def list_recent_runs(self, lane: str = "all", limit: int = 8, *, work_id: str | None = None) -> list[RunRecord]:
        self.sync_active_run()
        records: list[RunRecord] = []
        lane = lane.lower()
        work = self._work(work_id)

        include_thesis = lane in ("all", "thesis")
        include_article = lane in ("all", "article")

        if include_thesis:
            records.extend(self._load_manifest_records("thesis", work.slug))
        if include_article:
            records.extend(self._load_manifest_records("article", work.slug))

        records.extend(self._load_runtime_exception_records(lane, work.slug))

        active = self.store.get_active_run()
        if active and self._active_run_matches(active) and lane in ("all", active["lane"]):
            if str(active.get("work_id") or "").strip() == work.slug:
                records.insert(0, self._active_run_record(active))

        deduped: list[RunRecord] = []
        seen_manifests: set[str] = set()
        for record in sorted(records, key=lambda item: item.sort_key, reverse=True):
            if record.manifest_path:
                if record.manifest_path in seen_manifests:
                    continue
                seen_manifests.add(record.manifest_path)
            deduped.append(record)
            if len(deduped) >= limit:
                break
        return deduped

    def find_run_record(self, record_id: str) -> RunRecord | None:
        for record in self.list_recent_runs("all", limit=200):
            if record.record_id == record_id:
                return record
        return None

    def get_run_attachment(self, record_id: str, attachment: str) -> Path | None:
        record = self.find_run_record(record_id)
        if not record:
            return None

        mapping = {
            "trace": record.output_file,
            "manifest": record.manifest_path,
            "log": record.log_path,
        }
        path = mapping.get(attachment)
        if not path:
            return None
        candidate = Path(path)
        return candidate if candidate.exists() else None

    def get_artifact_status(self, subject: str, *, work_id: str | None = None) -> dict[str, Any]:
        work = self._work(work_id)
        if subject == "work":
            return self.get_work_state(work_id=work.slug)

        if subject == "thesis":
            sections = [self._thesis_section_status(path, work.slug) for path in self.list_thesis_sections(work_id=work.slug)]
            return {
                "kind": "thesis-overview",
                "work_id": work.slug,
                "sections": sections,
                "summary": self._build_thesis_overview_summary(sections),
            }

        if subject.startswith("thesis:"):
            return self._thesis_section_status(subject.split(":", 1)[1], work.slug)

        if subject == "article":
            bundles = [self._article_bundle_status(slug, work.slug) for slug in self.list_article_slugs(work_id=work.slug)]
            return {
                "kind": "article-overview",
                "work_id": work.slug,
                "bundles": bundles,
                "summary": self._build_article_overview_summary(bundles),
            }

        if subject.startswith("article:"):
            return self._article_bundle_status(subject.split(":", 1)[1], work.slug)

        raise WorkflowError(f"Не смогла определить, какой артефакт ты хочешь открыть: {subject}")

    def get_work_state(self, *, work_id: str | None = None) -> dict[str, Any]:
        work = self._work(work_id)
        thesis_overview: dict[str, Any] | None = None
        article_overview: dict[str, Any] | None = None

        if work.supports("thesis") and work.thesis:
            sections = [self._thesis_section_status(path, work.slug) for path in self.list_thesis_sections(work_id=work.slug)]
            thesis_overview = {
                "kind": "thesis-overview",
                "work_id": work.slug,
                "sections": sections,
                "summary": self._build_thesis_overview_summary(sections),
            }

        if work.supports("article") and work.article:
            bundles = [self._article_bundle_status(slug, work.slug) for slug in self.list_article_slugs(work_id=work.slug)]
            article_overview = {
                "kind": "article-overview",
                "work_id": work.slug,
                "bundles": bundles,
                "summary": self._build_article_overview_summary(bundles),
            }

        return build_work_state(
            root_dir=self.root_dir,
            work_id=work.slug,
            work_title=work.title,
            active_lanes=work.active_lanes,
            thesis_overview=thesis_overview,
            article_overview=article_overview,
            standards_profiles=self._resolve_work_standards_profiles(work),
            runtime_records=self._recent_workflow_runtime_records(work.slug, limit=5),
            active_run=self._active_workflow_run_for_work(work.slug),
        )

    def export_docx(self, subject: str, *, work_id: str | None = None) -> dict[str, Any]:
        work = self._work(work_id)
        if subject == "thesis":
            if not work.thesis:
                raise WorkflowError(f"Work `{work.slug}` не поддерживает thesis lane.")
            cmd = ["bash", "scripts/export_docx.sh", "--work", work.slug]
            expected = work.thesis.export_docx_path
        elif subject.startswith("article:"):
            slug = subject.split(":", 1)[1]
            status = self._article_bundle_status(slug, work.slug)
            final_markdown = status["files"]["final"]["path"]
            if not Path(final_markdown).exists():
                raise WorkflowError(f"У статьи `{slug}` пока нет финального Markdown-файла для экспорта.")
            cmd = [
                "bash",
                "scripts/export_academic_docx.sh",
                self._relative_to_root(Path(final_markdown)),
                "--work",
                work.slug,
            ]
            expected = Path(status["files"]["docx"]["path"])
        else:
            raise WorkflowError(f"Не понимаю, что именно нужно экспортировать: {subject}")

        completed = subprocess.run(
            cmd,
            cwd=self.root_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise WorkflowError(completed.stderr.strip() or completed.stdout.strip() or "Экспорт не получился.")

        output_path = expected
        for line in (completed.stdout or "").splitlines():
            if line.startswith("Exported "):
                output_path = Path(line[len("Exported ") :].strip())
                break

        return {
            "subject": subject,
            "path": str(output_path.resolve()),
            "stdout": completed.stdout.strip(),
        }

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
                    brief_resolution = self._resolve_target_for_action("article", "article-brief", target_value, work_id=work_id)
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
            work = self._work(work_id)
            metadata["work_id"] = work.slug
            metadata["work_title"] = work.title
            return base, metadata

        raise WorkflowError(f"Не понимаю такой контур работы: {lane}")

    def _resolve_article_input(self, target_or_topic: str) -> tuple[str, str]:
        raw = target_or_topic.strip()
        if not raw:
            raise WorkflowError("Нужна тема статьи или путь к брифу.")
        if raw.startswith("brief:"):
            return ("brief", raw.split(":", 1)[1].strip())
        if raw.startswith("бриф:"):
            return ("brief", raw.split(":", 1)[1].strip())
        if raw.startswith("topic:"):
            return ("topic", raw.split(":", 1)[1].strip())
        if raw.startswith("тема:"):
            return ("topic", raw.split(":", 1)[1].strip())
        if raw.endswith(".md"):
            return ("brief", raw)
        return ("topic", raw)

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
            return resolve_target_for_action(self._workspace_config(), work, lane, action, target, work_source="explicit")
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

    def _pid_is_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _finalize_runtime_run(
        self,
        run_dir: Path,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> RunRecord:
        manifest_path, output_file = self._resolve_manifest_outputs(request, result)
        status = result.get("status", "failed")
        if status == "success" and not manifest_path:
            status = "success"
        elif status == "failed" and manifest_path:
            status = "failed"
        elif status == "interrupted":
            status = "interrupted"

        record = RunRecord(
            record_id=request["run_id"],
            lane=request["lane"],
            action=request["action"],
            status=status,
            started_at=request.get("started_at", result.get("started_at", utc_now())),
            project_id=request.get("project_id", self.project_id),
            project_title=request.get("project_title", self.project_title),
            project_root=request.get("project_root", str(self.root_dir)),
            work_id=request.get("work_id"),
            work_title=request.get("work_title"),
            finished_at=result.get("finished_at"),
            target=request.get("target"),
            topic=request.get("topic"),
            manifest_path=manifest_path,
            output_file=output_file,
            log_path=result.get("log_path") or str(run_dir / "launcher.log"),
            runtime_run_dir=str(run_dir),
            source="runtime",
            summary=self._build_record_summary(
                request["lane"],
                request["action"],
                status,
                request.get("target"),
                request.get("topic"),
            ),
        )
        article_runtime = self._sync_article_runtime_state(request, record)
        thesis_runtime = self._sync_thesis_runtime_state(request, record)
        resolution_payload = record.to_dict()
        target_resolution = request.get("target_resolution")
        if isinstance(target_resolution, dict):
            resolution_payload["target_resolution"] = target_resolution
        if article_runtime:
            resolution_payload["article_runtime"] = article_runtime
        if thesis_runtime:
            resolution_payload["thesis_runtime"] = thesis_runtime
        self.store.write_json(run_dir / "resolution.json", resolution_payload)
        self._write_workflow_status(
            run_dir,
            request,
            result,
            record,
            article_runtime=article_runtime,
            thesis_runtime=thesis_runtime,
        )
        return record

    def _resolve_manifest_outputs(
        self,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        if result.get("returncode") != 0:
            return (None, None)

        candidate_records = self._load_manifest_records(request["lane"], str(request.get("work_id") or "").strip())
        started = parse_datetime(request.get("started_at"))
        chosen: RunRecord | None = None
        for record in candidate_records:
            if record.action != request["action"]:
                continue
            if record.work_id != request.get("work_id"):
                continue
            if request["lane"] == "thesis" and record.target != request.get("target"):
                continue
            if request["lane"] == "article":
                if request.get("target") and record.target != request.get("target"):
                    continue
                if request.get("topic") and record.topic != request.get("topic"):
                    continue
            if parse_datetime(record.started_at) < started:
                continue
            chosen = record
            break

        if not chosen and candidate_records:
            chosen = candidate_records[0]

        if not chosen:
            return (None, None)
        return (chosen.manifest_path, chosen.output_file)

    def _load_manifest_records(self, lane: str, work_id: str | None) -> list[RunRecord]:
        records: list[RunRecord] = []
        if lane not in ("thesis", "article") or not work_id:
            return records

        try:
            work = self._work(work_id)
        except WorkflowError:
            return records
        directory = work.thesis.paths.output_runs_dir if lane == "thesis" and work.thesis else None
        if lane == "article" and work.article:
            directory = work.article.paths.output_runs_dir
        if directory is None:
            return records

        if not directory.exists():
            return records

        for manifest in sorted(directory.glob("*.meta.json"), reverse=True):
            data = self.store.read_json(manifest)
            if not isinstance(data, dict):
                continue
            if lane == "thesis":
                action = data.get("preset", "unknown")
                target = (data.get("target") or {}).get("relative")
                topic = None
                started_at = data.get("timestamp", manifest.stem)
            else:
                action = data.get("command", "unknown")
                target = data.get("target_path") or data.get("input_brief")
                topic = data.get("topic")
                started_at = data.get("timestamp", manifest.stem)
            records.append(
                RunRecord(
                    record_id=f"{self.project_id}:{manifest.stem.replace('.meta', '')}",
                    lane=lane,
                    action=action,
                    status="success",
                    started_at=started_at,
                    project_id=self.project_id,
                    project_title=self.project_title,
                    project_root=str(self.root_dir),
                    work_id=data.get("work_id", work.slug),
                    work_title=data.get("work_title", work.title),
                    target=target,
                    topic=topic,
                    manifest_path=str(manifest.resolve()),
                    output_file=data.get("output_file"),
                    source="manifest",
                    summary=self._build_record_summary(lane, action, "success", target, topic),
                )
            )
        return records

    def _load_runtime_exception_records(self, lane: str, work_id: str) -> list[RunRecord]:
        records: list[RunRecord] = []
        for run_dir in self.store.list_run_dirs():
            request = self.store.read_json(run_dir / "request.json")
            if not isinstance(request, dict) or not self._request_matches_project(request):
                continue
            run_lane = request.get("lane")
            if lane not in ("all", run_lane):
                continue
            if str(request.get("work_id") or "").strip() != work_id:
                continue
            resolution = self.store.read_json(run_dir / "resolution.json")
            result = self.store.read_json(run_dir / "result.json")
            if isinstance(resolution, dict):
                record = RunRecord(
                    **{
                        key: value
                        for key, value in resolution.items()
                        if key in RunRecord.__dataclass_fields__
                    }
                )
                if record.status == "success" and record.manifest_path:
                    continue
                records.append(record)
                continue
            if not isinstance(result, dict) or result.get("status") == "success":
                continue
            records.append(
                RunRecord(
                    record_id=request["run_id"],
                    lane=request["lane"],
                    action=request["action"],
                    status=result.get("status", "failed"),
                    started_at=request.get("started_at", result.get("started_at", utc_now())),
                    project_id=request.get("project_id", self.project_id),
                    project_title=request.get("project_title", self.project_title),
                    project_root=request.get("project_root", str(self.root_dir)),
                    work_id=request.get("work_id"),
                    work_title=request.get("work_title"),
                    finished_at=result.get("finished_at"),
                    target=request.get("target"),
                    topic=request.get("topic"),
                    log_path=result.get("log_path") or str(run_dir / "launcher.log"),
                    runtime_run_dir=str(run_dir),
                    source="runtime",
                    summary=self._build_record_summary(
                        request["lane"],
                        request["action"],
                        result.get("status", "failed"),
                        request.get("target"),
                        request.get("topic"),
                    ),
                )
            )
        return records

    def _active_run_record(self, active: dict[str, Any]) -> RunRecord:
        return RunRecord(
            record_id=active["run_id"],
            lane=active["lane"],
            action=active["action"],
            status="running",
            started_at=active["started_at"],
            project_id=active.get("project_id", self.project_id),
            project_title=active.get("project_title", self.project_title),
            project_root=active.get("project_root", str(self.root_dir)),
            work_id=active.get("work_id"),
            work_title=active.get("work_title"),
            target=active.get("target"),
            topic=active.get("topic"),
            log_path=str(Path(active["run_dir"]) / "launcher.log"),
            runtime_run_dir=active["run_dir"],
            source="runtime",
            summary=self._build_record_summary(
                active["lane"],
                active["action"],
                "running",
                active.get("target"),
                active.get("topic"),
            ),
        )

    def _thesis_section_status(self, target: str, work_id: str) -> dict[str, Any]:
        work = self._work(work_id, target)
        section = self._validate_target("thesis", "write-section", target, work_id=work.slug)
        section_path = self.root_dir / section
        review_path = derive_review_path(self._workspace_config(), work, section)
        recent = [
            record.to_dict()
            for record in self.list_recent_runs("thesis", limit=20, work_id=work.slug)
            if record.target == section
        ][:3]
        latest_runtime = self._latest_workflow_runtime_record("thesis", work.slug, target=section)
        review_exists = review_path.exists() if review_path else False
        return {
            "kind": "thesis-section",
            "work_id": work.slug,
            "target": section,
            "review_path": str(review_path) if review_path else None,
            "review_exists": review_exists,
            "available_actions": list(THESIS_ACTIONS),
            "recent_runs": recent,
            "summary": self._build_thesis_section_summary(section, review_exists, recent, latest_runtime),
        }

    def _article_bundle_status(self, slug: str, work_id: str) -> dict[str, Any]:
        clean_slug = slug.strip()
        if not clean_slug:
            raise WorkflowError("Идентификатор статьи не может быть пустым.")
        work = self._work(work_id)
        try:
            files = article_bundle_paths(work, clean_slug)
        except WorkspaceConfigError as exc:
            raise WorkflowError(str(exc)) from exc
        state_path = article_bundle_manifest_path(work, clean_slug)
        state = load_article_bundle_state(state_path)
        if state is None:
            state = build_article_bundle_state(
                work_id=work.slug,
                article_slug=clean_slug,
                bundle=files,
            )
        present, missing = self._article_present_files(files)
        recent = [
            record.to_dict()
            for record in self.list_recent_runs("article", limit=20, work_id=work.slug)
            if (record.target and clean_slug in record.target)
            or (record.output_file and clean_slug in record.output_file)
        ][:3]
        return {
            "kind": "article-bundle",
            "work_id": work.slug,
            "slug": clean_slug,
            "bundle_state_manifest": str(state_path),
            "bundle_state_manifest_exists": state_path.exists(),
            "state": state.to_dict(),
            "files": present,
            "missing": missing,
            "complete": not missing,
            "recent_runs": recent,
            "summary": self._build_article_bundle_summary(clean_slug, state, present),
        }

    def _build_thesis_section_summary(
        self,
        target: str,
        review_present: bool,
        recent_runs: list[dict[str, Any]],
        runtime_record: RuntimeRecord | None,
    ) -> dict[str, Any]:
        last_run = recent_runs[0] if recent_runs else {}
        blocker_count = len(runtime_record.blockers) if runtime_record else 0
        terminal_reason = runtime_record.terminal_reason if runtime_record else None
        return self._compose_thesis_section_summary(
            target=target,
            review_present=review_present,
            last_run_action=_optional_text(last_run.get("action")),
            last_run_status=_optional_text(last_run.get("status")),
            blocker_count=blocker_count,
            terminal_reason=terminal_reason,
        )

    def _compose_thesis_section_summary(
        self,
        *,
        target: str,
        review_present: bool,
        last_run_action: str | None,
        last_run_status: str | None,
        blocker_count: int,
        terminal_reason: str | None,
    ) -> dict[str, Any]:
        if last_run_action is None and last_run_status is None:
            suggested_next_action = "write-section"
        elif blocker_count:
            suggested_next_action = "review-section" if last_run_action == "review-section" else "verify"
        elif not review_present:
            suggested_next_action = "review-section"
        else:
            suggested_next_action = "style-pass"
        return {
            "kind": "thesis-section-summary",
            "target": target,
            "review_present": review_present,
            "last_run_action": last_run_action,
            "last_run_status": last_run_status,
            "blocker_count": blocker_count,
            "terminal_reason": terminal_reason,
            "suggested_next_action": suggested_next_action,
        }

    def _build_thesis_overview_summary(self, sections: list[dict[str, Any]]) -> dict[str, Any]:
        review_count = sum(1 for item in sections if item.get("review_exists"))
        blocked_count = sum(
            1
            for item in sections
            if isinstance(item.get("summary"), dict) and int(item["summary"].get("blocker_count") or 0) > 0
        )
        if not sections:
            suggested_next_action = "write-section"
        elif blocked_count:
            suggested_next_action = "verify"
        elif review_count < len(sections):
            suggested_next_action = "review-section"
        else:
            suggested_next_action = "style-pass"
        return {
            "kind": "thesis-overview-summary",
            "section_count": len(sections),
            "reviewed_count": review_count,
            "blocked_count": blocked_count,
            "suggested_next_action": suggested_next_action,
        }

    def _build_article_bundle_summary(
        self,
        slug: str,
        state: Any,
        files: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        review_present = bool((files.get("review") or {}).get("exists"))
        checklist_present = bool((files.get("checklist") or {}).get("exists"))
        blocker_count = int(getattr(state, "blocker_count", 0) or 0)
        repair_decision = getattr(state, "repair_decision", None)
        repair_action = repair_decision.get("action") if isinstance(repair_decision, dict) else None
        if blocker_count:
            suggested_next_action = "repair"
        elif not review_present and bool((files.get("draft") or {}).get("exists")):
            suggested_next_action = "review"
        elif not bool((files.get("final") or {}).get("exists")):
            suggested_next_action = "article"
        elif not checklist_present:
            suggested_next_action = "repair"
        elif getattr(state, "current_status", None) == "strong-draft":
            suggested_next_action = "review"
        else:
            suggested_next_action = None
        return {
            "kind": "article-bundle-summary",
            "slug": slug,
            "current_phase": getattr(state, "current_phase", None),
            "current_status": getattr(state, "current_status", None),
            "readiness_status": getattr(state, "readiness_status", None),
            "blocker_count": blocker_count,
            "repair_action": repair_action,
            "repair_iteration": getattr(state, "repair_iteration", None),
            "review_present": review_present,
            "checklist_present": checklist_present,
            "suggested_next_action": suggested_next_action,
        }

    def _build_article_overview_summary(self, bundles: list[dict[str, Any]]) -> dict[str, Any]:
        blocked_count = sum(
            1
            for item in bundles
            if isinstance(item.get("summary"), dict) and int(item["summary"].get("blocker_count") or 0) > 0
        )
        ready_count = sum(
            1
            for item in bundles
            if isinstance(item.get("summary"), dict) and item["summary"].get("current_status") == "submission-ready"
        )
        review_missing_count = sum(
            1
            for item in bundles
            if isinstance(item.get("summary"), dict) and not bool(item["summary"].get("review_present"))
        )
        if not bundles:
            suggested_next_action = "article"
        elif blocked_count:
            suggested_next_action = "repair"
        elif review_missing_count:
            suggested_next_action = "review"
        else:
            suggested_next_action = "article"
        return {
            "kind": "article-overview-summary",
            "bundle_count": len(bundles),
            "blocked_count": blocked_count,
            "submission_ready_count": ready_count,
            "review_missing_count": review_missing_count,
            "suggested_next_action": suggested_next_action,
        }

    def _article_present_files(self, files: dict[str, Path]) -> tuple[dict[str, dict[str, Any]], list[str]]:
        exposed_files = {
            "brief": files["brief"],
            "evidence": files["evidence_pack"],
            "claim_map": files["claim_map"],
            "draft": files["draft"],
            "review": files["review"],
            "final": files["final_markdown"],
            "checklist": files["checklist"],
            "docx": files["docx"],
        }
        present = {name: {"path": str(path), "exists": path.exists()} for name, path in exposed_files.items()}
        missing = [name for name, info in present.items() if not info["exists"]]
        return present, missing

    def _latest_workflow_runtime_record(
        self,
        lane: str,
        work_id: str,
        *,
        target: str | None = None,
    ) -> RuntimeRecord | None:
        records: list[RuntimeRecord] = []
        for run_dir in self.store.list_run_dirs():
            record = load_runtime_record(run_dir, "workflow-run")
            if record is None or record.lane != lane:
                continue
            if str(record.work_id or "").strip() != work_id:
                continue
            if not self._runtime_record_matches_project(record):
                continue
            if target is not None:
                request = self.store.read_json(run_dir / "request.json", default={}) or {}
                request_target = str(request.get("target") or "").strip()
                if not request_target:
                    resolution = self.store.read_json(run_dir / "resolution.json", default={}) or {}
                    request_target = str(resolution.get("target") or "").strip()
                if request_target != target:
                    continue
            records.append(record)
        if not records:
            return None
        return sorted(records, key=lambda item: item.sort_key, reverse=True)[0]

    def _recent_workflow_runtime_records(self, work_id: str, *, limit: int = 5) -> list[RuntimeRecord]:
        records: list[RuntimeRecord] = []
        for run_dir in self.store.list_run_dirs():
            record = load_runtime_record(run_dir, "workflow-run")
            if record is None:
                continue
            if str(record.work_id or "").strip() != work_id:
                continue
            if not self._runtime_record_matches_project(record):
                continue
            records.append(record)
        return sorted(records, key=lambda item: item.sort_key, reverse=True)[:limit]

    def _active_workflow_run_for_work(self, work_id: str) -> dict[str, Any] | None:
        active = self.store.get_active_run()
        if not isinstance(active, dict) or not self._active_run_matches(active):
            return None
        if str(active.get("work_id") or "").strip() != work_id:
            return None
        return {
            "run_id": active.get("run_id"),
            "lane": active.get("lane"),
            "action": active.get("action"),
            "started_at": active.get("started_at"),
            "target": active.get("target"),
            "topic": active.get("topic"),
        }

    def _resolve_work_standards_profiles(self, work: WorkConfig) -> dict[str, Any]:
        profiles: dict[str, Any] = {}
        for lane in work.active_lanes:
            if lane not in ("thesis", "article"):
                continue
            try:
                profiles[lane] = resolve_standard_profile(
                    self.root_dir,
                    self._workspace_config(),
                    work,
                    lane=lane,
                    requested_profile_id=None,
                )
            except WorkspaceConfigError as exc:
                profiles[lane] = {"lane": lane, "error": str(exc)}
        return profiles

    def _runtime_record_matches_project(self, record: RuntimeRecord) -> bool:
        project_id = str(record.project_id or "").strip()
        if project_id:
            return project_id == self.project_id
        project_root = str(record.project_root or "").strip()
        if project_root:
            return Path(project_root).expanduser().resolve() == self.root_dir
        return self.root_dir == self.store.root_dir

    def _build_record_summary(
        self,
        lane: str,
        action: str,
        status: str,
        target: str | None,
        topic: str | None,
    ) -> str:
        subject = target or topic or "объект не указан"
        return (
            f"[{status}] {self.project_title} / {lane_title(lane)} / "
            f"{action_title(action)} -> {subject}"
        )

    def _active_run_matches(self, active: dict[str, Any]) -> bool:
        active_project_id = str(active.get("project_id") or "").strip()
        if active_project_id:
            return active_project_id == self.project_id
        active_root = str(active.get("project_root") or "").strip()
        if active_root:
            return Path(active_root).expanduser().resolve() == self.root_dir
        return self.root_dir == self.store.root_dir

    def _request_matches_project(self, request: dict[str, Any]) -> bool:
        request_project_id = str(request.get("project_id") or "").strip()
        if request_project_id:
            return request_project_id == self.project_id
        request_root = str(request.get("project_root") or "").strip()
        if request_root:
            return Path(request_root).expanduser().resolve() == self.root_dir
        return self.root_dir == self.store.root_dir

    def _workspace_config(self):
        if self._workspace is not None and self._workspace.root_dir == self.root_dir:
            return self._workspace
        try:
            self._workspace = load_workspace_config(self.root_dir)
        except WorkspaceConfigError as exc:
            raise WorkflowError(str(exc)) from exc
        return self._workspace

    def _work(self, work_id: str | None = None, target: str | None = None) -> WorkConfig:
        workspace = self._workspace_config()
        try:
            return resolve_work_config(workspace, work_id=work_id, target=target)
        except WorkspaceConfigError as exc:
            raise WorkflowError(str(exc)) from exc

    def _write_workflow_status(
        self,
        run_dir: Path,
        request: dict[str, Any],
        result: dict[str, Any],
        record: RunRecord,
        *,
        article_runtime: dict[str, Any] | None = None,
        thesis_runtime: dict[str, Any] | None = None,
    ) -> None:
        status_path = run_dir / "status.json"
        started_at = request.get("started_at", result.get("started_at", utc_now()))
        finished_at = result.get("finished_at")
        final_status = "succeeded"
        if record.status == "failed":
            final_status = "failed"
        elif record.status == "interrupted":
            final_status = "interrupted"
        elif record.status == "running":
            final_status = "running"

        final_stage = "completed"
        if final_status == "failed":
            final_stage = "failed"
        elif final_status == "interrupted":
            final_stage = "interrupted"
        elif final_status == "running":
            final_stage = "running"

        failure = None
        if final_status == "failed":
            message = str(result.get("error") or f"Launcher command exited with code {result.get('returncode')}.")
            failure = build_failure(
                "process",
                "command-exited-nonzero",
                message,
                retryable=True,
                details={"returncode": result.get("returncode")},
            )
        elif final_status == "interrupted":
            failure = build_failure(
                "runtime",
                "missing-result",
                str(result.get("error") or "Process exited without result.json"),
                retryable=True,
            )

        checkpoints = [
            build_checkpoint(
                "queued",
                status="queued",
                stage="queued",
                timestamp=started_at,
                message="Run wrapper started.",
            ),
            build_checkpoint(
                "command-started",
                status="running",
                stage="launching",
                timestamp=started_at,
                message=record.summary,
            ),
            build_checkpoint(
                "command-finished",
                status=final_status,
                stage=final_stage,
                timestamp=finished_at or utc_now(),
                message=record.summary,
                failure=failure,
            ),
        ]
        runtime_enrichment = article_runtime or thesis_runtime
        if article_runtime:
            article_phase = _optional_text(article_runtime.get("current_phase")) or final_stage
            checkpoints.append(
                build_checkpoint(
                    "article-bundle-synced",
                    status=final_status,
                    stage=article_phase,
                    timestamp=finished_at or utc_now(),
                    message=_optional_text(article_runtime.get("summary")) or record.summary,
                )
            )
            repair_decision = article_runtime.get("repair_decision")
            if isinstance(repair_decision, dict):
                decision_action = _optional_text(repair_decision.get("action")) or "n/a"
                decision_reason = _optional_text(repair_decision.get("reason")) or "n/a"
                checkpoints.append(
                    build_checkpoint(
                        "repair-decision-issued",
                        status=final_status,
                        stage=article_phase,
                        timestamp=finished_at or utc_now(),
                        message=f"{decision_action}: {decision_reason}",
                    )
                )
        elif thesis_runtime:
            thesis_stage = _optional_text(thesis_runtime.get("stage")) or final_stage
            checkpoints.append(
                build_checkpoint(
                    "thesis-runtime-synced",
                    status=final_status,
                    stage=thesis_stage,
                    timestamp=finished_at or utc_now(),
                    message=_optional_text(thesis_runtime.get("summary")) or record.summary,
                )
            )
            repair_decision = thesis_runtime.get("repair_decision")
            if isinstance(repair_decision, dict):
                decision_action = _optional_text(repair_decision.get("action")) or "n/a"
                decision_reason = _optional_text(repair_decision.get("reason")) or "n/a"
                checkpoints.append(
                    build_checkpoint(
                        "repair-decision-issued",
                        status=final_status,
                        stage=thesis_stage,
                        timestamp=finished_at or utc_now(),
                        message=f"{decision_action}: {decision_reason}",
                    )
                )
            thesis_repair_plan = thesis_runtime.get("thesis_repair_plan")
            if isinstance(thesis_repair_plan, dict) and thesis_repair_plan.get("suggested_command"):
                checkpoints.append(
                    build_checkpoint(
                        "thesis-repair-plan-issued",
                        status=final_status,
                        stage=thesis_stage,
                        timestamp=finished_at or utc_now(),
                        message=str(thesis_repair_plan.get("suggested_command")),
                    )
                )
        if runtime_enrichment:
            gate_summary = _contract_gate_summary(runtime_enrichment.get("contract_gates"))
            if gate_summary["total_count"]:
                checkpoints.append(
                    build_checkpoint(
                        "contract-gates-evaluated",
                        status=final_status,
                        stage=final_stage,
                        timestamp=finished_at or utc_now(),
                        message=(
                            f"blocks={gate_summary['block_count']}, "
                            f"warnings={gate_summary['warn_count']}, total={gate_summary['total_count']}"
                        ),
                    )
                )
        attachments = build_attachments(
            {
                "status": status_path,
                "request": run_dir / "request.json",
                "result": run_dir / "result.json",
                "log": result.get("log_path") or run_dir / "launcher.log",
                "manifest": record.manifest_path,
                "trace": record.output_file,
                "resolution": run_dir / "resolution.json",
                "bundle_state": article_runtime.get("bundle_state_manifest") if article_runtime else None,
            }
        )
        target_resolution = request.get("target_resolution")
        write_status(
            status_path,
            build_runtime_status(
                record_id=record.record_id,
                entity_kind="workflow-run",
                status=final_status,
                stage=final_stage,
                project_id=record.project_id,
                project_title=record.project_title,
                project_root=record.project_root,
                work_id=record.work_id,
                work_title=record.work_title,
                lane=record.lane,
                action=record.action,
                started_at=started_at,
                finished_at=finished_at,
                summary=_optional_text(runtime_enrichment.get("summary")) if runtime_enrichment else record.summary,
                failure=failure,
                blockers=runtime_enrichment.get("blockers") if runtime_enrichment else None,
                repair_decision=runtime_enrichment.get("repair_decision") if runtime_enrichment else None,
                repair_iteration=runtime_enrichment.get("repair_iteration") if runtime_enrichment else None,
                terminal_reason=_optional_text(runtime_enrichment.get("terminal_reason")) if runtime_enrichment else None,
                thesis_repair_plan=runtime_enrichment.get("thesis_repair_plan") if runtime_enrichment else None,
                contract_gates=runtime_enrichment.get("contract_gates") if runtime_enrichment else None,
                target_resolution=target_resolution if isinstance(target_resolution, dict) else None,
                checkpoints=checkpoints,
                attachments=attachments,
            ),
        )

    def _sync_article_runtime_state(
        self,
        request: dict[str, Any],
        record: RunRecord,
    ) -> dict[str, Any] | None:
        if record.lane != "article" or not record.work_id:
            return None
        work = self._work(record.work_id)
        manifest = self.store.read_json(Path(record.manifest_path)) if record.manifest_path else None
        if not isinstance(manifest, dict):
            return None
        bundle_payload = manifest.get("bundle")
        if not isinstance(bundle_payload, dict):
            return None
        article_slug = _optional_text(bundle_payload.get("slug"))
        if not article_slug:
            return None
        try:
            bundle = article_bundle_paths(work, article_slug)
        except WorkspaceConfigError:
            return None
        state_path = article_bundle_manifest_path(work, article_slug)
        previous_state = load_article_bundle_state(state_path)
        output_text = self._read_text(record.output_file)
        artifact_texts = {
            "output": output_text,
            "review": self._read_text(str(bundle["review"])),
            "checklist": self._read_text(str(bundle["checklist"])),
        }
        artifact_signals = extract_article_artifact_signals(artifact_texts)
        readiness_status = artifact_signals.readiness_status
        blockers = self._classify_article_blockers(
            bundle=bundle,
            manifest=manifest,
            readiness_status=readiness_status,
            artifact_blockers=artifact_signals.blockers,
        )
        effective_status = self._effective_article_status(readiness_status, blockers)
        terminal_reason = self._article_terminal_reason(effective_status, blockers)
        current_iteration = self._article_repair_iteration(record.action, previous_state)
        contract = execution_contract_from_payload(manifest.get("execution_contract"))
        contract_gates = self._contract_gate_payloads(contract=contract, work=work, lane="article", manifest=manifest)
        repair_decision = self._article_repair_decision(
            contract=contract,
            blockers=blockers,
            repair_iteration=current_iteration,
            terminal_reason=terminal_reason,
        )
        runtime_ids = self._merge_runtime_record_ids(
            previous_state.latest_runtime_record_ids if previous_state else (),
            record.record_id,
        )
        updated_state = build_article_bundle_state(
            work_id=work.slug,
            article_slug=article_slug,
            bundle=bundle,
            profile_id=_optional_text(manifest.get("resolved_profile_id")) or _optional_text(manifest.get("profile_id")),
            last_action=record.action,
            last_run_status=record.status,
            latest_run_manifest=record.manifest_path,
            latest_output_file=record.output_file,
            latest_runtime_record_ids=runtime_ids,
            readiness_status=effective_status,
            blockers=[item.to_dict() for item in blockers],
            repair_iteration=current_iteration,
            repair_decision=repair_decision,
            terminal_reason=terminal_reason,
            execution_contract=manifest.get("execution_contract") if isinstance(manifest.get("execution_contract"), dict) else None,
            topic=_optional_text(manifest.get("topic")),
            input_brief=_optional_text(manifest.get("input_brief")),
            target_path=_optional_text(manifest.get("target_path")),
            previous_state=previous_state,
        )
        from .article_bundle_state import write_article_bundle_state

        write_article_bundle_state(state_path, updated_state)
        present_files, _ = self._article_present_files(bundle)
        summary_block = self._build_article_bundle_summary(article_slug, updated_state, present_files)
        blocker_count = len(blockers)
        summary = record.summary
        if effective_status:
            summary = f"{summary} · article_status={effective_status}"
        if blocker_count:
            summary = f"{summary} · blockers={blocker_count}"
        if terminal_reason:
            summary = f"{summary} · terminal_reason={terminal_reason}"
        return {
            "article_slug": article_slug,
            "current_phase": updated_state.current_phase,
            "current_status": updated_state.current_status,
            "blockers": [item.to_dict() for item in blockers],
            "repair_decision": repair_decision,
            "repair_iteration": current_iteration,
            "terminal_reason": terminal_reason,
            "contract_gates": contract_gates,
            "bundle_state_manifest": str(state_path),
            "summary_block": summary_block,
            "summary": summary,
        }

    def _sync_thesis_runtime_state(
        self,
        request: dict[str, Any],
        record: RunRecord,
    ) -> dict[str, Any] | None:
        if record.lane != "thesis" or not record.work_id:
            return None
        manifest = self.store.read_json(Path(record.manifest_path)) if record.manifest_path else None
        if not isinstance(manifest, dict):
            return None
        contract = execution_contract_from_payload(manifest.get("execution_contract"))
        if contract is None or not contract.repair_policy.eligible:
            return None

        work = self._work(record.work_id)
        target_payload = manifest.get("target")
        target_rel = _optional_text((target_payload or {}).get("relative")) if isinstance(target_payload, dict) else None
        target_rel = target_rel or _optional_text(request.get("target")) or record.target
        if not target_rel:
            return None

        review_path_text = _optional_text(manifest.get("expected_review_file"))
        review_path = Path(review_path_text) if review_path_text else derive_review_path(self._workspace_config(), work, target_rel)
        review_present = review_path.exists() if review_path else False
        signals = extract_thesis_runtime_signals(
            {
                "output": self._read_text(record.output_file),
                "review": self._read_text(str(review_path)) if review_path else "",
            }
        )
        blockers = signals.blockers
        terminal_reason = self._thesis_terminal_reason(signals.status_hint, blockers)
        current_iteration = self._thesis_repair_iteration(
            request=request,
            manifest=manifest,
            record=record,
            target=target_rel,
        )
        contract_gates = self._contract_gate_payloads(contract=contract, work=work, lane="thesis", manifest=manifest)
        repair_decision = self._thesis_repair_decision(
            contract=contract,
            blockers=blockers,
            repair_iteration=current_iteration,
            terminal_reason=terminal_reason,
        )
        summary_block = self._compose_thesis_section_summary(
            target=target_rel,
            review_present=review_present,
            last_run_action=record.action,
            last_run_status=record.status,
            blocker_count=len(blockers),
            terminal_reason=terminal_reason,
        )
        thesis_repair_plan = build_thesis_repair_plan(
            section_summary=summary_block,
            blockers=blockers,
            contract=contract,
            target=target_rel,
            repair_iteration=current_iteration,
        ).to_dict()
        summary = record.summary
        if blockers:
            summary = f"{summary} · blockers={len(blockers)}"
        if terminal_reason:
            summary = f"{summary} · terminal_reason={terminal_reason}"
        return {
            "target": target_rel,
            "stage": "reviewed" if review_present else "drafted",
            "blockers": [item.to_dict() for item in blockers],
            "repair_decision": repair_decision,
            "repair_iteration": current_iteration,
            "terminal_reason": terminal_reason,
            "thesis_repair_plan": thesis_repair_plan,
            "contract_gates": contract_gates,
            "summary_block": summary_block,
            "summary": summary,
        }

    def _classify_article_blockers(
        self,
        *,
        bundle: dict[str, Path],
        manifest: dict[str, Any],
        readiness_status: str | None,
        artifact_blockers: tuple[Blocker, ...] = (),
    ) -> tuple[Blocker, ...]:
        blockers = list(artifact_blockers)
        missing_support = [name for name in ("evidence_pack", "claim_map") if not bundle[name].exists()]
        if readiness_status == "strong-draft-with-blockers":
            if missing_support:
                blockers.append(
                    Blocker(
                        category="primary-support",
                        code="evidence-coverage-gap",
                        message="Article bundle still lacks verified evidence coverage artifacts.",
                        repairable=True,
                        blocks_statuses=("submission-ready",),
                        details={"missing": missing_support},
                    )
                )
            elif not blockers:
                blockers.append(
                    Blocker(
                        category="review",
                        code="review-blockers-remain",
                        message="Article verdict still reports unresolved blockers.",
                        repairable=True,
                        blocks_statuses=("submission-ready",),
                    )
                )
        if readiness_status == "submission-ready":
            if missing_support:
                blockers.append(
                    Blocker(
                        category="primary-support",
                        code="submission-missing-evidence",
                        message="Submission-ready cannot be claimed while evidence coverage artifacts are missing.",
                        repairable=True,
                        blocks_statuses=("submission-ready",),
                        details={"missing": missing_support},
                    )
                )
            if not bundle["checklist"].exists():
                blockers.append(
                    Blocker(
                        category="artifact",
                        code="submission-checklist-missing",
                        message="Submission-ready cannot be claimed without a checklist artifact.",
                        repairable=True,
                        blocks_statuses=("submission-ready",),
                    )
                )
        if bool(manifest.get("profile_conflict_flag")) and readiness_status in {"submission-ready", "strong-draft-with-blockers"}:
            blockers.append(
                Blocker(
                    category="standards-consistency",
                    code="profile-conflict-flag",
                    message="The selected standards profile still has a visible conflict flag.",
                    repairable=True,
                    blocks_statuses=("submission-ready",),
                    details={"profile_id": _optional_text(manifest.get("resolved_profile_id")) or _optional_text(manifest.get("profile_id"))},
                )
            )
        deduped: list[Blocker] = []
        seen: set[tuple[str, str]] = set()
        for blocker in blockers:
            key = (blocker.category, blocker.code)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(blocker)
        return tuple(deduped)

    def _effective_article_status(self, readiness_status: str | None, blockers: tuple[Blocker, ...]) -> str | None:
        if blockers and readiness_status in {None, "submission-ready", "strong-draft"}:
            return "strong-draft-with-blockers"
        return readiness_status

    def _article_terminal_reason(self, readiness_status: str | None, blockers: tuple[Blocker, ...]) -> str | None:
        if blockers:
            return determine_terminal_reason(blockers)
        if readiness_status == "submission-ready":
            return "ready"
        if readiness_status == "strong-draft":
            return "ready-with-caveats"
        return None

    def _article_repair_iteration(self, action: str, previous_state: Any) -> int:
        previous_iteration = previous_state.repair_iteration if previous_state and previous_state.repair_iteration is not None else 0
        if action == "repair":
            return previous_iteration + 1
        return previous_iteration

    def _thesis_repair_iteration(
        self,
        *,
        request: dict[str, Any],
        manifest: dict[str, Any],
        record: RunRecord,
        target: str,
    ) -> int:
        explicit_iteration = _optional_int(request.get("repair_iteration"))
        if explicit_iteration is not None:
            return explicit_iteration
        explicit_iteration = _optional_int(manifest.get("repair_iteration"))
        if explicit_iteration is not None:
            return explicit_iteration
        return self._previous_thesis_repair_iteration(record, target)

    def _previous_thesis_repair_iteration(self, record: RunRecord, target: str) -> int:
        if not record.work_id:
            return 0
        current_run_dir = Path(record.runtime_run_dir).resolve() if record.runtime_run_dir else None
        latest_iteration = 0
        for run_dir in self.store.list_run_dirs():
            if current_run_dir is not None and run_dir.resolve() == current_run_dir:
                continue
            runtime_record = load_runtime_record(run_dir, "workflow-run")
            if runtime_record is None or runtime_record.lane != "thesis":
                continue
            if str(runtime_record.work_id or "").strip() != record.work_id:
                continue
            if not self._runtime_record_matches_project(runtime_record):
                continue
            if self._runtime_record_target(run_dir) != target:
                continue
            latest_iteration = max(latest_iteration, _effective_repair_iteration(runtime_record))
        return latest_iteration

    def _runtime_record_target(self, run_dir: Path) -> str | None:
        request = self.store.read_json(run_dir / "request.json", default={}) or {}
        request_target = _optional_text(request.get("target"))
        if request_target:
            return request_target
        resolution = self.store.read_json(run_dir / "resolution.json", default={}) or {}
        return _optional_text(resolution.get("target"))

    def _thesis_terminal_reason(self, status_hint: str | None, blockers: tuple[Blocker, ...]) -> str | None:
        if blockers:
            return determine_terminal_reason(blockers)
        if status_hint in {"ready-with-caveats", "blocked-runtime"}:
            return status_hint
        return None

    def _thesis_repair_decision(
        self,
        *,
        contract: Any,
        blockers: tuple[Blocker, ...],
        repair_iteration: int,
        terminal_reason: str | None,
    ) -> dict[str, Any]:
        payload = build_repair_decision(
            contract=contract,
            blockers=blockers,
            repair_iteration=repair_iteration,
        ).to_dict()
        if terminal_reason:
            payload["terminal_reason"] = terminal_reason
        return payload

    def _article_repair_decision(
        self,
        *,
        contract: Any,
        blockers: tuple[Blocker, ...],
        repair_iteration: int,
        terminal_reason: str | None,
    ) -> dict[str, Any]:
        if contract is not None:
            payload = build_repair_decision(
                contract=contract,
                blockers=blockers,
                repair_iteration=repair_iteration,
            ).to_dict()
        else:
            payload = {
                "action": "repair" if blockers else "stop",
                "reason": "repairable-blockers-available" if blockers else "blockers-cleared",
                "repair_iteration": repair_iteration,
                "blocker_count": len(blockers),
            }
        if terminal_reason:
            payload["terminal_reason"] = terminal_reason
        return payload

    def _contract_gate_payloads(
        self,
        *,
        contract: Any,
        work: WorkConfig,
        lane: str,
        manifest: dict[str, Any],
    ) -> list[dict[str, Any]]:
        profile = self._runtime_standard_profile(work, lane, manifest)
        return [item.to_dict() for item in evaluate_contract_gates(contract=contract, profile=profile)]

    def _runtime_standard_profile(
        self,
        work: WorkConfig,
        lane: str,
        manifest: dict[str, Any],
    ) -> Any:
        requested = (
            _optional_text(manifest.get("requested_profile_id"))
            or _optional_text(manifest.get("resolved_profile_id"))
            or _optional_text(manifest.get("profile_id"))
        )
        try:
            return resolve_standard_profile(
                self.root_dir,
                self._workspace_config(),
                work,
                lane=lane,
                requested_profile_id=requested,
            )
        except WorkspaceConfigError:
            return {
                "profile_id": requested,
                "resolved_profile_id": _optional_text(manifest.get("resolved_profile_id")) or requested,
                "normalized_path": _optional_text(manifest.get("profile_path")),
                "raw_status": _optional_text(manifest.get("profile_raw_status")),
                "official_only": manifest.get("profile_official_only", True),
                "conflict_flag": bool(manifest.get("profile_conflict_flag")),
            }

    def _merge_runtime_record_ids(self, existing: tuple[str, ...], record_id: str) -> tuple[str, ...]:
        merged: list[str] = []
        for item in (*existing, record_id):
            candidate = str(item).strip()
            if candidate and candidate not in merged:
                merged.append(candidate)
        return tuple(merged[-5:])

    def _read_text(self, path: str | None) -> str:
        if not path:
            return ""
        candidate = Path(path)
        if not candidate.exists():
            return ""
        return candidate.read_text(encoding="utf-8")


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
