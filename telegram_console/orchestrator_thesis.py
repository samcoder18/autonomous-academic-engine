"""Thesis-lane helpers for WorkflowOrchestrator (mixin).

Extracted from orchestrator.py to keep the core orchestrator focused on
run lifecycle, leaving thesis-specific status, summary, runtime sync,
and repair-iteration logic here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .orchestrator_support import (
    THESIS_ACTIONS,
    RunRecord,
    _effective_repair_iteration,
    _optional_int,
    _optional_text,
)
from .repair_kernel import Blocker, build_repair_decision, determine_terminal_reason
from .runtime_status import RuntimeRecord, load_runtime_record
from .thesis_repair_planner import build_thesis_repair_plan
from .thesis_runtime_signals import extract_thesis_runtime_signals
from .workspace import derive_review_path


class OrchestratorThesisMixin:
    """Thesis-specific status, summary and runtime-sync helpers."""

    def _thesis_section_status(self, target: str, work_id: str) -> dict[str, Any]:
        work = self._work(work_id, target)
        section = self._validate_target("thesis", "write-section", target, work_id=work.slug)
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
        from .action_specs import execution_contract_from_payload

        contract = execution_contract_from_payload(manifest.get("execution_contract"))
        if contract is None or not contract.repair_policy.eligible:
            return None

        work = self._work(record.work_id)
        target_payload = manifest.get("target")
        target_rel = (
            _optional_text((target_payload or {}).get("relative")) if isinstance(target_payload, dict) else None
        )
        target_rel = target_rel or _optional_text(request.get("target")) or record.target
        if not target_rel:
            return None

        review_path_text = _optional_text(manifest.get("expected_review_file"))
        review_path = (
            Path(review_path_text)
            if review_path_text
            else derive_review_path(self._workspace_config(), work, target_rel)
        )
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
