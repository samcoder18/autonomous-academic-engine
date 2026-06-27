from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .action_specs import AllowedWriteScope, ExecutionContract

RUNTIME_BLOCKER_CATEGORIES = {"artifact", "codex", "external", "process", "runtime"}
PRIMARY_SUPPORT_CATEGORIES = {"citation", "dynamic-material", "primary-support", "verification"}
SAFE_REPAIR_CATEGORIES = {"citation", "dynamic-material", "logic", "primary-support", "review", "verification"}
STANDARDS_BLOCKER_CATEGORIES = {"standards", "standards-consistency"}


@dataclass(frozen=True)
class Blocker:
    category: str
    code: str
    message: str
    repairable: bool = True
    blocks_statuses: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "category": self.category,
            "code": self.code,
            "message": self.message,
            "repairable": self.repairable,
        }
        if self.blocks_statuses:
            payload["blocks_statuses"] = list(self.blocks_statuses)
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class RepairDecision:
    action: str
    reason: str
    repair_iteration: int
    terminal_reason: str | None = None
    blocker_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "reason": self.reason,
            "repair_iteration": self.repair_iteration,
            "blocker_count": self.blocker_count,
        }
        if self.terminal_reason:
            payload["terminal_reason"] = self.terminal_reason
        return payload


@dataclass(frozen=True)
class RepairStep:
    blocker: Blocker
    assigned_role: str
    allowed_write_scopes: tuple[AllowedWriteScope, ...]
    rerun_gate: str
    stop_condition: str
    safe: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocker": self.blocker.to_dict(),
            "assigned_role": self.assigned_role,
            "allowed_write_scopes": [item.to_dict() for item in self.allowed_write_scopes],
            "rerun_gate": self.rerun_gate,
            "stop_condition": self.stop_condition,
            "safe": self.safe,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RepairPlan:
    lane: str
    action: str
    repair_iteration: int
    blockers: tuple[Blocker, ...]
    focus_areas: tuple[str, ...]
    safe_only: bool
    max_iterations: int
    steps: tuple[RepairStep, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "action": self.action,
            "repair_iteration": self.repair_iteration,
            "blockers": [item.to_dict() for item in self.blockers],
            "focus_areas": list(self.focus_areas),
            "safe_only": self.safe_only,
            "max_iterations": self.max_iterations,
            "steps": [item.to_dict() for item in self.steps],
        }


@dataclass(frozen=True)
class RepairOutcome:
    decisions: tuple[RepairDecision, ...]
    plans: tuple[RepairPlan, ...]
    repair_iteration: int
    terminal_reason: str
    remaining_blockers: tuple[Blocker, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions": [item.to_dict() for item in self.decisions],
            "plans": [item.to_dict() for item in self.plans],
            "repair_iteration": self.repair_iteration,
            "terminal_reason": self.terminal_reason,
            "remaining_blockers": [item.to_dict() for item in self.remaining_blockers],
        }


SOURCE_REPAIR_CATEGORIES = {"dynamic-material", "primary-support", "source", "verification"}
CITATION_REPAIR_CATEGORIES = {"citation"}
LOGIC_REPAIR_CATEGORIES = {"logic", "review"}
STANDARDS_REPAIR_CATEGORIES = {"standards", "standards-consistency"}
STYLE_REPAIR_CATEGORIES = {"style"}

SOURCE_SCOPE_NAMES = ("evidence", "ledger", "source", "sync", "requested-target", "canonical-target", "target")
CITATION_SCOPE_NAMES = ("draft", "final-markdown", "checklist", "review", "ledger", "requested-target", "target")
LOGIC_SCOPE_NAMES = ("draft", "final-markdown", "review", "checklist", "canonical-target", "requested-target", "target")
STANDARDS_SCOPE_NAMES = ("checklist", "final-markdown", "docx", "standards", "requested-target", "target")
STYLE_SCOPE_NAMES = ("draft", "final-markdown", "canonical-target", "full-draft", "requested-target", "target")


@dataclass(frozen=True)
class _RepairRoute:
    assigned_role: str
    rerun_gate: str
    preferred_scope_names: tuple[str, ...]
    stop_condition: str
    reason: str
    safe: bool = True


