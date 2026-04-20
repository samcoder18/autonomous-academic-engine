"""Status/contract-gate helpers for WorkflowOrchestrator (mixin)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contract_gates import evaluate_contract_gates
from .orchestrator_support import _contract_gate_summary, _optional_text
from .runtime_status import (
    build_attachments,
    build_checkpoint,
    build_failure,
    build_runtime_status,
    write_status,
)
from .standards import resolve_standard_profile
from .utils import utc_now
from .workspace import WorkConfig, WorkspaceConfigError


class OrchestratorStatusMixin:
    """Workflow-status file emission and runtime profile helpers."""

    root_dir: Path
    store: Any

    def _write_workflow_status(
        self,
        run_dir: Path,
        request: dict[str, Any],
        result: dict[str, Any],
        record: Any,
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
