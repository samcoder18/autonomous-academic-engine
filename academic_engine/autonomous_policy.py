from __future__ import annotations

from dataclasses import dataclass
from typing import Any

AUTONOMOUS_MODES = ("off", "suggest", "assisted", "autonomous-safe", "autonomous-full")
POLICY_DECISIONS = ("allowed", "requires-confirmation", "blocked")
READ_ONLY_INTENTS = {"explain", "status", "standards-status", "runtime-status", "work-status"}
SAFE_AUTONOMOUS_INTENTS = {"review", "verify"}
CONFIRMATION_INTENTS = {
    "article",
    "draft",
    "export",
    "finalize",
    "finalize-checklist",
    "repair",
    "standards-refresh",
    "write-section",
}


@dataclass(frozen=True)
class AutonomousPolicyDecision:
    decision: str
    reason: str
    mode: str
    command: str | None
    safe_command: str | None
    intent: str | None
    lane: str | None
    target: str | None
    action_id: str | None
    blocking_categories: tuple[str, ...] = ()
    blocking_gate_ids: tuple[str, ...] = ()
    readiness_claim: str = "none"
    max_run_count: int | None = None
    cooldown_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision if self.decision in POLICY_DECISIONS else "blocked",
            "reason": self.reason,
            "mode": self.mode,
            "command": self.command,
            "safe_command": self.safe_command,
            "intent": self.intent,
            "lane": self.lane,
            "target": self.target,
            "action_id": self.action_id,
            "blocking_categories": list(self.blocking_categories),
            "blocking_gate_ids": list(self.blocking_gate_ids),
            "readiness_claim": self.readiness_claim,
            "max_run_count": self.max_run_count,
            "cooldown_seconds": self.cooldown_seconds,
        }


def evaluate_autonomous_policy(
    *,
    work_state: dict[str, Any],
    action: dict[str, Any] | None,
    mode: str,
    target_resolution: dict[str, Any] | None = None,
) -> AutonomousPolicyDecision:
    mode_text = _normalize_mode(mode)
    action_payload = action if isinstance(action, dict) else {}
    command = _optional_text(action_payload.get("command"))
    intent = _optional_text(action_payload.get("intent")) or _infer_intent(command)
    lane = _optional_text(action_payload.get("lane"))
    target = _optional_text(action_payload.get("target"))
    action_id = _optional_text(action_payload.get("action_id"))

    if mode_text == "off" and intent not in READ_ONLY_INTENTS:
        return _decision(
            "blocked",
            "Autonomous mode is off.",
            mode_text,
            command,
            intent,
            lane,
            target,
            action_id,
            blocking_categories=("autonomous-off",),
        )

    if _has_active_run(work_state):
        return _decision(
            "blocked",
            "A workflow run is already active for this work.",
            mode_text,
            command,
            intent,
            lane,
            target,
            action_id,
            blocking_categories=("active-run",),
        )

    if _is_noncanonical_target(target_resolution):
        return _decision(
            "blocked",
            "Autonomous execution requires a canonical normalized target.",
            mode_text,
            command,
            intent,
            lane,
            target,
            action_id,
            blocking_categories=("noncanonical-target",),
        )

    if intent in READ_ONLY_INTENTS:
        return _decision(
            "allowed", "Read-only autonomous check is allowed.", mode_text, command, intent, lane, target, action_id
        )

    runtime_blockers = _blocker_categories(work_state, category="runtime")
    if runtime_blockers:
        return _decision(
            "blocked",
            "Runtime blockers require operator review before autonomous execution.",
            mode_text,
            command,
            intent,
            lane,
            target,
            action_id,
            blocking_categories=("runtime",),
        )

    standards_blockers = _blocker_categories(work_state, category="standards-consistency")
    contract_gate_ids = _blocking_contract_gate_ids(work_state)
    if mode_text == "autonomous-full" and intent in {"export", "finalize", "finalize-checklist"}:
        if standards_blockers:
            return _decision(
                "blocked",
                "Standards blockers prevent autonomous export or finalization.",
                mode_text,
                command,
                intent,
                lane,
                target,
                action_id,
                blocking_categories=("standards-consistency",),
            )
        if contract_gate_ids:
            return _decision(
                "blocked",
                "Contract gates prevent autonomous export or finalization.",
                mode_text,
                command,
                intent,
                lane,
                target,
                action_id,
                blocking_categories=("contract-gate",),
                blocking_gate_ids=contract_gate_ids,
            )

    thesis_block_categories = _lane_blocker_categories(work_state, "thesis")
    if lane == "thesis" and intent in {"draft", "write-section", "article"}:
        if "dynamic-material" in thesis_block_categories:
            return _decision(
                "blocked",
                "Dynamic legal material must be verified before thesis drafting.",
                mode_text,
                command,
                intent,
                lane,
                target,
                action_id,
                blocking_categories=("dynamic-material",),
            )
        if thesis_block_categories == {"style"}:
            return _decision(
                "blocked",
                "Broad style-only thesis blockers are not safe autonomous repair triggers.",
                mode_text,
                command,
                intent,
                lane,
                target,
                action_id,
                blocking_categories=("style",),
            )

    if mode_text == "suggest":
        return _decision(
            "requires-confirmation",
            "Suggest mode explains the step but does not execute it.",
            mode_text,
            command,
            intent,
            lane,
            target,
            action_id,
        )

    if intent in SAFE_AUTONOMOUS_INTENTS:
        return _decision(
            "allowed", "Safe review/verify action is allowed.", mode_text, command, intent, lane, target, action_id
        )

    finalization_check = action_payload.get("finalization_check")
    if mode_text == "autonomous-full" and intent == "export" and _finalization_export_ready(finalization_check):
        return _decision(
            "allowed",
            "Deterministic finalization checks allow export.",
            mode_text,
            command,
            intent,
            lane,
            target,
            action_id,
        )
    if (
        mode_text == "autonomous-full"
        and intent in {"draft", "write-section", "article"}
        and not _has_workflow_blockers(work_state)
    ):
        return _decision(
            "allowed",
            "Full autonomous mode allows bounded drafting when blockers are clear.",
            mode_text,
            command,
            intent,
            lane,
            target,
            action_id,
        )

    if intent in CONFIRMATION_INTENTS or mode_text in {"assisted", "autonomous-safe", "autonomous-full"}:
        return _decision(
            "requires-confirmation",
            "This action requires operator confirmation under the current autonomous policy.",
            mode_text,
            command,
            intent,
            lane,
            target,
            action_id,
        )

    return _decision(
        "blocked",
        "Action is not recognized as safe for autonomous execution.",
        mode_text,
        command,
        intent,
        lane,
        target,
        action_id,
    )