def _route_for_blocker(*, lane: str, category: str) -> _RepairRoute | None:
    if category in RUNTIME_BLOCKER_CATEGORIES:
        return None
    if category in SOURCE_REPAIR_CATEGORIES:
        return _RepairRoute(
            assigned_role="thesis-source-verifier" if lane == "thesis" else "academic-source-verifier",
            rerun_gate="source-verification",
            preferred_scope_names=SOURCE_SCOPE_NAMES,
            stop_condition="blocker-cleared-or-reroute-required",
            reason="Primary support blockers require verifier evidence before drafting or finalization.",
        )
    if category in CITATION_REPAIR_CATEGORIES:
        return _RepairRoute(
            assigned_role="thesis-citation-checker" if lane == "thesis" else "academic-citation-checker",
            rerun_gate="citation-check",
            preferred_scope_names=CITATION_SCOPE_NAMES,
            stop_condition="blocker-cleared-or-reroute-required",
            reason="Citation blockers require a citation check before evaluator promotion.",
        )
    if category in LOGIC_REPAIR_CATEGORIES:
        return _RepairRoute(
            assigned_role="thesis-argument-critic" if lane == "thesis" else "academic-counterargument-critic",
            rerun_gate="argument-review",
            preferred_scope_names=LOGIC_SCOPE_NAMES,
            stop_condition="blocker-cleared-or-reroute-required",
            reason="Logic and review blockers require a critic pass before evaluator promotion.",
        )
    if category in STANDARDS_REPAIR_CATEGORIES:
        return _RepairRoute(
            assigned_role="thesis-submission-evaluator" if lane == "thesis" else "academic-finalizer",
            rerun_gate="standards-check",
            preferred_scope_names=STANDARDS_SCOPE_NAMES,
            stop_condition="blocker-cleared-or-downgrade-preserved",
            reason="Standards blockers must remain visible until the checklist or gate confirms resolution.",
        )
    if category in STYLE_REPAIR_CATEGORIES:
        return _RepairRoute(
            assigned_role="thesis-style-editor" if lane == "thesis" else "academic-draft-writer",
            rerun_gate="style-review",
            preferred_scope_names=STYLE_SCOPE_NAMES,
            stop_condition="blocker-cleared-or-reroute-required",
            reason="Style blockers may be repaired only when the contract allows non-safe repair.",
            safe=False,
        )
    return None


def _build_repair_steps(*, contract: ExecutionContract, blockers: tuple[Blocker, ...]) -> tuple[RepairStep, ...]:
    steps: list[RepairStep] = []
    for blocker in blockers:
        route = _route_for_blocker(lane=contract.lane, category=blocker.category)
        if route is None:
            continue
        scopes = _matching_write_scopes(contract.allowed_write_scopes, route.preferred_scope_names)
        if not scopes:
            continue
        steps.append(
            RepairStep(
                blocker=blocker,
                assigned_role=route.assigned_role,
                allowed_write_scopes=scopes,
                rerun_gate=route.rerun_gate,
                stop_condition=route.stop_condition,
                safe=route.safe,
                reason=route.reason,
            )
        )
    return tuple(steps)


def _matching_write_scopes(
    scopes: tuple[AllowedWriteScope, ...],
    preferred_names: tuple[str, ...],
) -> tuple[AllowedWriteScope, ...]:
    matches: list[AllowedWriteScope] = []
    for scope in scopes:
        name = scope.name.lower()
        if any(preferred in name for preferred in preferred_names):
            matches.append(scope)
    return tuple(matches)


def build_repair_plan(
    *,
    contract: ExecutionContract,
    blockers: Iterable[Blocker | dict[str, Any]],
    repair_iteration: int,
) -> RepairPlan:
    normalized = tuple(_coerce_blockers(blockers))
    repairable = tuple(item for item in normalized if item.repairable)
    if contract.repair_policy.safe_only:
        repairable = tuple(item for item in repairable if item.category in SAFE_REPAIR_CATEGORIES)
    focus_areas = tuple(dict.fromkeys(item.category for item in repairable))
    steps = _build_repair_steps(contract=contract, blockers=repairable)
    return RepairPlan(
        lane=contract.lane,
        action=contract.action,
        repair_iteration=repair_iteration,
        blockers=repairable,
        focus_areas=focus_areas,
        safe_only=contract.repair_policy.safe_only,
        max_iterations=contract.repair_policy.max_iterations,
        steps=steps,
    )


