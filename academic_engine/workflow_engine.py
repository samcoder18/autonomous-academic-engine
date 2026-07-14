from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import tomllib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .action_specs import ExecutionContract
from .executors import (
    CallableRoleExecutor,
    ExecutorRouter,
    ExecutorUnavailableError,
    LegacyRoleExecutor,
    RoleExecutionContext,
    RoleExecutorProtocol,
    build_executor_router,
)
from .provider_write_contract import (
    PROVIDER_WRITE_PLAN_VERSION,
    ProviderWritePlan,
    ProviderWritePlanContext,
    parse_provider_write_plan,
    validate_provider_write_plan,
)
from .role_result_contract import (
    ALLOWED_BLOCKER_CATEGORIES,
    EVIDENCE_BLOCKER_CATEGORIES,
    ROLE_RESULT_VERSION,
    ArtifactRecord,
    RoleResultContext,
    validate_role_result_payload,
)
from .utils import utc_now

WORKFLOW_VERSION = "workflow-run/v1"
ROLE_TIMEOUT_SECONDS = 45 * 60
WORKFLOW_TIMEOUT_SECONDS = 240 * 60
MAX_CONCURRENT_WORKFLOWS = 2
VERIFIER_ROLE_IDS = {"thesis-source-verifier", "academic-source-verifier"}
_PROVIDER_WRITE_PLAN_INITIAL_INSTRUCTION = """
--- FIRST PROVIDER WRITE-PLAN PHASE ---
This is phase one of a provider write-plan route.
Return exactly one fenced `provider-write-plan` JSON block now.
Set its JSON `version` to `provider-write-plan/v1`.
The response must begin with exactly ```provider-write-plan (never ```json).
The response must end with exactly the closing ``` fence and contain no prose before or after the block.
Do not emit `role-result` or prose in this phase.
Do not change the sandbox directly.
"""
READINESS_ORDER = {
    "submission-ready": 0,
    "strong-draft": 1,
    "ready-with-caveats": 1,
    "reviewed": 1,
    "updated": 1,
    "strong-draft-with-blockers": 2,
    "blocked-primary-support": 2,
    "blocked-standards": 2,
    "blocked-runtime": 2,
    "not-evaluated": 3,
}


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    status: str
    reason: str
    blocking: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "gate_id": self.gate_id,
            "status": self.status,
            "reason": self.reason,
            "blocking": self.blocking,
        }
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class PromotionResult:
    status: str
    promoted: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "promoted": list(self.promoted),
            "conflicts": list(self.conflicts),
            "skipped": list(self.skipped),
        }
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class RoleNode:
    role_id: str
    policy_path: str
    checkpoints: tuple[str, ...] = ()
    evaluator: bool = False
    finalizer: bool = False


@dataclass
class RoleRun:
    role_run_id: str
    role_id: str
    policy_path: str
    workflow_id: str
    work_id: str
    lane: str
    action: str
    status: str
    started_at: str
    executor_route: str | None = None
    executor_id: str | None = None
    execution_mode: str | None = None
    write_plan_applied: bool = False
    reported_status: str | None = None
    finished_at: str | None = None
    attempt_count: int = 0
    checkpoints: list[str] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    output_file: str | None = None
    verdict: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_run_id": self.role_run_id,
            "role_id": self.role_id,
            "policy_path": self.policy_path,
            "workflow_id": self.workflow_id,
            "work_id": self.work_id,
            "lane": self.lane,
            "action": self.action,
            "executor_route": self.executor_route,
            "executor_id": self.executor_id,
            "execution_mode": self.execution_mode,
            "write_plan_applied": self.write_plan_applied,
            "status": self.status,
            "reported_status": self.reported_status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "attempt_count": self.attempt_count,
            "checkpoints": list(self.checkpoints),
            "blockers": list(self.blockers),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "changed_paths": list(self.changed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "output_file": self.output_file,
            "verdict": self.verdict,
            "error": self.error,
        }


@dataclass
class WorkflowRun:
    workflow_id: str
    run_id: str
    work_id: str
    lane: str
    action: str
    status: str
    execution_status: str
    readiness_status: str
    started_at: str
    workflow_dir: str
    sandbox_dir: str
    finished_at: str | None = None
    role_runs: list[RoleRun] = field(default_factory=list)
    gates: list[GateResult] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)
    promotion: PromotionResult | None = None
    evaluator_verdict: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        gate_counts: dict[str, int] = {}
        for gate in self.gates:
            gate_counts[gate.status] = gate_counts.get(gate.status, 0) + 1
        return {
            "version": WORKFLOW_VERSION,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "work_id": self.work_id,
            "lane": self.lane,
            "action": self.action,
            "status": self.status,
            "execution_status": self.execution_status,
            "readiness_status": self.readiness_status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "workflow_dir": self.workflow_dir,
            "sandbox_dir": self.sandbox_dir,
            "role_runs": [item.to_dict() for item in self.role_runs],
            "gates": [item.to_dict() for item in self.gates],
            "gate_summary": gate_counts,
            "blockers": list(self.blockers),
            "promotion": self.promotion.to_dict() if self.promotion else None,
            "promotion_status": self.promotion.status if self.promotion else "not-run",
            "evaluator_verdict": self.evaluator_verdict,
            "metadata": dict(self.metadata),
        }


class WorkflowBusyError(RuntimeError):
    pass


class ProviderWritePlanError(ExecutorUnavailableError):
    """Carry a fail-closed provider write-plan blocker into role state."""

    def __init__(self, blockers: list[dict[str, Any]]):
        message = (
            str(blockers[0].get("message", "Provider write plan failed."))
            if blockers
            else "Provider write plan failed."
        )
        super().__init__(message)
        self.blockers = blockers
        self.blocker_code = (
            str(blockers[0].get("code", "provider-write-plan-invalid")) if blockers else "provider-write-plan-invalid"
        )


class WorkflowLease:
    def __init__(
        self,
        root_dir: Path,
        work_id: str,
        limit: int = MAX_CONCURRENT_WORKFLOWS,
        *,
        wait: bool = False,
    ):
        self._lock_dir = root_dir / "output" / "runs" / ".locks"
        self._work_id = work_id
        self._limit = limit
        self._wait = wait
        self._work_handle: Any = None
        self._slot_handle: Any = None

    def __enter__(self) -> WorkflowLease:
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        work_path = self._lock_dir / f"work-{self._work_id}.lock"
        self._work_handle = work_path.open("a+", encoding="utf-8")
        try:
            operation = fcntl.LOCK_EX if self._wait else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.flock(self._work_handle.fileno(), operation)
        except BlockingIOError as exc:
            self._work_handle.close()
            self._work_handle = None
            raise WorkflowBusyError(f"Workflow already running for work `{self._work_id}`.") from exc

        while self._slot_handle is None:
            for index in range(self._limit):
                slot_path = self._lock_dir / f"slot-{index}.lock"
                handle = slot_path.open("a+", encoding="utf-8")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    handle.close()
                    continue
                self._slot_handle = handle
                break
            if self._slot_handle is not None or not self._wait:
                break
            time.sleep(0.1)
        if self._slot_handle is None:
            self.__exit__(None, None, None)
            raise WorkflowBusyError(f"Workflow concurrency limit ({self._limit}) reached.")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        for handle in (self._slot_handle, self._work_handle):
            if handle is None:
                continue
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        self._slot_handle = None
        self._work_handle = None


