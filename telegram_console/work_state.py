from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .contract_gates import blocking_gate_blockers


WORK_STATE_VERSION = "v1"


@dataclass(frozen=True)
class WorkNextAction:
    action_id: str
    label: str
    command: str
    reason: str
    priority: int
    lane: str | None = None
    target: str | None = None
    profile_id: str | None = None
    safety: str = "conservative"
    blocks_export: bool = False
    blocks_workflow: bool = False
    blocking_scope: tuple[str, ...] = ()
    intent: str | None = None
    fallback_for: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "label": self.label,
            "command": self.command,
            "reason": self.reason,
            "priority": self.priority,
            "lane": self.lane,
            "target": self.target,
            "profile_id": self.profile_id,
            "safety": self.safety,
            "blocks_export": self.blocks_export,
            "blocks_workflow": self.blocks_workflow,
            "blocking_scope": list(self.blocking_scope),
            "intent": self.intent,
            "fallback_for": self.fallback_for,
        }


def build_work_state(
    *,
    root_dir: str | Path,
    work_id: str,
    work_title: str,
    active_lanes: Iterable[str],
    thesis_overview: dict[str, Any] | None,
    article_overview: dict[str, Any] | None,
    standards_profiles: dict[str, Any],
    runtime_records: Iterable[Any],
    active_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root_path = Path(root_dir).resolve()
    lane_list = [lane for lane in (_optional_text(item) for item in active_lanes) if lane]
    thesis = _compact_thesis_state(root_path, thesis_overview)
    article = _compact_article_state(root_path, article_overview)
    standards = _compact_standards_state(standards_profiles)
    runtime = _compact_runtime_state(runtime_records, active_run)

    known_blockers = _dedupe_blockers(
        [
            *thesis["blockers"],
            *article["blockers"],
            *standards["blockers"],
            *runtime["blockers"],
        ]
    )
    next_actions = resolve_next_actions(
        thesis=thesis,
        article=article,
        standards=standards,
        runtime=runtime,
        known_blockers=known_blockers,
    )
    next_action_payloads = [item.to_dict() for item in next_actions]
    suggested_next_action = next_action_payloads[0] if next_action_payloads else None
    work_continuation_action = _first_work_continuation_action(next_action_payloads)

    return {
        "version": WORK_STATE_VERSION,
        "kind": "work-state",
        "work_id": work_id,
        "work_title": work_title,
        "assessment_scope": _assessment_scope(),
        "active_lanes": lane_list,
        "thesis": _strip_internal(thesis),
        "article": _strip_internal(article),
        "standards": _strip_internal(standards),
        "runtime": _strip_internal(runtime),
        "known_blockers": known_blockers,
        "known_blocker_count": len(known_blockers),
        "next_actions": next_action_payloads,
        "suggested_next_action": suggested_next_action,
        "work_continuation_action": work_continuation_action,
    }


def resolve_next_actions(
    *,
    thesis: dict[str, Any],
    article: dict[str, Any],
    standards: dict[str, Any],
    runtime: dict[str, Any],
    known_blockers: list[dict[str, Any]],
) -> tuple[WorkNextAction, ...]:
    actions: list[WorkNextAction] = []
    export_gate_active = _has_export_gate_candidate(article, thesis)
    standards_priority = 10 if export_gate_active else 95

    active_run = runtime.get("active_run")
    if isinstance(active_run, dict):
        actions.append(
            WorkNextAction(
                action_id="wait-active-run",
                label="Wait for active run",
                command="runtime status",
                reason="A workflow run is already active for this work.",
                priority=0,
                lane=_optional_text(active_run.get("lane")),
                safety="wait",
                blocks_export=True,
                blocks_workflow=True,
                blocking_scope=("new-run",),
                intent="wait",
            )
        )

    raw_blocker = _first_blocker(known_blockers, category="standards-consistency", code_contains="raw")
    if raw_blocker is not None:
        profile_id = _optional_text(raw_blocker.get("profile_id")) or _optional_text((raw_blocker.get("details") or {}).get("profile_id"))
        actions.append(
            WorkNextAction(
                action_id="standards-refresh",
                label="Refresh standards raw bundle",
                command=f"standards-refresh {profile_id or '<profile-id>'}",
                reason=_optional_text(raw_blocker.get("message")) or "Raw standards bundle is missing or partial.",
                priority=standards_priority,
                lane=_optional_text(raw_blocker.get("lane")),
                profile_id=profile_id,
                blocks_export=True,
                blocking_scope=("export", "submission-ready", "formal-compliance"),
                intent="standards-refresh",
            )
        )

    conflict_blocker = _first_blocker(known_blockers, category="standards-consistency", code_contains="conflict")
    if conflict_blocker is not None:
        profile_id = _optional_text(conflict_blocker.get("profile_id")) or _optional_text((conflict_blocker.get("details") or {}).get("profile_id"))
        actions.append(
            WorkNextAction(
                action_id="standards-review",
                label="Review standards conflict",
                command=f"standards-status {profile_id or '<profile-id>'}",
                reason=_optional_text(conflict_blocker.get("message")) or "Standards profile has a visible conflict flag.",
                priority=standards_priority + 1,
                lane=_optional_text(conflict_blocker.get("lane")),
                profile_id=profile_id,
                blocks_export=True,
                blocking_scope=("export", "submission-ready", "formal-compliance"),
                intent="standards-review",
            )
        )

    article_blocker = _first_lane_blocker(known_blockers, "article", exclude_categories={"standards-consistency"})
    if article_blocker is not None:
        target = _optional_text(article_blocker.get("target")) or _first_article_repair_target(article)
        actions.append(
            WorkNextAction(
                action_id="article-repair",
                label="Repair article blockers",
                command=f"launch-academic repair {target or '<article-draft-or-final>'}",
                reason=_optional_text(article_blocker.get("message")) or "Article blockers should be repaired before export.",
                priority=30,
                lane="article",
                target=target,
                blocks_export=True,
                blocks_workflow=True,
                blocking_scope=("work-continuation", "export", "submission-ready"),
                intent="repair",
            )
        )

    thesis_blocker = _first_lane_blocker(known_blockers, "thesis", exclude_categories={"standards-consistency"})
    if thesis_blocker is not None:
        target = _optional_text(thesis_blocker.get("target")) or _first_thesis_target(thesis)
        action = _optional_text((thesis_blocker.get("details") or {}).get("suggested_next_action")) or "verify"
        if action not in {"verify", "review-section", "style-pass", "write-section", "full-cycle", "source-pack"}:
            action = "verify"
        actions.append(
            WorkNextAction(
                action_id="thesis-verify",
                label="Verify thesis blockers",
                command=f"launch-thesis {action} {target or '<thesis-section>'}",
                reason=_optional_text(thesis_blocker.get("message")) or "Thesis blockers should be verified before export.",
                priority=35,
                lane="thesis",
                target=target,
                blocks_export=True,
                blocks_workflow=True,
                blocking_scope=("work-continuation", "export"),
                intent="verify",
            )
        )

    runtime_blocker = _first_blocker(known_blockers, category="runtime")
    if runtime_blocker is not None:
        actions.append(
            WorkNextAction(
                action_id="runtime-review",
                label="Review runtime failure",
                command="runtime status",
                reason=_optional_text(runtime_blocker.get("message")) or "Runtime blocker needs operator review.",
                priority=40,
                lane=_optional_text(runtime_blocker.get("lane")),
                blocks_export=True,
                blocks_workflow=True,
                blocking_scope=("work-continuation", "export"),
                intent="runtime-review",
            )
        )

    if not _has_workflow_blocking_action(actions):
        review_bundle = _first_article_bundle_needing_review(article)
        if review_bundle is not None:
            target = _article_bundle_target(review_bundle, preferred=("draft", "final", "brief"))
            actions.append(
                WorkNextAction(
                    action_id="article-review",
                    label="Review article bundle",
                    command=f"launch-academic review {target or '<article-draft-or-final>'}",
                    reason=f"Article bundle `{review_bundle.get('slug')}` has no managed review yet.",
                    priority=50,
                    lane="article",
                    target=target,
                    intent="review",
                )
            )

        checklist_bundle = _first_article_bundle_missing_checklist(article)
        if checklist_bundle is not None:
            target = _article_bundle_target(checklist_bundle, preferred=("final", "draft"))
            actions.append(
                WorkNextAction(
                    action_id="article-finalize",
                    label="Finalize article checklist",
                    command=f"launch-academic finalize {target or '<article-final-or-draft>'}",
                    reason=f"Article bundle `{checklist_bundle.get('slug')}` has final text but no checklist.",
                    priority=55,
                    lane="article",
                    target=target,
                    intent="finalize-checklist",
                )
            )

        unreviewed_section = _first_unreviewed_thesis_section(thesis)
        if unreviewed_section is not None:
            target = _optional_text(unreviewed_section.get("target"))
            actions.append(
                WorkNextAction(
                    action_id="thesis-review-section",
                    label="Review thesis section",
                    command=f"launch-thesis review-section {target or '<thesis-section>'}",
                    reason=f"Thesis section `{target or 'n/a'}` has no review artifact yet.",
                    priority=60,
                    lane="thesis",
                    target=target,
                    intent="review",
                )
            )

        if not any(_is_work_continuation_action(item) for item in actions) and (actions or not export_gate_active):
            actions.append(
                WorkNextAction(
                    action_id="draft-next",
                    label="Draft next artifact",
                    command="launch-thesis write-section <section> or launch-academic article --topic <topic>",
                    reason="No managed artifacts are ready for review or export yet.",
                    priority=90,
                    intent="draft",
                )
            )

    if not actions:
        article_export = _first_article_export_target(article)
        if article_export:
            actions.append(
                WorkNextAction(
                    action_id="export-article-docx",
                    label="Export article DOCX",
                    command=f"export-article-docx {article_export}",
                    reason="Article bundle has final markdown and checklist with no known blockers.",
                    priority=80,
                    lane="article",
                    target=article_export,
                    intent="export",
                )
            )
        elif _thesis_ready_for_export(thesis):
            actions.append(
                WorkNextAction(
                    action_id="export-thesis-docx",
                    label="Export thesis DOCX",
                    command="export-thesis-docx",
                    reason="Thesis sections are reviewed and no known blockers are visible.",
                    priority=85,
                    lane="thesis",
                    intent="export",
                )
            )

    deduped: dict[tuple[str, str | None, str | None], WorkNextAction] = {}
    for action in actions:
        key = (action.action_id, action.lane, action.target or action.profile_id)
        existing = deduped.get(key)
        if existing is None or action.priority < existing.priority:
            deduped[key] = action
    return tuple(sorted(deduped.values(), key=lambda item: (item.priority, item.action_id)))


def format_work_state_summary(state: dict[str, Any]) -> str:
    thesis_summary = state.get("thesis", {}).get("summary", {})
    article_summary = state.get("article", {}).get("summary", {})
    standards_profiles = state.get("standards", {}).get("profiles", {})
    runtime = state.get("runtime", {})
    next_action = state.get("suggested_next_action")
    continuation_action = state.get("work_continuation_action")

    lines = [
        f"Work status: {state.get('work_title') or 'n/a'} (`{state.get('work_id') or 'n/a'}`)",
        "Scope: signals-only; not source verification or repair planning",
        f"Lanes: {', '.join(state.get('active_lanes') or []) or 'none'}",
        (
            "Thesis: "
            f"sections={thesis_summary.get('section_count') or 0}, "
            f"reviewed={thesis_summary.get('reviewed_count') or 0}, "
            f"blockers={thesis_summary.get('blocked_count') or 0}"
        ),
        (
            "Articles: "
            f"bundles={article_summary.get('bundle_count') or 0}, "
            f"review_missing={article_summary.get('review_missing_count') or 0}, "
            f"blockers={article_summary.get('blocked_count') or 0}"
        ),
        f"Known blockers: {state.get('known_blocker_count') or 0}",
    ]
    if isinstance(standards_profiles, dict) and standards_profiles:
        standards_parts = []
        for lane, payload in sorted(standards_profiles.items()):
            if not isinstance(payload, dict):
                continue
            profile = payload.get("profile_id") or "n/a"
            raw = payload.get("raw_status") or "n/a"
            conflict = "yes" if payload.get("conflict_flag") else "no"
            standards_parts.append(f"{lane}={profile} raw={raw} conflict={conflict}")
        if standards_parts:
            lines.append("Standards: " + "; ".join(standards_parts))
    recent = runtime.get("recent") if isinstance(runtime, dict) else None
    lines.append(f"Recent runtime: {len(recent) if isinstance(recent, list) else 0}")
    gate_summary = _runtime_contract_gate_summary(runtime)
    if gate_summary["total_count"]:
        lines.append(f"Contract gates: blocks={gate_summary['block_count']} warnings={gate_summary['warn_count']}")
    thesis_repair_plan = _latest_thesis_repair_plan(runtime)
    if thesis_repair_plan is not None:
        plan_command = _optional_text(thesis_repair_plan.get("suggested_command"))
        plan_status = "eligible" if thesis_repair_plan.get("eligible") else "blocked"
        lines.append(f"Thesis repair plan: {plan_command or plan_status}")
    if isinstance(next_action, dict):
        lines.append(f"Next safe action: {next_action.get('command') or next_action.get('label')}")
        reason = _optional_text(next_action.get("reason"))
        if reason:
            lines.append(f"Reason: {reason}")
    else:
        lines.append("Next safe action: none")
    if isinstance(continuation_action, dict):
        next_command = next_action.get("command") if isinstance(next_action, dict) else None
        continuation_command = continuation_action.get("command")
        if continuation_command and continuation_command != next_command:
            lines.append(f"Unblocked work action: {continuation_command}")
    return "\n".join(lines)


def _latest_thesis_repair_plan(runtime: dict[str, Any]) -> dict[str, Any] | None:
    recent = runtime.get("recent") if isinstance(runtime, dict) else None
    if not isinstance(recent, list):
        return None
    for item in recent:
        if not isinstance(item, dict) or item.get("lane") != "thesis":
            continue
        plan = item.get("thesis_repair_plan")
        if isinstance(plan, dict):
            return plan
    return None


def format_work_state_dashboard_lines(state: dict[str, Any]) -> list[str]:
    thesis_summary = state.get("thesis", {}).get("summary", {})
    article_summary = state.get("article", {}).get("summary", {})
    next_action = state.get("suggested_next_action")
    continuation_action = state.get("work_continuation_action")
    lines = [
        (
            "Work status: "
            f"thesis {thesis_summary.get('reviewed_count') or 0}/{thesis_summary.get('section_count') or 0} reviewed, "
            f"articles {article_summary.get('bundle_count') or 0}, "
            f"blockers {state.get('known_blocker_count') or 0}"
        )
    ]
    if isinstance(next_action, dict):
        lines.append(f"Что дальше: {next_action.get('command') or next_action.get('label')}")
    else:
        lines.append("Что дальше: нет безопасного автоматического шага")
    if isinstance(continuation_action, dict):
        next_command = next_action.get("command") if isinstance(next_action, dict) else None
        continuation_command = continuation_action.get("command")
        if continuation_command and continuation_command != next_command:
            lines.append(f"Можно параллельно: {continuation_command}")
    return lines


def _compact_thesis_state(root_dir: Path, overview: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(overview, dict):
        return {
            "available": False,
            "sections": [],
            "summary": {"kind": "thesis-overview-summary", "section_count": 0, "reviewed_count": 0, "blocked_count": 0},
            "blockers": [],
        }
    sections: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for item in overview.get("sections") or []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        target = _compact_path(root_dir, _optional_text(item.get("target")) or _optional_text(summary.get("target")))
        blocker_count = _optional_int(summary.get("blocker_count")) or 0
        section = {
            "target": target,
            "review_path": _compact_path(root_dir, _optional_text(item.get("review_path"))),
            "review_exists": bool(item.get("review_exists")),
            "last_run_action": _optional_text(summary.get("last_run_action")),
            "last_run_status": _optional_text(summary.get("last_run_status")),
            "blocker_count": blocker_count,
            "terminal_reason": _optional_text(summary.get("terminal_reason")),
            "suggested_next_action": _optional_text(summary.get("suggested_next_action")),
        }
        sections.append(section)
        if blocker_count:
            blockers.append(
                {
                    "category": "thesis",
                    "code": "thesis-section-blocked",
                    "message": f"Thesis section `{target or 'n/a'}` has {blocker_count} known blocker(s).",
                    "repairable": True,
                    "lane": "thesis",
                    "target": target,
                    "details": {
                        "terminal_reason": section["terminal_reason"],
                        "suggested_next_action": section["suggested_next_action"],
                    },
                }
            )
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    return {
        "available": True,
        "sections": sections,
        "summary": {
            "kind": "thesis-overview-summary",
            "section_count": _optional_int(summary.get("section_count")) or len(sections),
            "reviewed_count": _optional_int(summary.get("reviewed_count")) or sum(1 for item in sections if item["review_exists"]),
            "blocked_count": _optional_int(summary.get("blocked_count")) or sum(1 for item in sections if item["blocker_count"]),
            "suggested_next_action": _optional_text(summary.get("suggested_next_action")),
        },
        "blockers": blockers,
    }


def _compact_article_state(root_dir: Path, overview: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(overview, dict):
        return {
            "available": False,
            "bundles": [],
            "summary": {"kind": "article-overview-summary", "bundle_count": 0, "blocked_count": 0, "review_missing_count": 0},
            "blockers": [],
        }
    bundles: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for item in overview.get("bundles") or []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        files = _compact_article_files(root_dir, item.get("files"))
        slug = _optional_text(item.get("slug")) or _optional_text(summary.get("slug")) or "unknown"
        blocker_count = _optional_int(summary.get("blocker_count")) or 0
        bundle = {
            "slug": slug,
            "current_phase": _optional_text(summary.get("current_phase")),
            "current_status": _optional_text(summary.get("current_status")),
            "readiness_status": _optional_text(summary.get("readiness_status")),
            "blocker_count": blocker_count,
            "review_present": bool(summary.get("review_present")),
            "checklist_present": bool(summary.get("checklist_present")),
            "repair_action": _optional_text(summary.get("repair_action")),
            "repair_iteration": _optional_int(summary.get("repair_iteration")),
            "suggested_next_action": _optional_text(summary.get("suggested_next_action")),
            "files": files,
        }
        bundles.append(bundle)
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        state_blockers = state.get("blockers") if isinstance(state.get("blockers"), list) else []
        for raw_blocker in state_blockers:
            if not isinstance(raw_blocker, dict):
                continue
            blockers.append(_enrich_blocker(raw_blocker, lane="article", article_slug=slug, target=_article_bundle_target(bundle)))
        if blocker_count and not state_blockers:
            blockers.append(
                {
                    "category": "article",
                    "code": "article-bundle-blocked",
                    "message": f"Article bundle `{slug}` has {blocker_count} known blocker(s).",
                    "repairable": True,
                    "lane": "article",
                    "article_slug": slug,
                    "target": _article_bundle_target(bundle),
                }
            )
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    return {
        "available": True,
        "bundles": bundles,
        "summary": {
            "kind": "article-overview-summary",
            "bundle_count": _optional_int(summary.get("bundle_count")) or len(bundles),
            "blocked_count": _optional_int(summary.get("blocked_count")) or sum(1 for item in bundles if item["blocker_count"]),
            "submission_ready_count": _optional_int(summary.get("submission_ready_count")) or 0,
            "review_missing_count": _optional_int(summary.get("review_missing_count"))
            or sum(1 for item in bundles if not item["review_present"]),
            "suggested_next_action": _optional_text(summary.get("suggested_next_action")),
        },
        "blockers": blockers,
    }


def _compact_standards_state(profiles: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, dict[str, Any]] = {}
    blockers: list[dict[str, Any]] = []
    for lane, profile in sorted(profiles.items()):
        lane_text = str(lane).strip()
        payload = _standard_profile_payload(lane_text, profile)
        result[lane_text] = payload
        error = _optional_text(payload.get("error"))
        if error:
            blockers.append(
                {
                    "category": "standards-consistency",
                    "code": f"{lane_text}-standards-resolution-error",
                    "message": error,
                    "repairable": True,
                    "lane": lane_text,
                    "profile_id": payload.get("profile_id"),
                }
            )
            continue
        raw_status = _optional_text(payload.get("raw_status"))
        if raw_status in {"missing", "partial"}:
            blockers.append(
                {
                    "category": "standards-consistency",
                    "code": f"{lane_text}-standards-raw-{raw_status}",
                    "message": f"Raw standards bundle is {raw_status} for {lane_text} profile `{payload.get('profile_id')}`.",
                    "repairable": True,
                    "lane": lane_text,
                    "profile_id": payload.get("profile_id"),
                    "details": {"raw_status": raw_status, "profile_id": payload.get("profile_id")},
                }
            )
        if bool(payload.get("conflict_flag")):
            blockers.append(
                {
                    "category": "standards-consistency",
                    "code": f"{lane_text}-standards-conflict",
                    "message": f"Standards profile `{payload.get('profile_id')}` has a visible conflict flag.",
                    "repairable": True,
                    "lane": lane_text,
                    "profile_id": payload.get("profile_id"),
                }
            )
    return {"profiles": result, "blockers": blockers}


def _compact_runtime_state(records: Iterable[Any], active_run: dict[str, Any] | None) -> dict[str, Any]:
    recent: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for record in records:
        payload = record.to_dict() if hasattr(record, "to_dict") else dict(record) if isinstance(record, dict) else {}
        if not payload:
            continue
        record_blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
        contract_gates = payload.get("contract_gates") if isinstance(payload.get("contract_gates"), list) else []
        contract_gate_summary = _contract_gate_summary(contract_gates)
        recent.append(
            {
                "record_id": payload.get("record_id"),
                "status": payload.get("status"),
                "stage": payload.get("stage"),
                "lane": payload.get("lane"),
                "action": payload.get("action"),
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "summary": payload.get("summary"),
                "blocker_count": len(record_blockers),
                "terminal_reason": payload.get("terminal_reason"),
                "repair_decision": payload.get("repair_decision"),
                "thesis_repair_plan": payload.get("thesis_repair_plan")
                if isinstance(payload.get("thesis_repair_plan"), dict)
                else None,
                "contract_gates": contract_gates,
                "contract_gate_summary": contract_gate_summary,
            }
        )
        for raw_blocker in record_blockers:
            if isinstance(raw_blocker, dict):
                blockers.append(
                    _enrich_blocker(
                        raw_blocker,
                        lane=_optional_text(payload.get("lane")),
                        source="runtime",
                        record_id=_optional_text(payload.get("record_id")),
                    )
                )
        for raw_blocker in blocking_gate_blockers(
            contract_gates,
            lane=_optional_text(payload.get("lane")),
            action=_optional_text(payload.get("action")),
        ):
            blockers.append(
                _enrich_blocker(
                    raw_blocker,
                    lane=_optional_text(payload.get("lane")),
                    source="runtime-contract-gate",
                    record_id=_optional_text(payload.get("record_id")),
                )
            )
    return {
        "active_run": active_run,
        "recent": recent,
        "blockers": blockers,
    }


def _standard_profile_payload(lane: str, profile: Any) -> dict[str, Any]:
    if isinstance(profile, dict):
        payload = dict(profile)
        payload.setdefault("lane", lane)
        if "profile_id" not in payload:
            payload["profile_id"] = payload.get("resolved_profile_id") or payload.get("requested_profile_id")
        return payload
    return {
        "lane": lane,
        "requested_profile_id": getattr(profile, "requested_profile_id", None),
        "profile_id": getattr(profile, "resolved_profile_id", None),
        "fallback_profile_id": getattr(profile, "fallback_profile_id", None),
        "normalized_path": str(getattr(profile, "normalized_path", "")) or None,
        "raw_dir": str(getattr(profile, "raw_dir", "")) or None,
        "raw_manifest_path": str(getattr(profile, "raw_manifest_path", "")) or None,
        "raw_status": getattr(profile, "raw_status", None),
        "last_refresh_at": getattr(profile, "last_refresh_at", None),
        "official_only": getattr(profile, "official_only", None),
        "conflict_flag": getattr(profile, "conflict_flag", None),
        "profile_status": getattr(profile, "profile_status", None),
    }


def _compact_article_files(root_dir: Path, files: object) -> dict[str, dict[str, Any]]:
    if not isinstance(files, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, payload in files.items():
        if not isinstance(payload, dict):
            continue
        path = _compact_path(root_dir, _optional_text(payload.get("path")))
        result[str(name)] = {"path": path, "exists": bool(payload.get("exists"))}
    return result


def _enrich_blocker(raw_blocker: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload = dict(raw_blocker)
    for key, value in extra.items():
        if value is not None and payload.get(key) is None:
            payload[key] = value
    payload.setdefault("repairable", True)
    return payload


def _dedupe_blockers(blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for blocker in blockers:
        category = _optional_text(blocker.get("category")) or "review"
        code = _optional_text(blocker.get("code")) or "unknown-blocker"
        lane = _optional_text(blocker.get("lane")) or ""
        target = _optional_text(blocker.get("target")) or ""
        profile_id = _optional_text(blocker.get("profile_id")) or ""
        key = (category, code, lane, target, profile_id)
        if key in seen:
            continue
        seen.add(key)
        normalized = dict(blocker)
        normalized["category"] = category
        normalized["code"] = code
        normalized["message"] = _optional_text(normalized.get("message")) or code
        result.append(normalized)
    return result


def _runtime_contract_gate_summary(runtime: dict[str, Any]) -> dict[str, int]:
    recent = runtime.get("recent") if isinstance(runtime, dict) else None
    if not isinstance(recent, list):
        return {"total_count": 0, "block_count": 0, "warn_count": 0}
    total = {"total_count": 0, "block_count": 0, "warn_count": 0}
    for item in recent:
        if not isinstance(item, dict):
            continue
        summary = item.get("contract_gate_summary")
        if not isinstance(summary, dict):
            continue
        for key in total:
            total[key] += _optional_int(summary.get(key)) or 0
    return total


def _contract_gate_summary(gates: list[dict[str, Any]]) -> dict[str, int]:
    total = {"total_count": 0, "block_count": 0, "warn_count": 0}
    for item in gates:
        if not isinstance(item, dict):
            continue
        total["total_count"] += 1
        status = _optional_text(item.get("status"))
        if status == "block":
            total["block_count"] += 1
        elif status == "warn":
            total["warn_count"] += 1
    return total


def _strip_internal(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "blockers"}


def _assessment_scope() -> dict[str, Any]:
    return {
        "depth": "signals-only",
        "readiness_claim": "none",
        "does_not_replace": [
            "source-verification",
            "citation-checking",
            "standards-review",
            "repair-planning",
        ],
    }


def _first_work_continuation_action(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for action in actions:
        if action.get("blocks_workflow"):
            continue
        action_id = _optional_text(action.get("action_id")) or ""
        if action_id.startswith("standards-") or action_id.startswith("export-"):
            continue
        if action.get("lane") in {"thesis", "article"} or action_id == "draft-next":
            return action
    return None


def _is_work_continuation_action(action: WorkNextAction) -> bool:
    if action.blocks_workflow:
        return False
    if action.action_id.startswith("standards-") or action.action_id.startswith("export-"):
        return False
    return action.lane in {"thesis", "article"} or action.action_id == "draft-next"


def _has_workflow_blocking_action(actions: list[WorkNextAction]) -> bool:
    return any(action.blocks_workflow for action in actions)


def _has_export_gate_candidate(article: dict[str, Any], thesis: dict[str, Any]) -> bool:
    return _first_article_export_target(article) is not None or _thesis_ready_for_export(thesis)


def _first_blocker(
    blockers: list[dict[str, Any]],
    *,
    category: str | None = None,
    code_contains: str | None = None,
) -> dict[str, Any] | None:
    for blocker in blockers:
        if category and blocker.get("category") != category:
            continue
        if code_contains and code_contains not in str(blocker.get("code") or ""):
            continue
        return blocker
    return None


def _first_lane_blocker(
    blockers: list[dict[str, Any]],
    lane: str,
    *,
    exclude_categories: set[str] | None = None,
) -> dict[str, Any] | None:
    excluded = exclude_categories or set()
    for blocker in blockers:
        if blocker.get("category") in excluded:
            continue
        if blocker.get("lane") == lane:
            return blocker
    return None


def _first_article_repair_target(article: dict[str, Any]) -> str | None:
    for bundle in article.get("bundles") or []:
        if not isinstance(bundle, dict):
            continue
        if int(bundle.get("blocker_count") or 0) > 0:
            return _article_bundle_target(bundle, preferred=("draft", "final", "review", "brief"))
    for bundle in article.get("bundles") or []:
        if isinstance(bundle, dict):
            target = _article_bundle_target(bundle, preferred=("draft", "final", "review", "brief"))
            if target:
                return target
    return None


def _first_article_bundle_needing_review(article: dict[str, Any]) -> dict[str, Any] | None:
    for bundle in article.get("bundles") or []:
        if not isinstance(bundle, dict):
            continue
        if bundle.get("review_present"):
            continue
        if _article_bundle_target(bundle, preferred=("draft", "final")):
            return bundle
    return None


def _first_article_bundle_missing_checklist(article: dict[str, Any]) -> dict[str, Any] | None:
    for bundle in article.get("bundles") or []:
        if not isinstance(bundle, dict):
            continue
        if bundle.get("checklist_present"):
            continue
        final_file = ((bundle.get("files") or {}).get("final") or {}) if isinstance(bundle.get("files"), dict) else {}
        if isinstance(final_file, dict) and final_file.get("exists"):
            return bundle
    return None


def _first_article_export_target(article: dict[str, Any]) -> str | None:
    for bundle in article.get("bundles") or []:
        if not isinstance(bundle, dict):
            continue
        files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
        final_file = files.get("final") if isinstance(files.get("final"), dict) else {}
        if final_file.get("exists") and bundle.get("checklist_present") and bundle.get("review_present"):
            return _optional_text(final_file.get("path"))
    return None


def _article_bundle_target(bundle: dict[str, Any], preferred: tuple[str, ...] = ("draft", "final", "review", "brief")) -> str | None:
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    for name in preferred:
        payload = files.get(name) if isinstance(files.get(name), dict) else {}
        if payload.get("exists"):
            return _optional_text(payload.get("path"))
    return None


def _first_unreviewed_thesis_section(thesis: dict[str, Any]) -> dict[str, Any] | None:
    for section in thesis.get("sections") or []:
        if isinstance(section, dict) and not section.get("review_exists"):
            return section
    return None


def _first_thesis_target(thesis: dict[str, Any]) -> str | None:
    for section in thesis.get("sections") or []:
        if isinstance(section, dict):
            target = _optional_text(section.get("target"))
            if target:
                return target
    return None


def _thesis_ready_for_export(thesis: dict[str, Any]) -> bool:
    summary = thesis.get("summary") if isinstance(thesis.get("summary"), dict) else {}
    section_count = int(summary.get("section_count") or 0)
    reviewed_count = int(summary.get("reviewed_count") or 0)
    blocked_count = int(summary.get("blocked_count") or 0)
    return section_count > 0 and reviewed_count >= section_count and blocked_count == 0


def _compact_path(root_dir: Path, raw_path: str | None) -> str | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(root_dir).as_posix()
    except ValueError:
        return str(path)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