def build_repair_decision(
    *,
    contract: ExecutionContract,
    blockers: Iterable[Blocker | dict[str, Any]],
    repair_iteration: int,
) -> RepairDecision:
    normalized = tuple(_coerce_blockers(blockers))
    if not normalized:
        return RepairDecision(
            action="stop",
            reason="blockers-cleared",
            repair_iteration=repair_iteration,
            terminal_reason="ready",
            blocker_count=0,
        )
    if not contract.repair_policy.eligible:
        return RepairDecision(
            action="stop",
            reason="repair-not-eligible",
            repair_iteration=repair_iteration,
            terminal_reason=determine_terminal_reason(normalized),
            blocker_count=len(normalized),
        )
    if repair_iteration >= contract.repair_policy.max_iterations:
        return RepairDecision(
            action="stop",
            reason="repair-limit-reached",
            repair_iteration=repair_iteration,
            terminal_reason="max-repair-iterations",
            blocker_count=len(normalized),
        )

    plan = build_repair_plan(contract=contract, blockers=normalized, repair_iteration=repair_iteration + 1)
    if not plan.steps:
        return RepairDecision(
            action="stop",
            reason="no-safe-repair-steps",
            repair_iteration=repair_iteration,
            terminal_reason=determine_terminal_reason(normalized),
            blocker_count=len(normalized),
        )
    return RepairDecision(
        action="repair",
        reason="repairable-blockers-available",
        repair_iteration=plan.repair_iteration,
        blocker_count=len(plan.blockers),
    )


def determine_terminal_reason(blockers: Iterable[Blocker | dict[str, Any]]) -> str:
    normalized = tuple(_coerce_blockers(blockers))
    if not normalized:
        return "ready"
    categories = {item.category for item in normalized}
    if categories & RUNTIME_BLOCKER_CATEGORIES:
        return "blocked-runtime"
    if categories & STANDARDS_BLOCKER_CATEGORIES:
        return "blocked-standards"
    if categories & PRIMARY_SUPPORT_CATEGORIES:
        return "blocked-primary-support"
    return "ready-with-caveats"


def run_bounded_repair_loop(
    *,
    contract: ExecutionContract,
    initial_blockers: Iterable[Blocker | dict[str, Any]],
    repair_fn: Callable[[RepairPlan], Any],
    evaluate_fn: Callable[[RepairPlan, Any], Iterable[Blocker | dict[str, Any]]],
) -> RepairOutcome:
    current_blockers = tuple(_coerce_blockers(initial_blockers))
    repair_iteration = 0
    decisions: list[RepairDecision] = []
    plans: list[RepairPlan] = []

    while True:
        decision = build_repair_decision(
            contract=contract,
            blockers=current_blockers,
            repair_iteration=repair_iteration,
        )
        decisions.append(decision)
        if decision.action != "repair":
            return RepairOutcome(
                decisions=tuple(decisions),
                plans=tuple(plans),
                repair_iteration=repair_iteration,
                terminal_reason=decision.terminal_reason or determine_terminal_reason(current_blockers),
                remaining_blockers=current_blockers,
            )

        plan = build_repair_plan(
            contract=contract,
            blockers=current_blockers,
            repair_iteration=decision.repair_iteration,
        )
        plans.append(plan)
        repair_result = repair_fn(plan)
        current_blockers = tuple(_coerce_blockers(evaluate_fn(plan, repair_result)))
        repair_iteration = plan.repair_iteration


def _coerce_blockers(items: Iterable[Blocker | dict[str, Any]]) -> list[Blocker]:
    result: list[Blocker] = []
    for item in items:
        if isinstance(item, Blocker):
            result.append(item)
            continue
        if isinstance(item, dict):
            category = str(item.get("category") or "").strip() or "review"
            code = str(item.get("code") or "").strip() or "unknown-blocker"
            message = str(item.get("message") or "").strip() or code
            repairable = bool(item.get("repairable", True))
            raw_statuses = item.get("blocks_statuses")
            if isinstance(raw_statuses, list | tuple):
                statuses = tuple(str(status).strip() for status in raw_statuses if str(status).strip())
            else:
                statuses = ()
            details = item.get("details")
            result.append(
                Blocker(
                    category=category,
                    code=code,
                    message=message,
                    repairable=repairable,
                    blocks_statuses=statuses,
                    details=details if isinstance(details, dict) else {},
                )
            )
    return result