class WorkflowEngine:
    def __init__(
        self,
        root_dir: str | Path,
        *,
        executor_router: RoleExecutorProtocol | None = None,
        role_executor: LegacyRoleExecutor | None = None,
        role_timeout_seconds: int = ROLE_TIMEOUT_SECONDS,
        workflow_timeout_seconds: int = WORKFLOW_TIMEOUT_SECONDS,
    ):
        if executor_router is not None and role_executor is not None:
            raise ValueError("Pass either executor_router or role_executor, not both.")
        self.root_dir = Path(root_dir).resolve()
        if executor_router is not None:
            self.executor_router = executor_router
        elif role_executor is not None:
            self.executor_router = ExecutorRouter(default_executor=CallableRoleExecutor(role_executor))
        else:
            self.executor_router = build_executor_router()
        self.role_timeout_seconds = role_timeout_seconds
        self.workflow_timeout_seconds = workflow_timeout_seconds

    def run(
        self,
        *,
        workflow_id: str | None = None,
        work_id: str,
        work_dir: Path,
        lane: str,
        action: str,
        contract: ExecutionContract,
        base_prompt: str,
        use_search: bool,
        model: str | None,
        metadata: dict[str, Any] | None = None,
        role_plan: tuple[RoleNode, ...] | None = None,
        promotion_enabled: bool = True,
    ) -> WorkflowRun:
        workflow_id = workflow_id or _new_workflow_id(work_id, lane, action)
        workflow_dir = self.root_dir / "output" / "runs" / workflow_id
        sandbox_dir = workflow_dir / "sandbox"
        workflow = WorkflowRun(
            workflow_id=workflow_id,
            run_id=workflow_id,
            work_id=work_id,
            lane=lane,
            action=action,
            status="queued",
            execution_status="queued",
            readiness_status="not-evaluated",
            started_at=utc_now(),
            workflow_dir=str(workflow_dir),
            sandbox_dir=str(sandbox_dir),
            metadata=metadata or {},
        )
        workflow_dir.mkdir(parents=True, exist_ok=False)
        self._write_workflow(workflow)
        self._event(workflow_dir, "workflow-queued", {"work_id": work_id, "lane": lane, "action": action})

        with WorkflowLease(self.root_dir, work_id, wait=True):
            started = time.monotonic()
            try:
                workflow.status = "running"
                workflow.execution_status = "running"
                self._write_workflow(workflow)
                self._create_sandbox(sandbox_dir, work_dir)
                baseline = _file_manifest(sandbox_dir)
                canonical_baseline = _canonical_manifest(self.root_dir, baseline)
                _write_json(workflow_dir / "baseline.json", canonical_baseline)
                nodes = (
                    role_plan
                    if role_plan is not None
                    else build_role_plan(
                        lane,
                        action,
                        contract.required_checkpoints,
                    )
                )
                repair_iterations_used = 1 if lane == "article" and action == "repair" else 0

                for node in nodes:
                    if time.monotonic() - started >= self.workflow_timeout_seconds:
                        workflow.blockers.append(_runtime_blocker("workflow-timeout", "Workflow timeout exceeded."))
                        break
                    if node.finalizer and workflow.readiness_status == "strong-draft-with-blockers":
                        self._event(workflow_dir, "finalizer-running-with-blockers", {})
                    role = self._run_role(
                        workflow=workflow,
                        node=node,
                        sandbox_dir=sandbox_dir,
                        contract=contract,
                        base_prompt=base_prompt,
                        use_search=use_search,
                        model=model,
                    )
                    workflow.role_runs.append(role)
                    workflow.blockers.extend(role.blockers)
                    self._write_workflow(workflow)
                    if role.status != "succeeded":
                        break
                    if (
                        lane == "thesis"
                        and node.role_id == "thesis-argument-critic"
                        and workflow.blockers
                        and contract.repair_policy.eligible
                        and repair_iterations_used < 2
                    ):
                        repair_runs = self._run_repairs(
                            workflow=workflow,
                            sandbox_dir=sandbox_dir,
                            contract=contract,
                            base_prompt=base_prompt,
                            use_search=use_search,
                            model=model,
                            started=started,
                            start_iteration=repair_iterations_used + 1,
                            max_iterations=2 - repair_iterations_used,
                        )
                        repair_iterations_used += _repair_iteration_count(repair_runs)
                        workflow.blockers = _latest_blockers(workflow.role_runs, workflow.blockers)
                    if node.evaluator:
                        workflow.evaluator_verdict = role.verdict
                        workflow.readiness_status = _readiness_from_verdict(role.verdict)
                        should_repair = (
                            workflow.readiness_status == "strong-draft-with-blockers"
                            and contract.repair_policy.eligible
                            and repair_iterations_used < 2
                            and not (lane == "thesis" and node is nodes[-1] and repair_iterations_used > 0)
                        )
                        if should_repair:
                            repair_runs = self._run_repairs(
                                workflow=workflow,
                                sandbox_dir=sandbox_dir,
                                contract=contract,
                                base_prompt=base_prompt,
                                use_search=use_search,
                                model=model,
                                started=started,
                                start_iteration=repair_iterations_used + 1,
                                max_iterations=2 - repair_iterations_used,
                            )
                            repair_iterations_used += _repair_iteration_count(repair_runs)
                            workflow.blockers = _latest_blockers(workflow.role_runs, workflow.blockers)
                            latest_verdict = _latest_evaluator_verdict(workflow.role_runs)
                            if latest_verdict:
                                workflow.evaluator_verdict = latest_verdict
                                workflow.readiness_status = _readiness_from_verdict(latest_verdict)
                            if lane == "thesis" and repair_runs:
                                final_pass = self._run_thesis_post_repair(
                                    workflow=workflow,
                                    sandbox_dir=sandbox_dir,
                                    contract=contract,
                                    base_prompt=base_prompt,
                                    use_search=use_search,
                                    model=model,
                                )
                                latest_verdict = _latest_evaluator_verdict(final_pass)
                                if latest_verdict:
                                    workflow.evaluator_verdict = latest_verdict
                                    workflow.readiness_status = _readiness_from_verdict(latest_verdict)
                                workflow.blockers = _latest_blockers(workflow.role_runs, workflow.blockers)

                final_manifest = _file_manifest(sandbox_dir)
                changed_paths = _changed_paths(baseline, final_manifest)
                gates = self._evaluate_gates(
                    workflow=workflow,
                    contract=contract,
                    sandbox_dir=sandbox_dir,
                    changed_paths=changed_paths,
                )
                workflow.gates = gates
                _write_json(
                    workflow_dir / "gates.json",
                    {
                        "version": WORKFLOW_VERSION,
                        "workflow_id": workflow.workflow_id,
                        "gates": [gate.to_dict() for gate in gates],
                    },
                )
                if any(item.blocking and item.status != "pass" for item in gates):
                    workflow.readiness_status = _downgrade_readiness(workflow.readiness_status)
                if promotion_enabled:
                    workflow.promotion = self._promote(
                        workflow=workflow,
                        contract=contract,
                        sandbox_dir=sandbox_dir,
                        baseline=canonical_baseline,
                        final_manifest=final_manifest,
                        changed_paths=changed_paths,
                    )
                else:
                    workflow.promotion = PromotionResult(
                        status="skipped",
                        skipped=tuple(changed_paths),
                        reason="qualification-no-promotion",
                    )
                    self._write_promotion_manifest(workflow, workflow.promotion)
                execution_blocked = any(
                    blocker.get("code") in {"workflow-timeout", "workflow-exception"} for blocker in workflow.blockers
                )
                workflow.execution_status = (
                    "succeeded"
                    if workflow.role_runs
                    and all(role.status == "succeeded" for role in workflow.role_runs)
                    and not execution_blocked
                    else "failed"
                )
                workflow.status = "completed" if workflow.execution_status == "succeeded" else "failed"
            except Exception as exc:
                workflow.status = "failed"
                workflow.execution_status = "failed"
                workflow.readiness_status = "strong-draft-with-blockers"
                workflow.blockers.append(_runtime_blocker("workflow-exception", str(exc)))
                self._event(workflow_dir, "workflow-exception", {"error": str(exc)})
            finally:
                workflow.finished_at = utc_now()
                gates_path = workflow_dir / "gates.json"
                if not gates_path.exists():
                    _write_json(
                        gates_path,
                        {
                            "version": WORKFLOW_VERSION,
                            "workflow_id": workflow.workflow_id,
                            "gates": [gate.to_dict() for gate in workflow.gates],
                        },
                    )
                if workflow.promotion is None:
                    workflow.promotion = PromotionResult(
                        status="blocked",
                        reason="Workflow did not reach promotion.",
                    )
                    self._write_promotion_manifest(workflow, workflow.promotion)
                self._write_workflow(workflow)
                self._event(
                    workflow_dir,
                    "workflow-finished",
                    {
                        "status": workflow.status,
                        "execution_status": workflow.execution_status,
                        "readiness_status": workflow.readiness_status,
                    },
                )
        return workflow

    def _create_sandbox(self, sandbox_dir: Path, work_dir: Path) -> None:
        sandbox_dir.mkdir(parents=True, exist_ok=False)
        ignored = shutil.ignore_patterns(".git", "output", "__pycache__", "*.pyc", ".DS_Store")
        for name in ("agents", "meta", "templates", "scripts", "academic_engine"):
            source = self.root_dir / name
            if source.exists():
                shutil.copytree(source, sandbox_dir / name, ignore=ignored)
        for name in ("AGENTS.md", "README.md", "workspace.toml", "pyproject.toml"):
            source = self.root_dir / name
            if source.exists():
                shutil.copy2(source, sandbox_dir / name)
        target_work = sandbox_dir / work_dir.resolve().relative_to(self.root_dir)
        target_work.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(work_dir, target_work, ignore=ignored)

    def _run_role(
        self,
        *,
        workflow: WorkflowRun,
        node: RoleNode,
        sandbox_dir: Path,
        contract: ExecutionContract,
        base_prompt: str,
        use_search: bool,
        model: str | None,
    ) -> RoleRun:
        role_run_id = f"{len(workflow.role_runs) + 1:02d}-{node.role_id}"
        role_dir = Path(workflow.workflow_dir) / "roles" / role_run_id
        role_dir.mkdir(parents=True, exist_ok=False)
        output_file = role_dir / "output.md"
        request_file = role_dir / "request.json"
        before = _file_manifest(sandbox_dir)
        policy_file = sandbox_dir / node.policy_path
        if not policy_file.exists():
            policy_file = Path(__file__).resolve().parents[1] / node.policy_path
        role = RoleRun(
            role_run_id=role_run_id,
            role_id=node.role_id,
            policy_path=node.policy_path,
            workflow_id=workflow.workflow_id,
            work_id=workflow.work_id,
            lane=workflow.lane,
            action=workflow.action,
            status="running",
            started_at=utc_now(),
            output_file=str(output_file),
        )
        context = RoleExecutionContext(
            workflow_id=workflow.workflow_id,
            role_run_id=role_run_id,
            role_id=node.role_id,
            work_id=workflow.work_id,
            lane=workflow.lane,
            action=workflow.action,
            sandbox_dir=sandbox_dir,
            output_file=output_file,
            use_search=use_search,
            model=model,
            timeout_seconds=self.role_timeout_seconds,
            is_evaluator=node.evaluator,
            is_verifier=node.role_id in VERIFIER_ROLE_IDS,
            is_finalizer=node.finalizer,
        )
        if isinstance(self.executor_router, ExecutorRouter):
            selection = self.executor_router.describe_selection(context)
            role.executor_route = selection.route_name
            role.executor_id = selection.executor_id
            role.execution_mode = selection.execution_mode
            context = replace(context, execution_mode=selection.execution_mode)
        else:
            role.executor_route = (
                "evaluator" if context.is_evaluator else "verifier" if context.is_verifier else "default"
            )
            role.executor_id = "custom"
        allowed = _allowed_relative_scopes(
            self.root_dir,
            _role_allowed_write_scopes(node, contract),
        )
        policy_text = policy_file.read_text(encoding="utf-8")
        if context.execution_mode == "write-plan":
            provider_base_prompt = _provider_write_base_prompt(base_prompt, self.root_dir, sandbox_dir)
            role_result_prompt = _role_prompt(
                workflow=workflow,
                node=node,
                contract=contract,
                policy_text=policy_text,
                base_prompt=provider_base_prompt,
                root_dir=self.root_dir,
                sandbox_dir=sandbox_dir,
                provider_write_result=True,
            )
            provider_write_plan_wire_context = _provider_write_plan_wire_context(
                workflow_id=workflow.workflow_id,
                role_run_id=role_run_id,
                role_id=node.role_id,
                work_id=workflow.work_id,
                allowed_write_scopes=allowed,
                pre_write_manifest=before,
            )
            prompt = _role_prompt(
                workflow=workflow,
                node=node,
                contract=contract,
                policy_text=policy_text,
                base_prompt=provider_base_prompt,
                root_dir=self.root_dir,
                sandbox_dir=sandbox_dir,
                execution_mode=context.execution_mode,
                provider_write_plan_wire_context=provider_write_plan_wire_context,
            )
        else:
            sandbox_base_prompt = base_prompt.replace(str(self.root_dir), str(sandbox_dir))
            role_result_prompt = _role_prompt(
                workflow=workflow,
                node=node,
                contract=contract,
                policy_text=policy_text,
                base_prompt=sandbox_base_prompt,
                root_dir=self.root_dir,
                sandbox_dir=sandbox_dir,
            )
            prompt = role_result_prompt
        _write_json(
            request_file,
            {
                "version": WORKFLOW_VERSION,
                "workflow_id": workflow.workflow_id,
                "role_run_id": role_run_id,
                "role_id": node.role_id,
                "work_id": workflow.work_id,
                "lane": workflow.lane,
                "action": workflow.action,
                "policy_path": node.policy_path,
                "checkpoints": list(node.checkpoints),
                "allowed_write_scopes": _sandbox_write_scopes(
                    self.root_dir,
                    sandbox_dir,
                    node,
                    contract,
                ),
            },
        )
        self._event(Path(workflow.workflow_dir), "role-started", {"role_run_id": role_run_id})
        error: Exception | None = None
        provider_result_evidence_envelope: dict[str, Any] | None = None
        provider_write_plan_applied = False
        for attempt in range(1, 3):
            role.attempt_count = attempt
            try:
                started = time.monotonic()
                self.executor_router.execute(context, prompt)
                if time.monotonic() - started > self.role_timeout_seconds:
                    raise TimeoutError(f"Role `{node.role_id}` exceeded {self.role_timeout_seconds} seconds.")
                raw_output = output_file.read_text(encoding="utf-8", errors="replace") if output_file.exists() else ""
                write_plan_payload, write_plan_blockers = parse_provider_write_plan(raw_output)
                if write_plan_payload is None:
                    if context.execution_mode == "write-plan" or any(
                        item["code"] != "provider-write-plan-block-missing" for item in write_plan_blockers
                    ):
                        raise ProviderWritePlanError(write_plan_blockers)
                else:
                    if context.execution_mode == "read-only":
                        raise ProviderWritePlanError(
                            [
                                _provider_write_blocker(
                                    "provider-write-plan-route-forbidden",
                                    "Provider write plans are forbidden for a read-only role.",
                                )
                            ]
                        )
                    first_after = _file_manifest(sandbox_dir)
                    first_changes = _changed_paths(before, first_after)
                    if first_changes:
                        raise ProviderWritePlanError(
                            [
                                _provider_write_blocker(
                                    "provider-write-plan-direct-write-forbidden",
                                    "Provider returned a write plan after changing the sandbox directly.",
                                    details={"paths": first_changes},
                                )
                            ]
                        )
                    if not allowed:
                        raise ProviderWritePlanError(
                            [
                                _provider_write_blocker(
                                    "provider-write-plan-route-forbidden",
                                    "Provider write plans are forbidden for a read-only role.",
                                )
                            ]
                        )
                    write_context = ProviderWritePlanContext(
                        workflow_id=workflow.workflow_id,
                        role_run_id=role_run_id,
                        role_id=node.role_id,
                        work_id=workflow.work_id,
                        sandbox_dir=sandbox_dir,
                        allowed_write_scopes=allowed,
                        pre_write_manifest=before,
                    )
                    write_plan, write_plan_blockers = validate_provider_write_plan(write_plan_payload, write_context)
                    if write_plan is None:
                        raise ProviderWritePlanError(write_plan_blockers)
                    post_write_manifest, apply_blockers = _apply_provider_write_plan(
                        sandbox_dir=sandbox_dir,
                        payload=write_plan_payload,
                        context=write_context,
                    )
                    if post_write_manifest is None:
                        raise ProviderWritePlanError(apply_blockers)
                    provider_write_plan_applied = True
                    provider_result_evidence_envelope = _provider_write_result_evidence_envelope(
                        write_plan,
                        post_write_manifest,
                        node.checkpoints,
                    )
                    follow_up_prompt = _provider_write_result_prompt(
                        role_result_prompt,
                        provider_result_evidence_envelope,
                    )
                    self.executor_router.execute(context, follow_up_prompt)
                    if time.monotonic() - started > self.role_timeout_seconds:
                        raise TimeoutError(f"Role `{node.role_id}` exceeded {self.role_timeout_seconds} seconds.")
                    follow_up_after = _file_manifest(sandbox_dir)
                    follow_up_changes = _changed_paths(post_write_manifest, follow_up_after)
                    if follow_up_changes:
                        raise ProviderWritePlanError(
                            [
                                _provider_write_blocker(
                                    "provider-write-plan-direct-write-forbidden",
                                    "Provider changed the sandbox during the role-result follow-up.",
                                    details={"paths": follow_up_changes},
                                )
                            ]
                        )
                error = None
                break
            except ExecutorUnavailableError as exc:
                error = exc
                break
            except (OSError, TimeoutError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                error = exc
                if provider_write_plan_applied or attempt >= 2:
                    break
            except Exception as exc:
                error = exc
                break

        after = _file_manifest(sandbox_dir)
        changed = _changed_paths(before, after)
        forbidden = [path for path in changed if not _path_is_allowed(path, allowed)]
        role.changed_paths = changed
        role.forbidden_paths = forbidden
        if error is not None:
            role.status = "failed"
            role.error = str(error)
            if isinstance(error, ProviderWritePlanError):
                role.blockers.extend(error.blockers)
            elif isinstance(error, ExecutorUnavailableError):
                blocker_code = getattr(error, "blocker_code", "executor-unavailable")
                role.blockers.append(_runtime_blocker(str(blocker_code), f"{node.role_id}: {error}"))
            else:
                role.blockers.append(_runtime_blocker("role-execution-failed", f"{node.role_id}: {error}"))
        else:
            role_result, result_blockers = _parse_role_result(
                output_file,
                workflow=workflow,
                node=node,
                contract=contract,
                root_dir=self.root_dir,
                sandbox_dir=sandbox_dir,
                after=after,
                changed_paths=changed,
                provider_result_evidence_envelope=provider_result_evidence_envelope,
            )
            role.blockers.extend(result_blockers)
            if role_result is None:
                role.status = "failed"
            else:
                role.reported_status = str(role_result["status"])
                role.checkpoints = list(role_result["checkpoints"])
                role.artifacts = list(role_result["artifacts"])
                role.blockers.extend(role_result["blockers"])
                role.verdict = role_result["verdict"]
                role.status = "failed" if role.reported_status == "failed" else "succeeded"
                role.write_plan_applied = provider_write_plan_applied and role.status == "succeeded"
        if forbidden:
            role.status = "failed"
            role.blockers.append(
                {
                    "category": "artifact",
                    "code": "write-scope-violation",
                    "message": f"Role `{node.role_id}` changed files outside allowed scopes.",
                    "repairable": False,
                    "details": {"paths": forbidden},
                }
            )
        deleted = [path for path in changed if path not in after]
        if deleted:
            role.status = "failed"
            role.blockers.append(
                {
                    "category": "artifact",
                    "code": "artifact-deletion-forbidden",
                    "message": f"Role `{node.role_id}` deleted files; v1 promotion is no-delete.",
                    "repairable": False,
                    "details": {"paths": deleted},
                }
            )
        role.finished_at = utc_now()
        _write_json(role_dir / "result.json", role.to_dict())
        self._event(
            Path(workflow.workflow_dir),
            "role-finished",
            {"role_run_id": role_run_id, "status": role.status, "changed_paths": changed},
        )
        return role

    def _run_repairs(
        self,
        *,
        workflow: WorkflowRun,
        sandbox_dir: Path,
        contract: ExecutionContract,
        base_prompt: str,
        use_search: bool,
        model: str | None,
        started: float,
        start_iteration: int,
        max_iterations: int,
    ) -> list[RoleRun]:
        runs: list[RoleRun] = []
        for iteration in range(start_iteration, start_iteration + max_iterations):
            if time.monotonic() - started >= self.workflow_timeout_seconds:
                break
            nodes = repair_role_plan(workflow.lane, iteration)
            for node in nodes:
                role = self._run_role(
                    workflow=workflow,
                    node=node,
                    sandbox_dir=sandbox_dir,
                    contract=contract,
                    base_prompt=(
                        f"{base_prompt}\n\nRepair iteration: {iteration}.\n"
                        f"Current blockers:\n{json.dumps(workflow.blockers, ensure_ascii=False, indent=2)}"
                    ),
                    use_search=use_search,
                    model=model,
                )
                runs.append(role)
                workflow.role_runs.append(role)
                workflow.blockers.extend(role.blockers)
                self._write_workflow(workflow)
                if role.status != "succeeded":
                    return runs
            verdict = _latest_evaluator_verdict(runs)
            if verdict and _readiness_from_verdict(verdict) != "strong-draft-with-blockers":
                break
        return runs

    def _run_thesis_post_repair(
        self,
        *,
        workflow: WorkflowRun,
        sandbox_dir: Path,
        contract: ExecutionContract,
        base_prompt: str,
        use_search: bool,
        model: str | None,
    ) -> list[RoleRun]:
        runs: list[RoleRun] = []
        for role_id in ("thesis-style-editor", "thesis-submission-evaluator"):
            node = RoleNode(
                role_id=role_id,
                policy_path=_ROLE_POLICIES[role_id],
                evaluator=role_id == "thesis-submission-evaluator",
            )
            role = self._run_role(
                workflow=workflow,
                node=node,
                sandbox_dir=sandbox_dir,
                contract=contract,
                base_prompt=f"{base_prompt}\n\nFinal pass after bounded thesis repair.",
                use_search=use_search,
                model=model,
            )
            runs.append(role)
            workflow.role_runs.append(role)
            workflow.blockers.extend(role.blockers)
            self._write_workflow(workflow)
            if role.status != "succeeded":
                break
        return runs

    def _evaluate_gates(
        self,
        *,
        workflow: WorkflowRun,
        contract: ExecutionContract,
        sandbox_dir: Path,
        changed_paths: list[str],
    ) -> list[GateResult]:
        gates: list[GateResult] = []
        for item in contract.required_context:
            path = _sandbox_path(self.root_dir, sandbox_dir, item.path)
            exists = path.exists()
            blocking = item.requirement == "required"
            gates.append(
                GateResult(
                    gate_id=f"required-context:{item.name}",
                    status="pass" if exists else ("block" if blocking else "not-applicable"),
                    reason=f"Context `{item.name}` {'exists' if exists else 'is missing'}.",
                    blocking=blocking and not exists,
                    details={"path": str(path)},
                )
            )
        for item in contract.required_outputs:
            path = _sandbox_path(self.root_dir, sandbox_dir, item.path)
            exists = path.exists()
            blocking = item.requirement == "required"
            gates.append(
                GateResult(
                    gate_id=f"required-output:{item.name}",
                    status="pass" if exists else ("block" if blocking else "not-applicable"),
                    reason=f"Output `{item.name}` {'exists' if exists else 'is missing'}.",
                    blocking=blocking and not exists,
                    details={"path": str(path)},
                )
            )
        observed = {checkpoint for role in workflow.role_runs for checkpoint in role.checkpoints}
        for checkpoint in contract.required_checkpoints:
            present = checkpoint in observed
            gates.append(
                GateResult(
                    gate_id=f"checkpoint:{checkpoint}",
                    status="pass" if present else "block",
                    reason=f"Checkpoint `{checkpoint}` {'was observed' if present else 'was not observed'}.",
                    blocking=not present,
                )
            )
        forbidden = sorted({path for role in workflow.role_runs for path in role.forbidden_paths})
        gates.append(
            GateResult(
                gate_id="allowed-write-scopes",
                status="pass" if not forbidden else "block",
                reason="All writes stayed in allowed scopes." if not forbidden else "Forbidden writes detected.",
                blocking=bool(forbidden),
                details={"forbidden_paths": forbidden, "changed_paths": changed_paths},
            )
        )
        evaluator_ok = workflow.evaluator_verdict is not None
        gates.append(
            GateResult(
                gate_id="evaluator-verdict",
                status="pass" if evaluator_ok else "block",
                reason="Independent evaluator verdict is valid." if evaluator_ok else "Evaluator verdict is missing.",
                blocking=not evaluator_ok,
            )
        )
        verifier_runs = [
            role
            for role in workflow.role_runs
            if role.role_id in {"thesis-source-verifier", "academic-source-verifier"}
        ]
        if verifier_runs:
            verifier_ok = any(role.verdict is not None for role in verifier_runs)
            gates.append(
                GateResult(
                    gate_id="source-verifier-verdict",
                    status="pass" if verifier_ok else "block",
                    reason=(
                        "Source verifier emitted a structured verdict."
                        if verifier_ok
                        else "Source verifier verdict is missing or invalid."
                    ),
                    blocking=not verifier_ok,
                )
            )
            provenance_summary = _source_provenance_summary(
                sandbox_dir,
                workflow.work_id,
            )
            live_provenance = (
                provenance_summary["total"] > 0 and provenance_summary["live"] == provenance_summary["total"]
            )
            gates.append(
                GateResult(
                    gate_id="live-source-provenance",
                    status="pass" if live_provenance else "block",
                    reason=(
                        "Live source provenance is present."
                        if live_provenance
                        else "No complete live source provenance record was found."
                    ),
                    blocking=not live_provenance,
                    details=provenance_summary,
                )
            )
        for gate in contract.quality_gates:
            blocking_categories = _gate_blocker_categories(gate.gate_id)
            blocked = any(item.get("category") in blocking_categories for item in workflow.blockers)
            if gate.gate_id == "lane-boundary":
                blocked = bool(forbidden)
            if gate.gate_id == "evaluator-verdict":
                blocked = not evaluator_ok
            if gate.gate_id == "standards-consistency":
                blocked = blocked or bool(workflow.metadata.get("profile_conflict_flag"))
            gates.append(
                GateResult(
                    gate_id=f"quality:{gate.gate_id}",
                    status="block" if blocked else "pass",
                    reason=gate.description,
                    blocking=blocked,
                )
            )
        return gates

    def _promote(
        self,
        *,
        workflow: WorkflowRun,
        contract: ExecutionContract,
        sandbox_dir: Path,
        baseline: dict[str, dict[str, Any] | None],
        final_manifest: dict[str, dict[str, Any]],
        changed_paths: list[str],
    ) -> PromotionResult:
        technical_block = any(role.status != "succeeded" for role in workflow.role_runs) or any(
            gate.blocking
            and gate.status != "pass"
            and (
                gate.gate_id == "allowed-write-scopes"
                or gate.gate_id.startswith("required-context:")
                or gate.gate_id.startswith("checkpoint:")
            )
            for gate in workflow.gates
        )
        invalid = [
            path for path in changed_paths if path in final_manifest and not _artifact_schema_valid(sandbox_dir / path)
        ]
        deleted = [path for path in changed_paths if path not in final_manifest]
        if technical_block or invalid or deleted:
            result = PromotionResult(
                status="blocked",
                skipped=tuple(changed_paths),
                reason="Technical gates, artifact schema validation, or no-delete policy failed.",
            )
            self._write_promotion_manifest(workflow, result)
            return result
        conflicts: list[str] = []
        promotable: list[str] = []
        skipped: list[str] = []
        readiness_blocked = workflow.readiness_status == "strong-draft-with-blockers"
        for path in changed_paths:
            source = sandbox_dir / path
            if not source.exists():
                skipped.append(path)
                continue
            if readiness_blocked and source.suffix.casefold() == ".docx":
                skipped.append(path)
                continue
            promotable.append(path)

        promotion_lock = self.root_dir / "output" / "runs" / ".locks" / "promotion.lock"
        promotion_lock.parent.mkdir(parents=True, exist_ok=True)
        with promotion_lock.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                conflicts = [path for path in promotable if _file_record(self.root_dir / path) != baseline.get(path)]
                if conflicts:
                    result = PromotionResult(
                        status="conflict",
                        conflicts=tuple(conflicts),
                        skipped=tuple(sorted(set(changed_paths) - set(conflicts))),
                        reason="Canonical files changed after workflow baseline.",
                    )
                    self._write_promotion_manifest(workflow, result)
                    return result
                promotion_error = self._atomic_promote_files(
                    workflow=workflow,
                    sandbox_dir=sandbox_dir,
                    promotable=promotable,
                )
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        if promotion_error:
            result = PromotionResult(
                status="blocked",
                skipped=tuple(changed_paths),
                reason=f"Atomic promotion failed and was rolled back: {promotion_error}",
            )
            self._write_promotion_manifest(workflow, result)
            return result
        status = "promoted" if promotable else "no-changes"
        result = PromotionResult(status=status, promoted=tuple(promotable), skipped=tuple(skipped))
        self._write_promotion_manifest(workflow, result)
        return result

    def _atomic_promote_files(
        self,
        *,
        workflow: WorkflowRun,
        sandbox_dir: Path,
        promotable: list[str],
    ) -> str | None:
        backup_root = Path(workflow.workflow_dir) / "promotion-backup"
        staged: dict[str, Path] = {}
        existing: set[str] = set()
        replaced: list[str] = []
        try:
            for path in promotable:
                source = sandbox_dir / path
                destination = self.root_dir / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(delete=False, dir=str(destination.parent)) as handle:
                    temp_path = Path(handle.name)
                shutil.copy2(source, temp_path)
                staged[path] = temp_path
                if destination.exists():
                    existing.add(path)
                    backup = backup_root / path
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, backup)
            for path in promotable:
                staged[path].replace(self.root_dir / path)
                replaced.append(path)
            return None
        except Exception as exc:  # noqa: BLE001 - rollback must cover filesystem failures
            for path in reversed(replaced):
                destination = self.root_dir / path
                backup = backup_root / path
                try:
                    if path in existing and backup.exists():
                        shutil.copy2(backup, destination)
                    elif destination.exists():
                        destination.unlink()
                except OSError:
                    pass
            return str(exc)
        finally:
            for temp_path in staged.values():
                if temp_path.exists():
                    temp_path.unlink()

    def _write_promotion_manifest(self, workflow: WorkflowRun, result: PromotionResult) -> None:
        _write_json(
            Path(workflow.workflow_dir) / "promotion.json",
            {
                "version": WORKFLOW_VERSION,
                "workflow_id": workflow.workflow_id,
                "readiness_status": workflow.readiness_status,
                "promoted_at": utc_now(),
                **result.to_dict(),
            },
        )

    def _write_workflow(self, workflow: WorkflowRun) -> None:
        _write_json(Path(workflow.workflow_dir) / "workflow.json", workflow.to_dict())

    def _event(self, workflow_dir: Path, event: str, payload: dict[str, Any]) -> None:
        record = {"timestamp": utc_now(), "event": event, **payload}
        with (workflow_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_role_plan(lane: str, action: str, checkpoints: tuple[str, ...]) -> tuple[RoleNode, ...]:
    role_ids = _ROLE_PLANS.get((lane, action))
    if role_ids is None:
        role_ids = _ROLE_PLANS[(lane, "full-cycle" if lane == "thesis" else "article")]
    checkpoint_groups = _distribute(checkpoints, len(role_ids))
    return tuple(
        RoleNode(
            role_id=role_id,
            policy_path=_ROLE_POLICIES[role_id],
            checkpoints=checkpoint_groups[index] or (f"role-completed:{role_id}",),
            evaluator=role_id in {"thesis-submission-evaluator", "academic-submission-evaluator"},
            finalizer=role_id == "academic-finalizer",
        )
        for index, role_id in enumerate(role_ids)
    )


def repair_role_plan(lane: str, iteration: int) -> tuple[RoleNode, ...]:
    if lane == "article":
        ids = (
            "academic-repair-orchestrator",
            "academic-source-verifier",
            "academic-citation-checker",
            "academic-submission-evaluator",
        )
    else:
        ids = (
            "thesis-draft-writer",
            "thesis-source-verifier",
            "thesis-citation-checker",
            "thesis-submission-evaluator",
        )
    return tuple(
        RoleNode(
            role_id=role_id,
            policy_path=_ROLE_POLICIES[role_id],
            checkpoints=(f"repair-{iteration}:{role_id}",),
            evaluator=role_id in {"thesis-submission-evaluator", "academic-submission-evaluator"},
        )
        for role_id in ids
    )


_ROLE_POLICIES = {
    "thesis-structure-architect": "agents/structure-architect.md",
    "thesis-research-synthesizer": "agents/research-synthesizer.md",
    "thesis-source-verifier": "agents/source-verifier.md",
    "thesis-draft-writer": "agents/draft-writer.md",
    "thesis-citation-checker": "agents/citation-checker.md",
    "thesis-argument-critic": "agents/argument-critic.md",
    "thesis-style-editor": "agents/style-editor.md",
    "thesis-submission-evaluator": "agents/thesis-submission-evaluator.md",
    "academic-intake": "agents/academic-intake.md",
    "academic-source-acquirer": "agents/academic-source-acquirer.md",
    "academic-source-verifier": "agents/academic-source-verifier.md",
    "academic-evidence-cartographer": "agents/academic-evidence-cartographer.md",
    "academic-draft-writer": "agents/academic-draft-writer.md",
    "academic-citation-checker": "agents/academic-citation-checker.md",
    "academic-counterargument-critic": "agents/academic-counterargument-critic.md",
    "academic-submission-evaluator": "agents/academic-submission-evaluator.md",
    "academic-repair-orchestrator": "agents/academic-repair-orchestrator.md",
    "academic-finalizer": "agents/academic-finalizer.md",
}

_ROLE_PLANS = {
    ("thesis", "full-cycle"): (
        "thesis-structure-architect",
        "thesis-research-synthesizer",
        "thesis-source-verifier",
        "thesis-draft-writer",
        "thesis-citation-checker",
        "thesis-argument-critic",
        "thesis-style-editor",
        "thesis-submission-evaluator",
    ),
    ("thesis", "source-pack"): (
        "thesis-research-synthesizer",
        "thesis-source-verifier",
        "thesis-submission-evaluator",
    ),
    ("thesis", "verify"): (
        "thesis-source-verifier",
        "thesis-citation-checker",
        "thesis-submission-evaluator",
    ),
    ("thesis", "write-section"): (
        "thesis-source-verifier",
        "thesis-draft-writer",
        "thesis-citation-checker",
        "thesis-submission-evaluator",
    ),
    ("thesis", "review-section"): (
        "thesis-citation-checker",
        "thesis-argument-critic",
        "thesis-submission-evaluator",
    ),
    ("thesis", "style-pass"): (
        "thesis-style-editor",
        "thesis-submission-evaluator",
    ),
    ("thesis", "build-maps"): (
        "thesis-structure-architect",
        "thesis-research-synthesizer",
        "thesis-source-verifier",
        "thesis-submission-evaluator",
    ),
    ("thesis", "verify-claims"): (
        "thesis-source-verifier",
        "thesis-citation-checker",
        "thesis-submission-evaluator",
    ),
    ("thesis", "counterargument-pass"): (
        "thesis-argument-critic",
        "thesis-submission-evaluator",
    ),
    ("thesis", "draft-author-position"): (
        "thesis-source-verifier",
        "thesis-draft-writer",
        "thesis-argument-critic",
        "thesis-submission-evaluator",
    ),
    ("thesis", "formal-artifacts"): (
        "thesis-citation-checker",
        "thesis-submission-evaluator",
    ),
    ("article", "article"): (
        "academic-intake",
        "academic-source-acquirer",
        "academic-source-verifier",
        "academic-evidence-cartographer",
        "academic-draft-writer",
        "academic-citation-checker",
        "academic-counterargument-critic",
        "academic-submission-evaluator",
        "academic-finalizer",
    ),
    ("article", "review"): (
        "academic-citation-checker",
        "academic-counterargument-critic",
        "academic-submission-evaluator",
    ),
    ("article", "repair"): (
        "academic-repair-orchestrator",
        "academic-source-verifier",
        "academic-citation-checker",
        "academic-submission-evaluator",
        "academic-finalizer",
    ),
    ("article", "finalize"): (
        "academic-citation-checker",
        "academic-submission-evaluator",
        "academic-finalizer",
    ),
}


def _role_prompt(
    *,
    workflow: WorkflowRun,
    node: RoleNode,
    contract: ExecutionContract,
    policy_text: str,
    base_prompt: str,
    root_dir: Path,
    sandbox_dir: Path,
    execution_mode: str | None = None,
    provider_write_plan_wire_context: dict[str, Any] | None = None,
    provider_write_result: bool = False,
) -> str:
    role_run_id = f"{len(workflow.role_runs) + 1:02d}-{node.role_id}"
    if execution_mode == "write-plan":
        if provider_write_plan_wire_context is None:
            raise ValueError("Provider write-plan prompts require trusted wire context.")
        context = _sanitize_role_context(base_prompt)
        return f"""You are an isolated role worker in deterministic workflow `{workflow.workflow_id}`.

Workflow ID: {workflow.workflow_id}
Role ID: {node.role_id}
Role Run ID: {role_run_id}
Work ID: {workflow.work_id}
Lane/action: {workflow.lane}/{workflow.action}

The role policy below is authoritative. Execute only this role; do not orchestrate other roles.

--- ROLE POLICY ---
{policy_text}
--- END ROLE POLICY ---

Workflow context:
{context}

--- TRUSTED PROVIDER WRITE-PLAN WIRE CONTEXT ---
{json.dumps(provider_write_plan_wire_context, ensure_ascii=False, indent=2)}
--- END TRUSTED PROVIDER WRITE-PLAN WIRE CONTEXT ---

Required checkpoints:
{json.dumps(list(node.checkpoints), ensure_ascii=False)}

Rules:
- The role policy and Workflow context are the complete provider-visible input.
- Provider/chat routes cannot call tools or read files.
- Do not emit tool calls, `read_file` requests, shell commands, or instructions to inspect files.
{_PROVIDER_WRITE_PLAN_INITIAL_INSTRUCTION}
- Use the Trusted provider write-plan wire context as the only source for the JSON `version`,
  `workflow_id`, `role_run_id`, `role_id`, and `work_id` values; copy them verbatim.
- The `fence_label` and `eligible_files` entries are prompt-only wire context, never plan fields.
- The JSON object must contain exactly these top-level fields and no others:
  `version`, `workflow_id`, `role_run_id`, `role_id`, `work_id`, and `operations`.
- Each non-empty `operations` entry must contain only `path`, `base_sha256`, and full replacement `content`.
- Each operation `path` and `base_sha256` pair must match one `eligible_files` entry exactly; never request a
  deletion or rename.
- Do not include `lane`, `action`, prose, or any extra field in the plan JSON.
"""
    context = (
        _sanitize_role_context(base_prompt)
        if provider_write_result
        else _role_context(
            workflow=workflow,
            node=node,
            contract=contract,
            base_prompt=base_prompt,
            sandbox_dir=sandbox_dir,
        )
    )
    artifact_example_path = f"works/{workflow.work_id}/path/to/artifact.md"
    artifact_example_hash = "<64 lowercase hex>"
    evidence_envelope = None
    read_only_provider_role = node.evaluator or node.role_id in VERIFIER_ROLE_IDS
    repair_no_change_evidence_envelope = None
    if read_only_provider_role:
        artifacts = _file_manifest(sandbox_dir / "works" / workflow.work_id)
        evidence_envelope = _provider_result_evidence_envelope(
            work_id=workflow.work_id,
            artifacts=artifacts,
            checkpoints=node.checkpoints,
        )
        if evidence_envelope is not None:
            artifact = evidence_envelope["artifacts"][0]
            artifact_example_path = str(artifact["path"])
            artifact_example_hash = str(artifact["sha256"])
    elif node.checkpoints and all(re.fullmatch(r"repair-\d+:[^:]+", checkpoint) for checkpoint in node.checkpoints):
        repair_no_change_evidence_envelope = _repair_no_change_evidence_envelope(
            root_dir=root_dir,
            sandbox_dir=sandbox_dir,
            contract=contract,
            work_id=workflow.work_id,
            artifacts=_file_manifest(sandbox_dir / "works" / workflow.work_id),
            checkpoints=node.checkpoints,
        )
    success_verdict_example = "null"
    blocked_verdict_example = "null"
    if node.evaluator:
        success_verdict_example = json.dumps(
            {
                "verdict_version": "1",
                "lane": workflow.lane,
                "kind": "submission-evaluator",
                "status": "submission-ready",
                "summary": "Independent evaluation complete.",
                "blockers": [],
            },
            ensure_ascii=False,
        )
        blocked_verdict_example = json.dumps(
            {
                "verdict_version": "1",
                "lane": workflow.lane,
                "kind": "submission-evaluator",
                "status": "strong-draft-with-blockers",
                "summary": "Independent evaluation found unresolved evidence.",
                "blockers": [
                    {
                        "category": "primary-support",
                        "code": "primary-support-missing",
                        "message": "Primary support is still missing.",
                        "repairable": True,
                    }
                ],
            },
            ensure_ascii=False,
        )
    checkpoint_evidence_example = json.dumps(
        evidence_envelope["checkpoint_evidence"]
        if evidence_envelope is not None
        else {checkpoint: [artifact_example_path] for checkpoint in node.checkpoints},
        ensure_ascii=False,
    )
    writable_evidence_preflight = ""
    if not read_only_provider_role:
        writable_evidence_preflight = "\n".join(
            (
                "- Writable-role preflight: calculate each reported `artifacts[].sha256` in the sandbox",
                "  immediately before the final `role-result`, for example with "
                "`shasum -a 256 <sandbox-relative-path>`.",
                "- Every listed digest must be the real 64-character lowercase SHA-256 value; never omit it",
                "  for a blocked or failed result and never copy the `<64 lowercase hex>` example placeholder.",
                f"- Writable-role preflight checkpoint keys: {json.dumps(list(node.checkpoints), ensure_ascii=False)}.",
                "  Copy these literal keys into `checkpoint_evidence` and map each to an artifact whose actual",
                "  SHA-256 appears in `artifacts`.",
            )
        )
    repair_no_change_evidence_prompt = ""
    if repair_no_change_evidence_envelope is not None:
        repair_no_change_evidence_prompt = f"""- No-change repair fallback applies only when you make no file changes
  and return `blocked` or `failed`, such as when the bounded repair limit is reached.
- Copy `artifacts` and `checkpoint_evidence` from `repair_no_change_evidence_envelope` verbatim only while
  the supplied artifact remains unchanged.
- Do not claim that the fallback artifact was modified.
- If you change any artifact, do not use this fallback: calculate post-write SHA-256 values and list every
  changed artifact in `artifacts` as above.
--- REPAIR NO-CHANGE EVIDENCE ENVELOPE ---
{json.dumps({"repair_no_change_evidence_envelope": repair_no_change_evidence_envelope}, ensure_ascii=False, indent=2)}
--- END REPAIR NO-CHANGE EVIDENCE ENVELOPE ---"""
    sandbox_root_line = "" if provider_write_result else f"Sandbox root: {workflow.sandbox_dir}"
    allowed_write_scopes = ""
    if not provider_write_result:
        allowed_write_scopes = f"""Allowed write scopes:
{json.dumps(_sandbox_write_scopes(root_dir, sandbox_dir, node, contract), ensure_ascii=False, indent=2)}"""
    provider_visible_input_rule = ""
    if provider_write_result:
        provider_visible_input_rule = (
            "- The role policy and Workflow context are the complete provider-visible input.\n"
        )
    verdict_rule = "- Put the structured verdict object in `verdict`; evaluator roles must not use `null`."
    if provider_write_result and not node.evaluator:
        verdict_rule = (
            "- This non-evaluator provider write-plan result must set `verdict` to `null`.\n"
            "- Do not put a structured verdict object inside `role-result` in this phase."
        )
    return f"""You are an isolated role worker in deterministic workflow `{workflow.workflow_id}`.

Workflow ID: {workflow.workflow_id}
Role ID: {node.role_id}
Role Run ID: {role_run_id}
Work ID: {workflow.work_id}
Lane/action: {workflow.lane}/{workflow.action}
{sandbox_root_line}

The role policy below is authoritative. Execute only this role; do not orchestrate other roles.

--- ROLE POLICY ---
{policy_text}
--- END ROLE POLICY ---

Workflow context:
{context}

{allowed_write_scopes}

Required checkpoints:
{json.dumps(list(node.checkpoints), ensure_ascii=False)}

Rules:
{provider_visible_input_rule}- Work only inside the sandbox and active work.
- Do not edit role policies, runtime code, workspace configuration, or other works.
- Do not create or export DOCX unless this is the finalizer and every blocker is resolved.
- Preserve unresolved blockers explicitly.
- End with the role's required fenced `verdict` block when the role policy requires one.
- End with exactly one fenced `role-result` JSON block after all prose.
- Provider/chat routes cannot call tools or read files; when `read_only_provider_context` is true,
  treat the Workflow context as the complete provider-visible input.
- Do not emit tool calls, `read_file` requests, shell commands, or instructions to inspect files.
- The opening fence must be exactly ```role-result; the JSON `version` field must be "{ROLE_RESULT_VERSION}".
- Do not use ```role-result/v1 as the fence label and do not use ```json for the role-result block.
- The `role-result` must repeat the exact workflow, role, and work identifiers.
- Report every required checkpoint and map it to at least one hash-verified artifact.
- `checkpoint_evidence` must include every required checkpoint as an object key,
  each mapped to one or more paths that also appear in `artifacts[].path`.
- Do not leave `checkpoint_evidence` empty when required checkpoints are listed.
- Required checkpoint strings are literal. Copy every string from Required checkpoints exactly into both
  `checkpoints` and `checkpoint_evidence`; never replace a dynamic repair checkpoint with a generic placeholder.
- A blocked or failed result must still map every required checkpoint to a non-empty artifact list.
- For a repair checkpoint, record a managed review or repair artifact in `artifacts` with its SHA-256 and map the
  exact `repair-N:<role-id>` key to that artifact.
{writable_evidence_preflight}
{repair_no_change_evidence_prompt}
- If Workflow context includes `artifact_manifest`, use those SHA-256 records for unchanged read-only artifacts.
- If you cannot verify checkpoint evidence, return structured `blocked` or `failed`;
  do not return shell commands or prose only.
- A `succeeded` result is invalid unless all required checkpoints have hash-verified artifact evidence.
- If blockers remain, use status `blocked` or `failed`; never report `succeeded` with blockers.
- Every blocker must use a stable lowercase machine code such as `primary-support-missing`, not free-form prose.
- Every blocker `category` must be exactly one of:
  {json.dumps(sorted(ALLOWED_BLOCKER_CATEGORIES), ensure_ascii=False)}
- Evidence roles must use only these blocker categories in `blockers` and `verdict.blockers`:
  {json.dumps(sorted(EVIDENCE_BLOCKER_CATEGORIES), ensure_ascii=False)}
- Read-only provider access gaps are `verification` or `process` blockers for evidence roles, not `runtime`.
- List every created or modified artifact with its sandbox-relative path and SHA-256.
- Do not invent artifact paths or SHA-256 values.
- For read-only provider routes, use only paths and hashes from `artifact_manifest` in `artifacts`.
- For read-only provider routes, `artifact_manifest` is exhaustive.
- Do not cite paths from role policy, formal contract, or expected outputs unless they appear in `artifact_manifest`.
- For read-only provider routes, include in `artifacts` only manifest pairs referenced by `checkpoint_evidence`;
  do not copy unrelated `artifact_manifest` entries.
- For read-only provider routes, copy `provider_result_evidence_envelope` verbatim into `artifacts` and
  `checkpoint_evidence`. Do not add, omit, or alter its entries.
{verdict_rule}
- Evaluator roles must repeat every Required checkpoint and its manifest-backed evidence even when the role status is
  `blocked` or `failed`.
- Evaluator roles must include a non-null `verdict` even when the role status is `blocked` or `failed`.
- A structured `verdict` may only use these top-level fields: `verdict_version`, `lane`, `kind`,
  `status`, `target`, `summary`, `blockers`, `notes`, `metrics`.
- `verdict.notes` must be an array of strings when present; use [] or omit it instead of a string.
- `verdict.metrics` must be an object when present; use {{}} or omit it instead of null.
- Put role-specific verdict metadata such as loop counts, reroute decisions, or review measurements under
  `metrics` or `notes`, never as extra top-level fields.

Required role result shape:
```role-result
{{
  "version": "{ROLE_RESULT_VERSION}",
  "workflow_id": "{workflow.workflow_id}",
  "role_run_id": "{role_run_id}",
  "role_id": "{node.role_id}",
  "work_id": "{workflow.work_id}",
  "lane": "{workflow.lane}",
  "action": "{workflow.action}",
  "status": "succeeded",
  "checkpoints": {json.dumps(list(node.checkpoints), ensure_ascii=False)},
  "checkpoint_evidence": {checkpoint_evidence_example},
  "blockers": [],
  "artifacts": [
    {{"path": "{artifact_example_path}", "sha256": "{artifact_example_hash}"}}
  ],
  "verdict": {success_verdict_example}
}}
```

If the role cannot honestly satisfy the checkpoints, return status `blocked` or `failed` and include blockers like:
```role-result
{{
  "version": "{ROLE_RESULT_VERSION}",
  "workflow_id": "{workflow.workflow_id}",
  "role_run_id": "{role_run_id}",
  "role_id": "{node.role_id}",
  "work_id": "{workflow.work_id}",
  "lane": "{workflow.lane}",
  "action": "{workflow.action}",
  "status": "blocked",
  "checkpoints": {json.dumps(list(node.checkpoints), ensure_ascii=False)},
  "checkpoint_evidence": {checkpoint_evidence_example},
  "blockers": [
    {{
      "category": "primary-support",
      "code": "primary-support-missing",
      "message": "Primary support is still missing.",
      "repairable": true
    }}
  ],
  "artifacts": [
    {{"path": "{artifact_example_path}", "sha256": "{artifact_example_hash}"}}
  ],
  "verdict": {blocked_verdict_example}
}}
```
"""


def _sandbox_write_scopes(
    root_dir: Path,
    sandbox_dir: Path,
    node: RoleNode,
    contract: ExecutionContract,
) -> list[dict[str, str]]:
    scopes: list[dict[str, str]] = []
    for item in _role_allowed_write_scopes(node, contract):
        scopes.append(
            {
                "name": item.name,
                "path": str(_sandbox_path(root_dir, sandbox_dir, item.path)),
                "description": item.description,
            }
        )
    return scopes


def _provider_write_plan_wire_context(
    *,
    workflow_id: str,
    role_run_id: str,
    role_id: str,
    work_id: str,
    allowed_write_scopes: tuple[str, ...],
    pre_write_manifest: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    eligible_files: list[dict[str, str]] = []
    for path in sorted(pre_write_manifest):
        if not _path_is_allowed(path, allowed_write_scopes):
            continue
        base_sha256 = str(pre_write_manifest[path].get("sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", base_sha256):
            continue
        eligible_files.append({"path": path, "base_sha256": base_sha256})
    return {
        "fence_label": "provider-write-plan",
        "version": PROVIDER_WRITE_PLAN_VERSION,
        "workflow_id": workflow_id,
        "role_run_id": role_run_id,
        "role_id": role_id,
        "work_id": work_id,
        "eligible_files": eligible_files,
    }


def _role_allowed_write_scopes(
    node: RoleNode,
    contract: ExecutionContract,
) -> tuple[Any, ...]:
    if node.evaluator:
        return ()
    return contract.allowed_write_scopes


def _required_output_paths(root_dir: Path, contract: ExecutionContract) -> tuple[str, ...]:
    paths: list[str] = []
    for item in contract.required_outputs:
        path = Path(item.path)
        if path.is_absolute():
            try:
                paths.append(path.resolve().relative_to(root_dir.resolve()).as_posix())
            except ValueError:
                continue
        else:
            paths.append(path.as_posix())
    return tuple(dict.fromkeys(paths))


_ROLE_RESULT_PATTERN = re.compile(
    r"```[ \t]*role-result[ \t]*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def _parse_role_result(
    path: Path,
    *,
    workflow: WorkflowRun,
    node: RoleNode,
    contract: ExecutionContract,
    root_dir: Path,
    sandbox_dir: Path,
    after: dict[str, dict[str, Any]],
    changed_paths: list[str],
    provider_result_evidence_envelope: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not path.exists():
        return None, [_runtime_blocker("role-result-block-missing", "Role produced no output.")]
    text = path.read_text(encoding="utf-8", errors="replace")
    matches = list(_ROLE_RESULT_PATTERN.finditer(text))
    if not matches:
        return None, [_runtime_blocker("role-result-block-missing", "Role produced no role-result block.")]
    if len(matches) != 1:
        return None, [
            _runtime_blocker(
                "role-result-block-count-invalid",
                f"Expected exactly one role-result block, found {len(matches)}.",
            )
        ]
    try:
        payload = json.loads(matches[0].group("body"))
    except json.JSONDecodeError as exc:
        return None, [_runtime_blocker("role-result-json-invalid", f"Role result is not valid JSON: {exc}.")]

    validated, result_blockers = validate_role_result_payload(
        payload,
        RoleResultContext(
            workflow_id=workflow.workflow_id,
            expected_role_run_id=f"{len(workflow.role_runs) + 1:02d}-{node.role_id}",
            role_id=node.role_id,
            work_id=workflow.work_id,
            lane=workflow.lane,
            action=workflow.action,
            required_checkpoints=node.checkpoints,
            sandbox_dir=sandbox_dir,
            post_manifest=after,
            changed_paths=tuple(changed_paths),
            required_output_paths=_required_output_paths(root_dir, contract),
            evaluator=node.evaluator,
            finalizer=node.finalizer,
            provider_result_evidence_envelope=provider_result_evidence_envelope,
        ),
    )
    if validated is None:
        return None, result_blockers

    return {
        "status": validated.status,
        "checkpoints": list(validated.checkpoints),
        "blockers": list(validated.blockers),
        "artifacts": list(validated.artifacts),
        "verdict": validated.verdict,
    }, []


def _apply_provider_write_plan(
    *,
    sandbox_dir: Path,
    payload: object,
    context: ProviderWritePlanContext,
) -> tuple[dict[str, dict[str, Any]] | None, list[dict[str, Any]]]:
    """Apply a validated plan only to sandbox files with atomic replacements."""
    plan, blockers = validate_provider_write_plan(payload, context)
    if plan is None:
        return None, blockers

    staged: list[tuple[Path, Path]] = []
    try:
        for operation in plan.operations:
            target = sandbox_dir / operation.path
            original_mode = stat.S_IMODE(target.stat().st_mode)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=str(target.parent),
            ) as handle:
                handle.write(operation.content)
                os.fchmod(handle.fileno(), original_mode)
                handle.flush()
                os.fsync(handle.fileno())
                staged.append((Path(handle.name), target))

        revalidated, revalidation_blockers = validate_provider_write_plan(payload, context)
        if revalidated is None:
            return None, revalidation_blockers
        for temporary, target in staged:
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        return _file_manifest(sandbox_dir), []
    except OSError as exc:
        return None, [
            _provider_write_blocker(
                "provider-write-apply-failed",
                f"WorkflowEngine could not apply the provider write plan: {exc}",
            )
        ]
    finally:
        for temporary, _ in staged:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass


def _provider_write_result_evidence_envelope(
    plan: ProviderWritePlan,
    post_manifest: dict[str, dict[str, Any]],
    checkpoints: tuple[str, ...],
) -> dict[str, Any]:
    artifacts = [
        {"path": operation.path, "sha256": str(post_manifest[operation.path]["sha256"])}
        for operation in plan.operations
    ]
    paths = [str(item["path"]) for item in artifacts]
    return {
        "artifacts": artifacts,
        "checkpoint_evidence": {checkpoint: paths for checkpoint in checkpoints},
    }


def _provider_write_result_prompt(base_prompt: str, evidence_envelope: dict[str, Any]) -> str:
    return f"""{base_prompt}

--- PROVIDER WRITE PLAN RESULT PHASE ---
WorkflowEngine has validated and applied the prior provider-write-plan/v1 only
inside the sandbox. You still have no filesystem, shell, Git, promotion, or
gate access. Do not emit another write plan and do not request tools.

Provider result evidence envelope:
{json.dumps({"provider_result_evidence_envelope": evidence_envelope}, ensure_ascii=False, indent=2)}

Return exactly one strict fenced `role-result` JSON block. Copy the
`artifacts` and `checkpoint_evidence` values from
`provider_result_evidence_envelope` verbatim: do not add, omit, reorder, or
alter their entries. All other role-result/v1 validation rules in this prompt
remain in force.
"""


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _provider_write_blocker(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blocker: dict[str, Any] = {
        "category": "artifact",
        "code": code,
        "message": message,
        "repairable": False,
        "blocks_statuses": ["submission-ready"],
    }
    if details:
        blocker["details"] = details
    return blocker


def _sanitize_role_context(base_prompt: str) -> str:
    blocked_fragments = (
        "$thesis-workflow-orchestrator",
        "$academic-workflow-orchestrator",
        "use $thesis-",
        "use $academic-",
    )
    lines = [
        line
        for line in base_prompt.splitlines()
        if not any(fragment in line.casefold() for fragment in blocked_fragments)
    ]
    return "\n".join(lines).strip()


def _provider_write_base_prompt(base_prompt: str, root_dir: Path, sandbox_dir: Path) -> str:
    """Keep provider write-plan prompts free of engine-generated absolute paths."""
    sanitized = base_prompt
    for path in (root_dir, root_dir.resolve(), sandbox_dir, sandbox_dir.resolve()):
        sanitized = sanitized.replace(str(path), ".")
    return sanitized


def _role_context(
    *,
    workflow: WorkflowRun,
    node: RoleNode,
    contract: ExecutionContract,
    base_prompt: str,
    sandbox_dir: Path,
) -> str:
    if node.evaluator or node.role_id in VERIFIER_ROLE_IDS:
        return _read_only_role_context(
            workflow,
            contract,
            sandbox_dir,
            role_id=node.role_id,
            checkpoints=node.checkpoints,
        )
    return _sanitize_role_context(base_prompt)


def _read_only_role_context(
    workflow: WorkflowRun,
    contract: ExecutionContract,
    sandbox_dir: Path,
    *,
    role_id: str,
    checkpoints: tuple[str, ...],
) -> str:
    work_root = sandbox_dir / "works" / workflow.work_id
    artifacts = _file_manifest(work_root) if work_root.exists() else {}
    payload = {
        "workflow": {
            "workflow_id": workflow.workflow_id,
            "work_id": workflow.work_id,
            "lane": workflow.lane,
            "action": workflow.action,
        },
        "role": {
            "role_id": role_id,
            "read_only_provider_context": True,
        },
        "formal_contract": contract.to_dict(),
        "artifact_manifest": {f"works/{workflow.work_id}/{path}": record for path, record in artifacts.items()},
        "provider_result_evidence_envelope": _provider_result_evidence_envelope(
            work_id=workflow.work_id,
            artifacts=artifacts,
            checkpoints=checkpoints,
        ),
        "role_result_contract": {
            "fence_label": "role-result",
            "version": ROLE_RESULT_VERSION,
            "blocked_or_failed_when_evidence_is_insufficient": True,
        },
        "provider_limits": {
            "tool_access": "none",
            "filesystem_access": "none",
            "provider_visible_input_complete": True,
            "on_missing_evidence": "return structured blocked or failed role-result",
        },
        "machine_blockers": list(workflow.blockers),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).replace(
        str(Path(workflow.workflow_dir).parents[2]),
        str(sandbox_dir),
    )


def _provider_result_evidence_envelope(
    *,
    work_id: str,
    artifacts: dict[str, dict[str, Any]],
    checkpoints: tuple[str, ...],
) -> dict[str, Any] | None:
    if not artifacts:
        return None
    relative_path, record = next(iter(artifacts.items()))
    path = f"works/{work_id}/{relative_path}"
    return {
        "artifacts": [{"path": path, "sha256": str(record["sha256"])}],
        "checkpoint_evidence": {checkpoint: [path] for checkpoint in checkpoints},
    }


def _repair_no_change_evidence_envelope(
    *,
    root_dir: Path,
    sandbox_dir: Path,
    contract: ExecutionContract,
    work_id: str,
    artifacts: dict[str, dict[str, Any]],
    checkpoints: tuple[str, ...],
) -> dict[str, Any] | None:
    candidates = _managed_contract_evidence_candidates(
        root_dir=root_dir,
        sandbox_dir=sandbox_dir,
        contract=contract,
        work_id=work_id,
        artifacts=artifacts,
    )
    if not candidates:
        return None

    relative_path, _ = min(candidates.items(), key=lambda item: (item[1], item[0]))
    record = artifacts[relative_path]
    sandbox_path = f"works/{work_id}/{relative_path}"
    return {
        "artifacts": [{"path": sandbox_path, "sha256": str(record["sha256"])}],
        "checkpoint_evidence": {checkpoint: [sandbox_path] for checkpoint in checkpoints},
    }


def _managed_contract_evidence_candidates(
    *,
    root_dir: Path,
    sandbox_dir: Path,
    contract: ExecutionContract,
    work_id: str,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, tuple[int, int]]:
    candidates: dict[str, tuple[int, int]] = {}
    for item in (*contract.required_context, *contract.required_outputs):
        _add_contract_evidence_candidate(
            candidates,
            priority=_managed_contract_evidence_priority(item.name),
            declaration_priority=0,
            sandbox_relative_path=_sandbox_relative_contract_path(root_dir, sandbox_dir, item.path),
            work_id=work_id,
            artifacts=artifacts,
        )
    for item in contract.allowed_write_scopes:
        _add_contract_evidence_candidate(
            candidates,
            priority=_managed_contract_evidence_priority(item.name),
            declaration_priority=1,
            sandbox_relative_path=_sandbox_relative_contract_path(root_dir, sandbox_dir, item.path),
            work_id=work_id,
            artifacts=artifacts,
        )
    return candidates


def _add_contract_evidence_candidate(
    candidates: dict[str, tuple[int, int]],
    *,
    priority: int | None,
    declaration_priority: int,
    sandbox_relative_path: str | None,
    work_id: str,
    artifacts: dict[str, dict[str, Any]],
) -> None:
    if priority is None or sandbox_relative_path is None:
        return
    path = _work_relative_contract_path(sandbox_relative_path, work_id)
    if path is None or path not in artifacts:
        return
    candidate_priority = (priority, declaration_priority)
    previous = candidates.get(path)
    if previous is None or candidate_priority < previous:
        candidates[path] = candidate_priority


def _managed_contract_evidence_priority(name: str) -> int | None:
    tokens = set(name.casefold().replace("_", "-").split("-"))
    if tokens.intersection({"review", "reviews"}):
        return 0
    if tokens.intersection({"repair", "repairs"}):
        return 1
    if tokens.intersection({"checklist", "checklists"}):
        return 2
    return None


def _sandbox_relative_contract_path(root_dir: Path, sandbox_dir: Path, raw_path: str) -> str | None:
    sandbox_root = sandbox_dir.resolve()
    candidate = _sandbox_path(root_dir.resolve(), sandbox_root, raw_path).resolve()
    try:
        relative = candidate.relative_to(sandbox_root)
    except ValueError:
        return None
    if relative.parts and relative.parts[0] == "__outside_workspace__":
        return None
    return relative.as_posix()


def _work_relative_contract_path(sandbox_relative_path: str, work_id: str) -> str | None:
    prefix = f"works/{work_id}/"
    if not sandbox_relative_path.startswith(prefix):
        return None
    path = sandbox_relative_path[len(prefix) :]
    return path or None


def _source_provenance_summary(sandbox_dir: Path, work_id: str) -> dict[str, int]:
    summary = {"total": 0, "live": 0, "stub": 0, "invalid": 0}
    work_dir = sandbox_dir / "works" / work_id
    if not work_dir.exists():
        return summary
    for path in work_dir.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        _collect_source_provenance(payload, summary)
    return summary


def _collect_source_provenance(payload: object, summary: dict[str, int]) -> None:
    if isinstance(payload, list):
        for item in payload:
            _collect_source_provenance(item, summary)
        return
    if not isinstance(payload, dict):
        return

    source_kind = str(payload.get("kind") or "").strip()
    primary_kinds = {"statute", "case", "regulator-guidance", "statistics"}
    provenance = payload.get("provenance")
    source_record = source_kind in primary_kinds and isinstance(provenance, dict)
    flat_record = not source_kind and {"canonical_url", "retrieved_at", "content_hash", "http_status"}.issubset(payload)
    if source_record or flat_record:
        record = provenance if source_record else payload
        assert isinstance(record, dict)
        notes = str(record.get("notes") or "").strip().casefold()
        canonical_url = str(payload.get("canonical_url") or record.get("canonical_url") or "").strip()
        retrieved_at = str(record.get("retrieved_at") or "").strip()
        content_hash = str(payload.get("content_hash") or record.get("content_hash") or "").strip().casefold()
        http_status = record.get("http_status")
        summary["total"] += 1
        if notes == "stub-mode":
            summary["stub"] += 1
        elif (
            canonical_url.startswith(("https://", "http://"))
            and _valid_retrieval_timestamp(retrieved_at)
            and re.fullmatch(r"[0-9a-f]{64}", content_hash)
            and isinstance(http_status, int)
            and 200 <= http_status < 300
        ):
            summary["live"] += 1
        else:
            summary["invalid"] += 1
        if source_record:
            for key, value in payload.items():
                if key != "provenance":
                    _collect_source_provenance(value, summary)
            return
    for value in payload.values():
        _collect_source_provenance(value, summary)


def _valid_retrieval_timestamp(value: str) -> bool:
    if not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _readiness_from_verdict(verdict: dict[str, Any] | None) -> str:
    if not verdict:
        return "strong-draft-with-blockers"
    status = str(verdict.get("status") or "").strip()
    if status in {"submission-ready", "strong-draft", "ready-with-caveats", "reviewed", "updated"}:
        return status
    return "strong-draft-with-blockers"


def _downgrade_readiness(status: str) -> str:
    if status == "strong-draft-with-blockers":
        return status
    return "strong-draft-with-blockers"


def _latest_evaluator_verdict(runs: Iterable[RoleRun]) -> dict[str, Any] | None:
    result = None
    for role in runs:
        if role.role_id in {"thesis-submission-evaluator", "academic-submission-evaluator"} and role.verdict:
            result = role.verdict
    return result


def _repair_iteration_count(runs: Iterable[RoleRun]) -> int:
    iterations: set[str] = set()
    for role in runs:
        for checkpoint in role.checkpoints:
            if checkpoint.startswith("repair-") and ":" in checkpoint:
                iterations.add(checkpoint.split(":", 1)[0])
    return len(iterations)


def _latest_blockers(runs: Iterable[RoleRun], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evaluator_runs = [
        role for role in runs if role.role_id in {"thesis-submission-evaluator", "academic-submission-evaluator"}
    ]
    if evaluator_runs:
        return list(evaluator_runs[-1].blockers)
    return fallback


def _gate_blocker_categories(gate_id: str) -> set[str]:
    mapping = {
        "verified-support": {"verification", "primary-support", "citation"},
        "dynamic-material-refresh": {"dynamic-material", "verification"},
        "primary-support": {"primary-support", "verification"},
        "standards-consistency": {"standards", "standards-consistency"},
        "evaluator-verdict": {"verdict"},
    }
    return mapping.get(gate_id, set())


def _new_workflow_id(work_id: str, lane: str, action: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    safe = "-".join(_safe_token(item) for item in (work_id, lane, action))
    return f"{safe}-{stamp}-{suffix}"


def _safe_token(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in value).strip("-") or "run"


def _distribute(items: tuple[str, ...], count: int) -> tuple[tuple[str, ...], ...]:
    groups: list[list[str]] = [[] for _ in range(count)]
    for index, item in enumerate(items):
        groups[min(index * count // max(len(items), 1), count - 1)].append(item)
    return tuple(tuple(group) for group in groups)


def _file_manifest(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _ignored_path(path.relative_to(root)):
            continue
        relative = path.relative_to(root).as_posix()
        record = _file_record(path)
        if record is not None:
            result[relative] = record
    return result


def _canonical_manifest(root: Path, sandbox_manifest: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    return {path: _file_record(root / path) for path in sandbox_manifest}


def _file_record(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": size}


def _changed_paths(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _ignored_path(path: Path) -> bool:
    ignored_part = any(part in {".git", "__pycache__", ".pytest_cache", ".ruff_cache"} for part in path.parts)
    return ignored_part or path.suffix == ".pyc"


def _allowed_relative_scopes(root: Path, scopes: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    for scope in scopes:
        path = Path(scope.path)
        if not path.is_absolute():
            path = root / path
        try:
            result.append(path.resolve().relative_to(root).as_posix())
        except ValueError:
            continue
    return tuple(dict.fromkeys(result))


def _path_is_allowed(path: str, allowed: tuple[str, ...]) -> bool:
    candidate = Path(path)
    return any(candidate == Path(scope) or Path(scope) in candidate.parents for scope in allowed)


def _sandbox_path(root: Path, sandbox: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root)
        except ValueError:
            return sandbox / "__outside_workspace__"
    return sandbox / path


def _artifact_schema_valid(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return True
    if path.suffix.casefold() == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
    elif path.suffix.casefold() == ".toml":
        try:
            tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            return False
    elif path.suffix.casefold() in {".md", ".txt"}:
        try:
            return bool(path.read_text(encoding="utf-8").strip())
        except UnicodeDecodeError:
            return False
    return True


def _runtime_blocker(code: str, message: str) -> dict[str, Any]:
    return {
        "category": "runtime",
        "code": code,
        "message": message,
        "repairable": code not in {"workflow-exception", "write-scope-violation"},
        "blocks_statuses": ["submission-ready"],
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent)) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)