def _decision(
    decision: str,
    reason: str,
    mode: str,
    command: str | None,
    intent: str | None,
    lane: str | None,
    target: str | None,
    action_id: str | None,
    *,
    blocking_categories: tuple[str, ...] = (),
    blocking_gate_ids: tuple[str, ...] = (),
) -> AutonomousPolicyDecision:
    return AutonomousPolicyDecision(
        decision=decision,
        reason=reason,
        mode=mode,
        command=command,
        safe_command=command if decision == "allowed" else None,
        intent=intent,
        lane=lane,
        target=target,
        action_id=action_id,
        blocking_categories=blocking_categories,
        blocking_gate_ids=blocking_gate_ids,
        max_run_count=1 if decision == "allowed" else None,
        cooldown_seconds=0 if decision == "allowed" else None,
    )


def _normalize_mode(mode: str) -> str:
    value = str(mode or "").strip()
    return value if value in AUTONOMOUS_MODES else "suggest"


def _has_active_run(work_state: dict[str, Any]) -> bool:
    runtime = work_state.get("runtime") if isinstance(work_state.get("runtime"), dict) else {}
    return isinstance(runtime.get("active_run"), dict)


def _is_noncanonical_target(target_resolution: dict[str, Any] | None) -> bool:
    if not isinstance(target_resolution, dict):
        return False
    warning_code = _optional_text(target_resolution.get("warning_code"))
    resolution_mode = _optional_text(target_resolution.get("resolution_mode"))
    return warning_code == "legacy-root-target" or resolution_mode == "legacy-root"


def _blocking_contract_gate_ids(work_state: dict[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for blocker in _known_blockers(work_state):
        details = blocker.get("details") if isinstance(blocker.get("details"), dict) else {}
        if blocker.get("category") != "contract-gate":
            continue
        if not details.get("blocks_export") and not details.get("blocks_submission_ready"):
            continue
        gate_id = _optional_text(details.get("gate_id"))
        if gate_id and gate_id not in result:
            result.append(gate_id)
    return tuple(result)


def _blocker_categories(work_state: dict[str, Any], *, category: str) -> set[str]:
    return {category for blocker in _known_blockers(work_state) if blocker.get("category") == category}


def _lane_blocker_categories(work_state: dict[str, Any], lane: str) -> set[str]:
    categories: set[str] = set()
    for blocker in _known_blockers(work_state):
        if blocker.get("lane") != lane:
            continue
        category = _optional_text(blocker.get("category"))
        if category:
            categories.add(category)
    return categories


def _has_workflow_blockers(work_state: dict[str, Any]) -> bool:
    for blocker in _known_blockers(work_state):
        if blocker.get("blocks_workflow"):
            return True
        if blocker.get("category") in {"runtime", "contract-gate", "primary-support", "dynamic-material"}:
            return True
    return False


def _known_blockers(work_state: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = work_state.get("known_blockers")
    return [item for item in blockers if isinstance(item, dict)] if isinstance(blockers, list) else []


def _finalization_export_ready(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return payload.get("status") == "pass" and payload.get("finalization_status") in {"export-ready", "exported"}


def _infer_intent(command: str | None) -> str | None:
    if not command:
        return None
    if command.startswith("work-status"):
        return "work-status"
    if command.startswith("runtime status"):
        return "runtime-status"
    if command.startswith("standards-status"):
        return "standards-status"
    if command.startswith("standards-refresh"):
        return "standards-refresh"
    if command.startswith("export-"):
        return "export"
    if " review" in command or "review-section" in command:
        return "review"
    if " verify" in command:
        return "verify"
    if " finalize" in command:
        return "finalize"
    if " write-section" in command or " article" in command:
        return "draft"
    if " repair" in command:
        return "repair"
    return None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
