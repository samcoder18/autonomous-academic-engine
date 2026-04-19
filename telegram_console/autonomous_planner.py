from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .autonomous_policy import AutonomousPolicyDecision, evaluate_autonomous_policy


@dataclass(frozen=True)
class AutonomousPlanStep:
    index: int
    command: str | None
    action_id: str | None
    policy: AutonomousPolicyDecision
    finalization_check: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "index": self.index,
            "command": self.command,
            "action_id": self.action_id,
            "policy": self.policy.to_dict(),
        }
        if self.finalization_check:
            payload["finalization_check"] = self.finalization_check
        return payload


@dataclass(frozen=True)
class AutonomousPlan:
    mode: str
    work_id: str | None
    status: str
    steps: tuple[AutonomousPlanStep, ...]
    stop_reason: str | None
    readiness_claim: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "autonomous-plan",
            "mode": self.mode,
            "work_id": self.work_id,
            "status": self.status,
            "steps": [item.to_dict() for item in self.steps],
            "stop_reason": self.stop_reason,
            "readiness_claim": self.readiness_claim,
        }


def build_autonomous_plan(
    *,
    work_state: dict[str, Any],
    mode: str,
    max_steps: int = 3,
) -> AutonomousPlan:
    candidates = _candidate_actions(work_state)
    allowed_steps: list[AutonomousPlanStep] = []
    confirmation_steps: list[AutonomousPlanStep] = []
    stop_reason: str | None = None
    for action in candidates:
        enriched_action = _enrich_action(work_state, action)
        decision = evaluate_autonomous_policy(work_state=work_state, action=enriched_action, mode=mode)
        if decision.decision == "blocked":
            stop_reason = decision.reason
            continue
        step = AutonomousPlanStep(
            index=0,
            command=enriched_action.get("command"),
            action_id=enriched_action.get("action_id"),
            policy=decision,
            finalization_check=enriched_action.get("finalization_check")
            if isinstance(enriched_action.get("finalization_check"), dict)
            else None,
        )
        if decision.decision == "allowed":
            allowed_steps.append(step)
        else:
            confirmation_steps.append(step)
        if len(allowed_steps) >= max(1, max_steps):
            break

    selected = allowed_steps[: max(1, max_steps)]
    if not selected and confirmation_steps:
        selected = confirmation_steps[:1]
        stop_reason = "operator-confirmation-required"
    steps = tuple(
        AutonomousPlanStep(
            index=index,
            command=step.command,
            action_id=step.action_id,
            policy=step.policy,
            finalization_check=step.finalization_check,
        )
        for index, step in enumerate(selected, start=1)
    )
    if not steps and stop_reason is None:
        stop_reason = "no-autonomous-actions"
    status = (
        "ready"
        if any(step.policy.decision == "allowed" for step in steps)
        else "blocked"
        if not steps
        else "needs-confirmation"
    )
    return AutonomousPlan(
        mode=mode,
        work_id=_optional_text(work_state.get("work_id")),
        status=status,
        steps=tuple(steps),
        stop_reason=stop_reason,
    )


def format_autonomous_plan(plan: AutonomousPlan) -> str:
    payload = plan.to_dict()
    wid = payload.get("work_id") or "n/a"
    mode = payload.get("mode")
    st = payload.get("status")
    lines = [
        f"Autonomous plan: work={wid} mode={mode} status={st}",
        "Readiness claim: none",
    ]
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    if not steps:
        lines.append(f"Stop reason: {payload.get('stop_reason') or 'none'}")
        return "\n".join(lines)
    for step in steps:
        policy = step.get("policy") if isinstance(step.get("policy"), dict) else {}
        lines.append(
            "Step "
            f"{step.get('index')}: {step.get('command') or 'n/a'} "
            f"(decision={policy.get('decision') or 'n/a'}, intent={policy.get('intent') or 'n/a'})"
        )
        reason = _optional_text(policy.get("reason"))
        if reason:
            lines.append(f"Reason: {reason}")
    if payload.get("stop_reason"):
        lines.append(f"Stop reason: {payload.get('stop_reason')}")
    return "\n".join(lines)


def _candidate_actions(work_state: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in ("suggested_next_action", "work_continuation_action"):
        action = work_state.get(key)
        if isinstance(action, dict):
            _append_unique(result, action)
    for action in work_state.get("next_actions") or []:
        if isinstance(action, dict):
            _append_unique(result, action)
    if not result:
        result.append(
            {
                "action_id": "work-status",
                "label": "Read work status",
                "command": "work-status",
                "intent": "work-status",
            }
        )
    return result


def _enrich_action(work_state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    payload = dict(action)
    if _optional_text(payload.get("intent")) != "export" or _optional_text(payload.get("lane")) != "article":
        return payload
    finalization_check = _article_finalization_check_from_state(work_state, payload)
    if finalization_check:
        payload["finalization_check"] = finalization_check
    return payload


def _article_finalization_check_from_state(work_state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any] | None:
    bundle = _article_bundle_for_action(work_state, action)
    if not bundle:
        return None
    blocked: list[str] = []
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    final_file = files.get("final") if isinstance(files.get("final"), dict) else {}
    if not final_file.get("exists"):
        blocked.append("final-markdown-missing")
    if not bundle.get("checklist_present"):
        blocked.append("checklist-missing")
    if not bundle.get("review_present"):
        blocked.append("review-missing")
    for blocker in _known_blockers(work_state):
        category = _optional_text(blocker.get("category"))
        if category == "contract-gate":
            gate_id = (
                _optional_text((blocker.get("details") or {}).get("gate_id"))
                if isinstance(blocker.get("details"), dict)
                else None
            )
            blocked.append(f"gate:{gate_id or 'contract-gate'}")
        elif category == "standards-consistency":
            blocked.append("standards-blockers")
        elif category == "primary-support":
            blocked.append("primary-support-blockers")
        elif category == "dynamic-material":
            blocked.append("dynamic-material-blockers")
        elif category in {"article", "runtime"}:
            blocked.append(f"{category}-blockers")
    status = "block" if blocked else "pass"
    return {
        "kind": "article-finalization-check",
        "status": status,
        "finalization_status": "blocked" if blocked else "export-ready",
        "effective_readiness_status": _optional_text(bundle.get("readiness_status")),
        "allowed_exports": [] if blocked else ["docx"],
        "blocked_reasons": list(dict.fromkeys(blocked)),
        "required_followups": ["Resolve blocking gates before export."] if blocked else [],
        "readiness_claim": "none",
    }


def _article_bundle_for_action(work_state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any] | None:
    target = _optional_text(action.get("target"))
    article = work_state.get("article") if isinstance(work_state.get("article"), dict) else {}
    for bundle in article.get("bundles") or []:
        if not isinstance(bundle, dict):
            continue
        if target is None:
            return bundle
        files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
        for payload in files.values():
            if isinstance(payload, dict) and _optional_text(payload.get("path")) == target:
                return bundle
    return None


def _known_blockers(work_state: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = work_state.get("known_blockers")
    return [item for item in blockers if isinstance(item, dict)] if isinstance(blockers, list) else []


def _append_unique(items: list[dict[str, Any]], action: dict[str, Any]) -> None:
    command = _optional_text(action.get("command"))
    if not command:
        return
    if any(existing.get("command") == command for existing in items):
        return
    items.append(dict(action))


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
