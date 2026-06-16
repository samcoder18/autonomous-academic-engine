"""Runtime/records helpers for WorkflowOrchestrator (mixin).

Extracted from orchestrator.py: handles the run lifecycle around the
RuntimeStore — synchronisation of the active run, notification drain,
recent-runs listing, attachment resolution, manifest/runtime record
loading, record summaries, project/work filtering, and small helpers
used from the core orchestrator.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .orchestrator_support import (
    RunRecord,
    WorkflowError,
    action_title,
    lane_title,
)
from .runtime_status import RuntimeRecord, load_runtime_record
from .utils import parse_datetime, utc_now


class OrchestratorRuntimeMixin:
    """Active run sync, record loading and lightweight runtime helpers."""

    def sync_active_run(self, work_id: str | None = None) -> list[RunRecord]:
        active_runs = [self.store.get_active_run(work_id)] if work_id else self.store.list_active_runs()
        completed: list[RunRecord] = []
        for active in active_runs:
            if not active or not self._active_run_matches(active):
                continue
            run_dir = Path(active["run_dir"])
            request = self.store.read_json(run_dir / "request.json", default={}) or {}
            result = self.store.read_json(run_dir / "result.json")
            if result is None and self._pid_is_alive(int(active.get("pid", 0))):
                continue

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
            active_work_id = str(active.get("work_id") or "").strip() or None
            self.store.clear_active_run(active_work_id)
            self.store.append_notification(record.to_dict())
            completed.append(record)
        return completed

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

        active = self.store.get_active_run(work.slug)
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
                    **{key: value for key, value in resolution.items() if key in RunRecord.__dataclass_fields__}
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
        active = self.store.get_active_run(work_id)
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
        return f"[{status}] {self.project_title} / {lane_title(lane)} / {action_title(action)} -> {subject}"

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
