from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contract_gates import evaluate_contract_gates
from .orchestrator_article import OrchestratorArticleMixin
from .orchestrator_exports import OrchestratorExportMixin
from .orchestrator_runtime import OrchestratorRuntimeMixin
from .orchestrator_support import (
    ARTICLE_ACTIONS,
    THESIS_ACTIONS,
    RunBusyError,
    RunRecord,
    WorkflowError,
    _contract_gate_summary,
    _optional_text,
    action_title,
    lane_title,
    slugify,
)
from .orchestrator_thesis import OrchestratorThesisMixin
from .quality_advisories import build_quality_advisories
from .runtime_status import (
    build_attachments,
    build_checkpoint,
    build_failure,
    build_runtime_status,
    write_status,
)
from .standards import resolve_standard_profile
from .state import RuntimeStore
from .thesis_evidence_ledger import audit_thesis_ledgers
from .utils import utc_now
from .work_state import build_work_state
from .workspace import (
    WorkConfig,
    WorkspaceConfigError,
    discover_article_slugs,
    list_targets_for_action,
    load_workspace_config,
    resolve_target_for_action,
    resolve_target_path,
    resolve_work_config,
)


class WorkflowOrchestrator(
    OrchestratorArticleMixin,
    OrchestratorThesisMixin,
    OrchestratorRuntimeMixin,
    OrchestratorExportMixin,
):
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

        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
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

    def get_artifact_status(self, subject: str, *, work_id: str | None = None) -> dict[str, Any]:
        work = self._work(work_id)
        if subject == "work":
            return self.get_work_state(work_id=work.slug)

        if subject == "thesis":
            sections = [
                self._thesis_section_status(path, work.slug) for path in self.list_thesis_sections(work_id=work.slug)
            ]
            return {
                "kind": "thesis-overview",
                "work_id": work.slug,
                "sections": sections,
                "summary": self._build_thesis_overview_summary(sections),
            }

        if subject.startswith("thesis:"):
            return self._thesis_section_status(subject.split(":", 1)[1], work.slug)

        if subject == "article":
            bundles = [
                self._article_bundle_status(slug, work.slug) for slug in self.list_article_slugs(work_id=work.slug)
            ]
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
        thesis_ledger_advisory: dict[str, Any] | None = None
        article_overview: dict[str, Any] | None = None

        if work.supports("thesis") and work.thesis:
            sections = [
                self._thesis_section_status(path, work.slug) for path in self.list_thesis_sections(work_id=work.slug)
            ]
            thesis_overview = {
                "kind": "thesis-overview",
                "work_id": work.slug,
                "sections": sections,
                "summary": self._build_thesis_overview_summary(sections),
            }
            thesis_ledger_advisory = audit_thesis_ledgers(work)

        if work.supports("article") and work.article:
            bundles = [
                self._article_bundle_status(slug, work.slug) for slug in self.list_article_slugs(work_id=work.slug)
            ]
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
            thesis_ledger_advisory=thesis_ledger_advisory,
            article_overview=article_overview,
            quality_advisories=build_quality_advisories(work),
            standards_profiles=self._resolve_work_standards_profiles(work),
            runtime_records=self._recent_workflow_runtime_records(work.slug, limit=5),
            active_run=self._active_workflow_run_for_work(work.slug),
        )

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
            finalization_check = runtime_enrichment.get("finalization_check")
            if isinstance(finalization_check, dict):
                checkpoints.append(
                    build_checkpoint(
                        "finalization-check-evaluated",
                        status=final_status,
                        stage=final_stage,
                        timestamp=finished_at or utc_now(),
                        message=(
                            f"{finalization_check.get('status') or 'n/a'}: "
                            f"{finalization_check.get('finalization_status') or 'n/a'}"
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
                terminal_reason=_optional_text(runtime_enrichment.get("terminal_reason"))
                if runtime_enrichment
                else None,
                thesis_repair_plan=runtime_enrichment.get("thesis_repair_plan") if runtime_enrichment else None,
                contract_gates=runtime_enrichment.get("contract_gates") if runtime_enrichment else None,
                finalization_check=runtime_enrichment.get("finalization_check") if runtime_enrichment else None,
                target_resolution=target_resolution if isinstance(target_resolution, dict) else None,
                checkpoints=checkpoints,
                attachments=attachments,
            ),
        )

    def _runtime_record_target(self, run_dir: Path) -> str | None:
        request = self.store.read_json(run_dir / "request.json", default={}) or {}
        request_target = _optional_text(request.get("target"))
        if request_target:
            return request_target
        resolution = self.store.read_json(run_dir / "resolution.json", default={}) or {}
        return _optional_text(resolution.get("target"))

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
