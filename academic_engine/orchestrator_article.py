"""Article-lane helpers for WorkflowOrchestrator (mixin).

Extracted from orchestrator.py to keep article-specific status, summary,
blocker classification, runtime-state sync, and repair-iteration logic
together.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .article_bundle_state import (
    article_bundle_manifest_path,
    build_article_bundle_state,
    load_article_bundle_state,
    write_article_bundle_state,
)
from .article_runtime_signals import extract_article_artifact_signals
from .finalization_engine import evaluate_article_finalization
from .orchestrator_support import (
    RunRecord,
    WorkflowError,
    _optional_text,
)
from .repair_kernel import Blocker, build_repair_decision, determine_terminal_reason
from .workspace import (
    WorkspaceConfigError,
    article_bundle_paths,
)


class OrchestratorArticleMixin:
    """Article-specific status, summary, blocker and runtime-sync helpers."""

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
        from .action_specs import execution_contract_from_payload

        contract = execution_contract_from_payload(manifest.get("execution_contract"))
        contract_gates = self._contract_gate_payloads(contract=contract, work=work, lane="article", manifest=manifest)
        finalization_check = None
        if record.action == "finalize":
            finalization_check = evaluate_article_finalization(
                bundle=bundle,
                readiness_status=effective_status,
                blockers=[item.to_dict() for item in blockers],
                contract_gates=contract_gates,
            ).to_dict()
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
            profile_id=_optional_text(manifest.get("resolved_profile_id"))
            or _optional_text(manifest.get("profile_id")),
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
            execution_contract=manifest.get("execution_contract")
            if isinstance(manifest.get("execution_contract"), dict)
            else None,
            topic=_optional_text(manifest.get("topic")),
            input_brief=_optional_text(manifest.get("input_brief")),
            target_path=_optional_text(manifest.get("target_path")),
            previous_state=previous_state,
        )
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
        if isinstance(finalization_check, dict):
            summary = f"{summary} · finalization={finalization_check.get('finalization_status')}"
        return {
            "article_slug": article_slug,
            "current_phase": updated_state.current_phase,
            "current_status": updated_state.current_status,
            "blockers": [item.to_dict() for item in blockers],
            "repair_decision": repair_decision,
            "repair_iteration": current_iteration,
            "terminal_reason": terminal_reason,
            "contract_gates": contract_gates,
            "finalization_check": finalization_check,
            "bundle_state_manifest": str(state_path),
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
        if bool(manifest.get("profile_conflict_flag")) and readiness_status in {
            "submission-ready",
            "strong-draft-with-blockers",
        }:
            blockers.append(
                Blocker(
                    category="standards-consistency",
                    code="profile-conflict-flag",
                    message="The selected standards profile still has a visible conflict flag.",
                    repairable=True,
                    blocks_statuses=("submission-ready",),
                    details={
                        "profile_id": _optional_text(manifest.get("resolved_profile_id"))
                        or _optional_text(manifest.get("profile_id"))
                    },
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
        previous_iteration = (
            previous_state.repair_iteration if previous_state and previous_state.repair_iteration is not None else 0
        )
        if action == "repair":
            return previous_iteration + 1
        return previous_iteration

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
