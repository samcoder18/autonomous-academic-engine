from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .action_specs import ExecutionContract
from .repair_kernel import Blocker, determine_terminal_reason


THESIS_REPAIR_PLAN_KIND = "thesis-repair-plan"
VERIFY_CATEGORIES = {"citation", "dynamic-material", "primary-support", "source", "verification"}
REVIEW_CATEGORIES = {"logic", "review"}
SAFE_ACTION_ORDER = ("verify", "review-section")


@dataclass(frozen=True)
class ThesisSafeRepairAction:
    action: str
    command: str
    reason: str
    categories: tuple[str, ...]
    safety: str = "conservative"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "command": self.command,
            "reason": self.reason,
            "categories": list(self.categories),
            "safety": self.safety,
        }


@dataclass(frozen=True)
class ThesisRepairPlan:
    eligible: bool
    target: str | None
    repair_iteration: int
    max_iterations: int
    safe_repair_actions: tuple[ThesisSafeRepairAction, ...]
    blocked_reasons: tuple[str, ...]
    suggested_action: str | None
    suggested_command: str | None
    terminal_reason: str | None
    readiness_claim: str = "none"
    kind: str = THESIS_REPAIR_PLAN_KIND
    lane: str = "thesis"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "lane": self.lane,
            "eligible": self.eligible,
            "target": self.target,
            "repair_iteration": self.repair_iteration,
            "max_iterations": self.max_iterations,
            "safe_repair_actions": [item.to_dict() for item in self.safe_repair_actions],
            "blocked_reasons": list(self.blocked_reasons),
            "suggested_action": self.suggested_action,
            "suggested_command": self.suggested_command,
            "terminal_reason": self.terminal_reason,
            "readiness_claim": self.readiness_claim,
        }


def build_thesis_repair_plan(
    *,
    section_summary: dict[str, Any] | None,
    blockers: Iterable[Blocker | dict[str, Any]],
    contract: ExecutionContract | None,
    target: str | None,
    repair_iteration: int,
) -> ThesisRepairPlan:
    normalized = tuple(_coerce_blockers(blockers))
    target_text = _optional_text(target) or _optional_text((section_summary or {}).get("target"))
    current_iteration = max(0, repair_iteration)
    max_iterations = _max_iterations(contract)
    blocked_reasons: list[str] = []

    if not normalized:
        return _blocked_plan(
            target=target_text,
            repair_iteration=current_iteration,
            max_iterations=max_iterations,
            blocked_reasons=("no-blockers",),
            terminal_reason="ready",
        )

    if contract is None:
        return _blocked_plan(
            target=target_text,
            repair_iteration=current_iteration,
            max_iterations=max_iterations,
            blocked_reasons=("missing-execution-contract",),
            terminal_reason=determine_terminal_reason(normalized),
        )
    if contract.lane != "thesis":
        return _blocked_plan(
            target=target_text,
            repair_iteration=current_iteration,
            max_iterations=max_iterations,
            blocked_reasons=("non-thesis-contract",),
            terminal_reason=determine_terminal_reason(normalized),
        )
    if not contract.repair_policy.eligible:
        return _blocked_plan(
            target=target_text,
            repair_iteration=current_iteration,
            max_iterations=max_iterations,
            blocked_reasons=("repair-not-eligible",),
            terminal_reason=determine_terminal_reason(normalized),
        )
    if current_iteration >= contract.repair_policy.max_iterations:
        return _blocked_plan(
            target=target_text,
            repair_iteration=current_iteration,
            max_iterations=max_iterations,
            blocked_reasons=("repair-limit-reached",),
            terminal_reason="max-repair-iterations",
        )
    if not target_text:
        return _blocked_plan(
            target=None,
            repair_iteration=current_iteration,
            max_iterations=max_iterations,
            blocked_reasons=("missing-target-section",),
            terminal_reason=determine_terminal_reason(normalized),
        )

    actions = _safe_actions_for_blockers(normalized, target_text)
    if not actions:
        blocked_reasons.append("no-safe-thesis-repair-actions")
    if any(not blocker.repairable for blocker in normalized):
        blocked_reasons.append("unrepairable-blockers-present")

    suggested = actions[0] if actions else None
    return ThesisRepairPlan(
        eligible=bool(actions),
        target=target_text,
        repair_iteration=current_iteration,
        max_iterations=max_iterations,
        safe_repair_actions=actions,
        blocked_reasons=tuple(dict.fromkeys(blocked_reasons)),
        suggested_action=suggested.action if suggested else None,
        suggested_command=suggested.command if suggested else None,
        terminal_reason=None if actions else determine_terminal_reason(normalized),
    )


def _blocked_plan(
    *,
    target: str | None,
    repair_iteration: int,
    max_iterations: int,
    blocked_reasons: tuple[str, ...],
    terminal_reason: str,
) -> ThesisRepairPlan:
    return ThesisRepairPlan(
        eligible=False,
        target=target,
        repair_iteration=repair_iteration,
        max_iterations=max_iterations,
        safe_repair_actions=(),
        blocked_reasons=blocked_reasons,
        suggested_action=None,
        suggested_command=None,
        terminal_reason=terminal_reason,
    )


def _safe_actions_for_blockers(blockers: tuple[Blocker, ...], target: str) -> tuple[ThesisSafeRepairAction, ...]:
    categories_by_action: dict[str, list[str]] = {"verify": [], "review-section": []}
    for blocker in blockers:
        if not blocker.repairable:
            continue
        category = blocker.category
        if category in VERIFY_CATEGORIES:
            categories_by_action["verify"].append(category)
        elif category in REVIEW_CATEGORIES:
            categories_by_action["review-section"].append(category)

    actions: list[ThesisSafeRepairAction] = []
    for action in SAFE_ACTION_ORDER:
        categories = tuple(dict.fromkeys(categories_by_action[action]))
        if not categories:
            continue
        actions.append(
            ThesisSafeRepairAction(
                action=action,
                command=f"launch-thesis {action} {target}",
                reason=_action_reason(action, categories),
                categories=categories,
            )
        )
    return tuple(actions)


def _action_reason(action: str, categories: tuple[str, ...]) -> str:
    if action == "verify" and "dynamic-material" in categories:
        return "Dynamic legal material requires verification before drafting."
    if action == "verify":
        return "Source, citation, or primary-support blockers need a verification pass."
    return "Review or logic blockers should be scoped through a review-section pass."


def _max_iterations(contract: ExecutionContract | None) -> int:
    if contract is None:
        return 0
    return max(0, contract.repair_policy.max_iterations)


def _coerce_blockers(items: Iterable[Blocker | dict[str, Any]]) -> list[Blocker]:
    result: list[Blocker] = []
    for item in items:
        if isinstance(item, Blocker):
            result.append(item)
            continue
        if not isinstance(item, dict):
            continue
        raw_statuses = item.get("blocks_statuses")
        statuses = tuple(str(status).strip() for status in raw_statuses if str(status).strip()) if isinstance(raw_statuses, list | tuple) else ()
        details = item.get("details")
        result.append(
            Blocker(
                category=_optional_text(item.get("category")) or "review",
                code=_optional_text(item.get("code")) or "unknown-blocker",
                message=_optional_text(item.get("message")) or "Unknown thesis blocker.",
                repairable=bool(item.get("repairable", True)),
                blocks_statuses=statuses,
                details=details if isinstance(details, dict) else {},
            )
        )
    return result


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
