from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os
import re
import subprocess
import sys

from .state import RuntimeStore


THESIS_ACTIONS = (
    "full-cycle",
    "source-pack",
    "verify",
    "write-section",
    "review-section",
    "style-pass",
)
ARTICLE_ACTIONS = ("article", "review", "repair")

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


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_datetime(raw: str | None) -> datetime:
    if not raw:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if re.fullmatch(r"\d{8}-\d{6}", raw):
        return datetime.strptime(raw, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


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
        self.output_docx_dir = self.root_dir / "output" / "docx"
        self.article_docx_dir = self.output_docx_dir / "articles"
        self.thesis_manifest_dir = self.root_dir / "output" / "codex"
        self.article_manifest_dir = self.root_dir / "articles" / "runs"

    def list_targets(self, lane: str, action: str) -> list[str]:
        lane = lane.strip().lower()
        action = action.strip().lower()

        if lane == "thesis":
            patterns = {
                "full-cycle": (
                    "chapters/*.md",
                    "sources/*.md",
                    "manuscript/sections/*.md",
                    "reviews/*.md",
                ),
                "source-pack": ("sources/*.md",),
                "verify": (
                    "chapters/*.md",
                    "sources/*.md",
                    "manuscript/sections/*.md",
                    "reviews/*.md",
                ),
                "write-section": ("manuscript/sections/*.md",),
                "review-section": ("manuscript/sections/*.md",),
                "style-pass": ("manuscript/sections/*.md",),
            }.get(action)
            if not patterns:
                raise WorkflowError(f"Не знаю такого действия для диплома: {action}")
            return self._collect_targets(patterns)

        if lane == "article":
            patterns = {
                "article": ("articles/briefs/*.md",),
                "article-brief": ("articles/briefs/*.md",),
                "review": ("articles/drafts/*.md", "articles/final/*.md"),
                "repair": (
                    "articles/drafts/*.md",
                    "articles/final/*.md",
                    "articles/reviews/*.md",
                ),
            }.get(action)
            if not patterns:
                raise WorkflowError(f"Не знаю такого действия для статьи: {action}")
            return self._collect_targets(patterns)

        raise WorkflowError(f"Не понимаю такой контур работы: {lane}")

    def list_article_slugs(self) -> list[str]:
        slugs: set[str] = set()
        folders = (
            self.root_dir / "articles" / "briefs",
            self.root_dir / "articles" / "evidence",
            self.root_dir / "articles" / "claim-maps",
            self.root_dir / "articles" / "drafts",
            self.root_dir / "articles" / "reviews",
            self.root_dir / "articles" / "final",
            self.article_docx_dir,
        )
        for folder in folders:
            if not folder.exists():
                continue
            for path in folder.glob("*"):
                if path.name.startswith(".") or path.name == "README.md":
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

    def list_thesis_sections(self) -> list[str]:
        return self._collect_targets(("manuscript/sections/*.md",))

    def start_run(
        self,
        lane: str,
        action: str,
        target_or_topic: str,
        notes: str | None = None,
        search_override: bool | None = None,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        self.sync_active_run()
        active = self.store.get_active_run()
        if active:
            raise RunBusyError(self.describe_active_run(active))

        launcher_cmd, request_metadata = self._build_launch_command(
            lane=lane,
            action=action,
            target_or_topic=target_or_topic,
            notes=notes,
            search_override=search_override,
            model_override=model_override,
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

    def list_recent_runs(self, lane: str = "all", limit: int = 8) -> list[RunRecord]:
        self.sync_active_run()
        records: list[RunRecord] = []
        lane = lane.lower()

        include_thesis = lane in ("all", "thesis")
        include_article = lane in ("all", "article")

        if include_thesis:
            records.extend(self._load_manifest_records("thesis"))
        if include_article:
            records.extend(self._load_manifest_records("article"))

        records.extend(self._load_runtime_exception_records(lane))

        active = self.store.get_active_run()
        if active and self._active_run_matches(active) and lane in ("all", active["lane"]):
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

    def get_artifact_status(self, subject: str) -> dict[str, Any]:
        if subject == "thesis":
            return {
                "kind": "thesis-overview",
                "sections": [self._thesis_section_status(path) for path in self.list_thesis_sections()],
            }

        if subject.startswith("thesis:"):
            return self._thesis_section_status(subject.split(":", 1)[1])

        if subject == "article":
            return {
                "kind": "article-overview",
                "bundles": [self._article_bundle_status(slug) for slug in self.list_article_slugs()],
            }

        if subject.startswith("article:"):
            return self._article_bundle_status(subject.split(":", 1)[1])

        raise WorkflowError(f"Не смогла определить, какой артефакт ты хочешь открыть: {subject}")

    def export_docx(self, subject: str) -> dict[str, Any]:
        if subject == "thesis":
            cmd = ["bash", "scripts/export_docx.sh"]
            expected = self.output_docx_dir / "thesis-draft.docx"
        elif subject.startswith("article:"):
            slug = subject.split(":", 1)[1]
            status = self._article_bundle_status(slug)
            final_markdown = status["files"]["final"]["path"]
            if not Path(final_markdown).exists():
                raise WorkflowError(f"У статьи `{slug}` пока нет финального Markdown-файла для экспорта.")
            cmd = [
                "bash",
                "scripts/export_academic_docx.sh",
                self._relative_to_root(Path(final_markdown)),
            ]
            expected = self.article_docx_dir / f"{slug}.docx"
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
    ) -> tuple[list[str], dict[str, Any]]:
        lane = lane.strip().lower()
        action = action.strip().lower()
        notes_clean = notes.strip() if notes and notes.strip() else None

        if lane == "thesis":
            if action not in THESIS_ACTIONS:
                raise WorkflowError(f"Для диплома пока не поддерживается действие: {action}")
            target = self._validate_target("thesis", action, target_or_topic)
            cmd = ["bash", "scripts/codex_thesis.sh", action, target]
            if notes_clean:
                cmd.extend(["--notes", notes_clean])
            if search_override is True:
                cmd.append("--search")
            elif search_override is False:
                cmd.append("--no-search")
            if model_override:
                cmd.extend(["--model", model_override])
            return cmd, {"target": target}

        if lane == "article":
            if action not in ARTICLE_ACTIONS:
                raise WorkflowError(f"Для статьи пока не поддерживается действие: {action}")
            base = ["bash", "scripts/codex_academic.sh", action]
            metadata: dict[str, Any] = {}
            if action == "article":
                target_mode, target_value = self._resolve_article_input(target_or_topic)
                if target_mode == "brief":
                    brief = self._validate_target("article", "article-brief", target_value)
                    base.extend(["--brief", brief])
                    metadata["target"] = brief
                    metadata["input_mode"] = "brief"
                else:
                    topic = target_value.strip()
                    if not topic:
                        raise WorkflowError("Тема статьи не может быть пустой.")
                    base.extend(["--topic", topic])
                    metadata["topic"] = topic
                    metadata["input_mode"] = "topic"
            else:
                target = self._validate_target("article", action, target_or_topic)
                base.append(target)
                metadata["target"] = target

            if notes_clean:
                base.extend(["--notes", notes_clean])
            if search_override is True:
                base.append("--search")
            elif search_override is False:
                base.append("--no-search")
            if model_override:
                base.extend(["--model", model_override])
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

    def _validate_target(self, lane: str, action: str, target: str) -> str:
        normalized = self._normalize_relative_path(target)
        allowed = set(self.list_targets(lane, action))
        if normalized not in allowed:
            raise WorkflowError(
                f"Этот файл не подходит для сценария «{lane_title(lane)} / {action_title(action)}»:\n{normalized}"
            )
        return normalized

    def _normalize_relative_path(self, raw: str) -> str:
        raw_path = Path(raw.strip()).expanduser()
        candidate = raw_path if raw_path.is_absolute() else self.root_dir / raw_path
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise WorkflowError(f"Не нашла файл: {raw}") from exc

        try:
            relative = resolved.relative_to(self.root_dir)
        except ValueError as exc:
            raise WorkflowError(f"Этот путь находится вне корня проекта:\n{raw}") from exc
        return relative.as_posix()

    def _collect_targets(self, patterns: tuple[str, ...]) -> list[str]:
        targets: list[str] = []
        seen: set[str] = set()
        for pattern in patterns:
            for path in sorted(self.root_dir.glob(pattern)):
                if path.name == "README.md" or path.name.startswith("."):
                    continue
                rel = self._relative_to_root(path)
                if rel not in seen:
                    seen.add(rel)
                    targets.append(rel)
        return targets

    def _relative_to_root(self, path: Path) -> str:
        return path.resolve().relative_to(self.root_dir).as_posix()

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
        self.store.write_json(run_dir / "resolution.json", record.to_dict())
        return record

    def _resolve_manifest_outputs(
        self,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        if result.get("returncode") != 0:
            return (None, None)

        candidate_records = self._load_manifest_records(request["lane"])
        started = parse_datetime(request.get("started_at"))
        chosen: RunRecord | None = None
        for record in candidate_records:
            if record.action != request["action"]:
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

    def _load_manifest_records(self, lane: str) -> list[RunRecord]:
        records: list[RunRecord] = []
        if lane == "thesis":
            directory = self.thesis_manifest_dir
        elif lane == "article":
            directory = self.article_manifest_dir
        else:
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
                    target=target,
                    topic=topic,
                    manifest_path=str(manifest.resolve()),
                    output_file=data.get("output_file"),
                    source="manifest",
                    summary=self._build_record_summary(lane, action, "success", target, topic),
                )
            )
        return records

    def _load_runtime_exception_records(self, lane: str) -> list[RunRecord]:
        records: list[RunRecord] = []
        for run_dir in self.store.list_run_dirs():
            request = self.store.read_json(run_dir / "request.json")
            if not isinstance(request, dict) or not self._request_matches_project(request):
                continue
            run_lane = request.get("lane")
            if lane not in ("all", run_lane):
                continue
            resolution = self.store.read_json(run_dir / "resolution.json")
            result = self.store.read_json(run_dir / "result.json")
            if isinstance(resolution, dict):
                record = RunRecord(**resolution)
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

    def _thesis_section_status(self, target: str) -> dict[str, Any]:
        section = self._validate_target("thesis", "write-section", target)
        section_path = self.root_dir / section
        review_path = self.root_dir / "reviews" / f"{section_path.stem}-review.md"
        recent = [
            record.to_dict()
            for record in self.list_recent_runs("thesis", limit=20)
            if record.target == section
        ][:3]
        return {
            "kind": "thesis-section",
            "target": section,
            "review_path": str(review_path),
            "review_exists": review_path.exists(),
            "available_actions": list(THESIS_ACTIONS),
            "recent_runs": recent,
        }

    def _article_bundle_status(self, slug: str) -> dict[str, Any]:
        clean_slug = slug.strip()
        if not clean_slug:
            raise WorkflowError("Идентификатор статьи не может быть пустым.")

        files = {
            "brief": self.root_dir / "articles" / "briefs" / f"{clean_slug}.md",
            "evidence": self.root_dir / "articles" / "evidence" / f"{clean_slug}.md",
            "claim_map": self.root_dir / "articles" / "claim-maps" / f"{clean_slug}.md",
            "draft": self.root_dir / "articles" / "drafts" / f"{clean_slug}.md",
            "review": self.root_dir / "articles" / "reviews" / f"{clean_slug}.md",
            "final": self.root_dir / "articles" / "final" / f"{clean_slug}.md",
            "checklist": self.root_dir / "articles" / "final" / f"{clean_slug}-checklist.md",
            "docx": self.article_docx_dir / f"{clean_slug}.docx",
        }
        present = {name: {"path": str(path), "exists": path.exists()} for name, path in files.items()}
        missing = [name for name, info in present.items() if not info["exists"]]
        recent = [
            record.to_dict()
            for record in self.list_recent_runs("article", limit=20)
            if (record.target and clean_slug in record.target)
            or (record.output_file and clean_slug in record.output_file)
        ][:3]
        return {
            "kind": "article-bundle",
            "slug": clean_slug,
            "files": present,
            "missing": missing,
            "complete": not missing,
            "recent_runs": recent,
        }

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
