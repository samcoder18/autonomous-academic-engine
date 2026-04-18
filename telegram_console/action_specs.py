from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .standards import StandardProfileResolution
from .workspace import WorkConfig


@dataclass(frozen=True)
class RequiredArtifact:
    name: str
    path: str
    requirement: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "path": self.path,
            "requirement": self.requirement,
            "description": self.description,
        }


@dataclass(frozen=True)
class QualityGate:
    gate_id: str
    description: str
    blocks_statuses: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "description": self.description,
            "blocks_statuses": list(self.blocks_statuses),
        }


@dataclass(frozen=True)
class RepairPolicy:
    eligible: bool
    max_iterations: int
    safe_only: bool
    triggers: tuple[str, ...]
    terminal_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "max_iterations": self.max_iterations,
            "safe_only": self.safe_only,
            "triggers": list(self.triggers),
            "terminal_reasons": list(self.terminal_reasons),
        }


@dataclass(frozen=True)
class ExecutionTransition:
    from_phase: str
    to_phase: str
    completion_signal: str

    def to_dict(self) -> dict[str, str]:
        return {
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "completion_signal": self.completion_signal,
        }


@dataclass(frozen=True)
class AllowedWriteScope:
    name: str
    path: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "path": self.path,
            "description": self.description,
        }


@dataclass(frozen=True)
class ActionSpec:
    lane: str
    action: str
    title: str
    summary: str
    target_kind: str
    prompt_rules: tuple[str, ...]
    deliverables: tuple[str, ...]
    required_checkpoints: tuple[str, ...]
    terminal_statuses: tuple[str, ...]
    transitions: tuple[ExecutionTransition, ...]
    quality_gates: tuple[QualityGate, ...]
    repair_policy: RepairPolicy
    target_validation: str


@dataclass(frozen=True)
class ExecutionContract:
    lane: str
    action: str
    title: str
    summary: str
    target_kind: str
    target_validation: str
    prompt_rules: tuple[str, ...]
    deliverables: tuple[str, ...]
    required_context: tuple[RequiredArtifact, ...]
    allowed_write_scopes: tuple[AllowedWriteScope, ...]
    required_outputs: tuple[RequiredArtifact, ...]
    required_checkpoints: tuple[str, ...]
    terminal_statuses: tuple[str, ...]
    quality_gates: tuple[QualityGate, ...]
    repair_policy: RepairPolicy
    transitions: tuple[ExecutionTransition, ...]
    metadata: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "action": self.action,
            "title": self.title,
            "summary": self.summary,
            "target_kind": self.target_kind,
            "target_validation": self.target_validation,
            "prompt_rules": list(self.prompt_rules),
            "deliverables": list(self.deliverables),
            "required_context": [item.to_dict() for item in self.required_context],
            "allowed_write_scopes": [item.to_dict() for item in self.allowed_write_scopes],
            "required_outputs": [item.to_dict() for item in self.required_outputs],
            "required_checkpoints": list(self.required_checkpoints),
            "terminal_statuses": list(self.terminal_statuses),
            "quality_gates": [item.to_dict() for item in self.quality_gates],
            "repair_policy": self.repair_policy.to_dict(),
            "transitions": [item.to_dict() for item in self.transitions],
            "metadata": [{key: value} for key, value in self.metadata],
        }


THESIS_TERMINAL_STATUSES = (
    "updated",
    "reviewed",
    "ready-with-caveats",
    "blocked-primary-support",
    "blocked-runtime",
)
ARTICLE_TERMINAL_STATUSES = (
    "submission-ready",
    "strong-draft",
    "strong-draft-with-blockers",
)
COMMON_REPAIR_TERMINAL_REASONS = (
    "ready",
    "ready-with-caveats",
    "blocked-primary-support",
    "blocked-standards",
    "blocked-runtime",
    "max-repair-iterations",
)

THESIS_QUALITY_GATES = (
    QualityGate("lane-boundary", "Canonical thesis text must stay inside works/<slug>/thesis/.", THESIS_TERMINAL_STATUSES),
    QualityGate("verified-support", "Strong claims must be supported or narrowed.", ("ready-with-caveats",)),
    QualityGate("dynamic-material-refresh", "Dynamic legal material must be rechecked against primary sources.", THESIS_TERMINAL_STATUSES),
)
ARTICLE_QUALITY_GATES = (
    QualityGate("lane-boundary", "Article artifacts must stay inside works/<slug>/articles/.", ARTICLE_TERMINAL_STATUSES),
    QualityGate("primary-support", "Submission-ready is blocked by unsupported strong claims.", ("submission-ready",)),
    QualityGate("standards-consistency", "Raw/normalized standards conflicts must stay visible in final status.", ("submission-ready",)),
    QualityGate("evaluator-verdict", "Final status must reflect evaluator blockers honestly.", ARTICLE_TERMINAL_STATUSES),
)


_ACTION_SPECS: dict[tuple[str, str], ActionSpec] = {
    ("thesis", "full-cycle"): ActionSpec(
        lane="thesis",
        action="full-cycle",
        title="Полный thesis cycle",
        summary="Полный bounded thesis workflow с drafting, verification, critique и финальным checkpoint.",
        target_kind="thesis artifact",
        prompt_rules=(
            "Open AGENTS.md, workspace.toml, the active work's work.toml, work-canon.md, and meta/master-protocol.md before editing.",
            "Use the appropriate internal chain across structure, research, verification, drafting, citations, criticism, and style.",
            "Write canonical thesis text only inside the active work bundle under works/{work.slug}/thesis/.",
            "For dynamic legal material, verify against up-to-date official or primary sources and use web search when needed.",
            "If you safely skip a workflow step, record the reason in a sync artifact inside the active work.",
            "If you update a manuscript section, rebuild with scripts/assemble_thesis.sh --work {work.slug}.",
            "If the task explicitly asks for Word output or reaches a polished section checkpoint, export DOCX with scripts/export_docx.sh --work {work.slug}.",
            "Do not optimize for detector bypass. Optimize for independent analysis, reliable sourcing, and natural academic prose.",
        ),
        deliverables=(
            "Make the changes directly in files.",
            "Update the work-local sync/ if the run produces a meaningful checkpoint.",
            "End with a concise summary of changed files, verification performed, and remaining risks.",
        ),
        required_checkpoints=(
            "context-loaded",
            "structure-confirmed",
            "sources-verified",
            "draft-updated",
            "reviewed",
            "sync-recorded-or-skipped",
        ),
        terminal_statuses=THESIS_TERMINAL_STATUSES,
        transitions=(
            ExecutionTransition("validated", "context-loaded", "Required context opened."),
            ExecutionTransition("context-loaded", "draft-updated", "Thesis artifact updated."),
            ExecutionTransition("draft-updated", "reviewed", "Verification and critique finished."),
        ),
        quality_gates=THESIS_QUALITY_GATES,
        repair_policy=RepairPolicy(
            eligible=True,
            max_iterations=1,
            safe_only=False,
            triggers=("review blockers", "verification drift"),
            terminal_reasons=COMMON_REPAIR_TERMINAL_REASONS,
        ),
        target_validation="Validated by workspace target normalization for thesis full-cycle actions.",
    ),
    ("thesis", "source-pack"): ActionSpec(
        lane="thesis",
        action="source-pack",
        title="Thesis source package",
        summary="Сборка и верификация source package без encyclopedic drift.",
        target_kind="source package",
        prompt_rules=(
            "Build or update the package using templates/source-package-passport.md.",
            "Prefer primary and official sources for law, case law, regulator guidance, and statistics.",
            "Record verification dates for dynamic materials.",
            "Mark what is verified, what still needs re-checking, and what remains analytical rather than factual.",
            "Keep the package compact and thesis-oriented rather than encyclopedic.",
        ),
        deliverables=(
            "Update the target package directly.",
            "Update the work-local sync/ if the package meaningfully changes the working baseline.",
            "End with a concise summary of sources added, sources verified, and gaps that still remain.",
        ),
        required_checkpoints=(
            "context-loaded",
            "sources-collected",
            "sources-verified",
            "package-updated",
            "sync-recorded-or-skipped",
        ),
        terminal_statuses=THESIS_TERMINAL_STATUSES,
        transitions=(
            ExecutionTransition("validated", "context-loaded", "Required context opened."),
            ExecutionTransition("context-loaded", "sources-verified", "Primary sources checked."),
            ExecutionTransition("sources-verified", "completed", "Source package saved."),
        ),
        quality_gates=THESIS_QUALITY_GATES,
        repair_policy=RepairPolicy(
            eligible=False,
            max_iterations=0,
            safe_only=True,
            triggers=(),
            terminal_reasons=COMMON_REPAIR_TERMINAL_REASONS,
        ),
        target_validation="Validated by workspace target normalization for thesis source-pack actions.",
    ),
    ("thesis", "verify"): ActionSpec(
        lane="thesis",
        action="verify",
        title="Thesis verification pass",
        summary="Проверка сильных утверждений, ссылок и динамических материалов без broad rewrite.",
        target_kind="thesis file",
        prompt_rules=(
            "Check significant legal, factual, and statistical claims for source support.",
            "For dynamic materials, verify against current official or primary sources and use web search when needed.",
            "Narrow or mark unsafe claims instead of leaving them overstated.",
            "Strengthen citations or footnote hygiene where appropriate.",
            "Do not do a broad stylistic rewrite unless a wording change is necessary to restore accuracy.",
        ),
        deliverables=(
            "Update the target file if factual or citation fixes are needed.",
            "If verification materially changes work assumptions, update the work-local sync/.",
            "End with a concise summary of what was verified, what was corrected, and what still needs follow-up.",
        ),
        required_checkpoints=(
            "context-loaded",
            "claims-checked",
            "citations-checked",
            "target-updated-or-confirmed",
            "sync-recorded-or-skipped",
        ),
        terminal_statuses=THESIS_TERMINAL_STATUSES,
        transitions=(
            ExecutionTransition("validated", "context-loaded", "Required context opened."),
            ExecutionTransition("context-loaded", "claims-checked", "Claims audited against sources."),
            ExecutionTransition("claims-checked", "completed", "Verification pass finished."),
        ),
        quality_gates=THESIS_QUALITY_GATES,
        repair_policy=RepairPolicy(
            eligible=True,
            max_iterations=1,
            safe_only=True,
            triggers=("unsupported claims", "citation drift", "dynamic material drift"),
            terminal_reasons=COMMON_REPAIR_TERMINAL_REASONS,
        ),
        target_validation="Validated by workspace target normalization for thesis verification actions.",
    ),
    ("thesis", "write-section"): ActionSpec(
        lane="thesis",
        action="write-section",
        title="Thesis section drafting",
        summary="Написание или расширение thesis section только по verified support.",
        target_kind="manuscript section",
        prompt_rules=(
            "Open the relevant brief, source packages, workspace docs, and work canon before writing.",
            "Draft only from verified sources or clearly marked analytical conclusions.",
            "Keep the voice academic, specific, and legally grounded.",
            "Add or maintain Markdown footnotes where source support is already pinned.",
            "If you safely skip a workflow step, record the reason in the work-local sync/.",
            "If the target is inside the manuscript sections, rebuild the manuscript after changes.",
        ),
        deliverables=(
            "Update the section directly.",
            "Update the work-local sync/ if the section reaches a meaningful checkpoint.",
            "End with a concise summary of what was written, which sources were relied on, and what remains unverified or incomplete.",
        ),
        required_checkpoints=(
            "context-loaded",
            "sources-confirmed",
            "section-updated",
            "manuscript-rebuilt-if-needed",
            "sync-recorded-or-skipped",
        ),
        terminal_statuses=THESIS_TERMINAL_STATUSES,
        transitions=(
            ExecutionTransition("validated", "context-loaded", "Required context opened."),
            ExecutionTransition("context-loaded", "section-updated", "Section content updated."),
            ExecutionTransition("section-updated", "completed", "Section drafting pass finished."),
        ),
        quality_gates=THESIS_QUALITY_GATES,
        repair_policy=RepairPolicy(
            eligible=False,
            max_iterations=0,
            safe_only=False,
            triggers=(),
            terminal_reasons=COMMON_REPAIR_TERMINAL_REASONS,
        ),
        target_validation="Validated by workspace target normalization for thesis manuscript sections.",
    ),
    ("thesis", "review-section"): ActionSpec(
        lane="thesis",
        action="review-section",
        title="Thesis section review",
        summary="Findings-first critique of a thesis section with dedicated review artifact.",
        target_kind="manuscript section review",
        prompt_rules=(
            "Review the section for logic gaps, overclaims, repetition, weak transitions, and citation issues.",
            "Create or update the review artifact using templates/chapter-review-sheet.md.",
            "Keep the primary output findings-first.",
            "Do not rewrite the manuscript broadly; only make trivial citation-hygiene fixes if they are obvious and safe.",
        ),
        deliverables=(
            "Update or create the dedicated review artifact.",
            "Update the work-local sync/ if the review changes priorities or safely skips any expected check.",
            "End with the key findings first, then a brief note on any small fixes made.",
        ),
        required_checkpoints=(
            "context-loaded",
            "section-reviewed",
            "review-artifact-updated",
            "sync-recorded-or-skipped",
        ),
        terminal_statuses=THESIS_TERMINAL_STATUSES,
        transitions=(
            ExecutionTransition("validated", "context-loaded", "Required context opened."),
            ExecutionTransition("context-loaded", "section-reviewed", "Review findings collected."),
            ExecutionTransition("section-reviewed", "completed", "Review artifact saved."),
        ),
        quality_gates=THESIS_QUALITY_GATES,
        repair_policy=RepairPolicy(
            eligible=True,
            max_iterations=1,
            safe_only=True,
            triggers=("review blockers", "logic gaps", "citation issues"),
            terminal_reasons=COMMON_REPAIR_TERMINAL_REASONS,
        ),
        target_validation="Validated by workspace target normalization for thesis review actions.",
    ),
    ("thesis", "style-pass"): ActionSpec(
        lane="thesis",
        action="style-pass",
        title="Thesis style pass",
        summary="Финальная stylistic refinement без detector games и semantic drift.",
        target_kind="checked thesis text",
        prompt_rules=(
            "Improve natural academic Russian, paragraph rhythm, specificity, and authorial voice.",
            "Do not change the substantive meaning of claims unless a tiny narrowing is needed for credibility.",
            "Do not optimize for detector bypass or mechanical uniqueness.",
            "Remove stock transitions and machine-flat phrasing where possible.",
            "If the target is inside manuscript sections, rebuild the manuscript after changes.",
        ),
        deliverables=(
            "Update the target file directly.",
            "End with a concise summary of stylistic improvements and any residual sections that still sound too generic.",
        ),
        required_checkpoints=(
            "context-loaded",
            "style-pass-completed",
            "manuscript-rebuilt-if-needed",
        ),
        terminal_statuses=THESIS_TERMINAL_STATUSES,
        transitions=(
            ExecutionTransition("validated", "context-loaded", "Required context opened."),
            ExecutionTransition("context-loaded", "style-pass-completed", "Style edits applied."),
            ExecutionTransition("style-pass-completed", "completed", "Style pass finished."),
        ),
        quality_gates=THESIS_QUALITY_GATES,
        repair_policy=RepairPolicy(
            eligible=False,
            max_iterations=0,
            safe_only=True,
            triggers=(),
            terminal_reasons=COMMON_REPAIR_TERMINAL_REASONS,
        ),
        target_validation="Validated by workspace target normalization for thesis style-pass actions.",
    ),
    ("article", "article"): ActionSpec(
        lane="article",
        action="article",
        title="Article full workflow",
        summary="Полный bounded article workflow от brief до final status с evaluator gates.",
        target_kind="article brief or topic",
        prompt_rules=(
            "Open README.md, AGENTS.md, workspace.toml, the active work's work.toml, work-canon.md, meta/master-protocol.md, the active profile, and the article templates before editing.",
            "Start with academic intake and normalize the request into the managed brief path.",
            "Use source acquisition, source verification, and evidence cartography before serious drafting.",
            "For law, case law, regulator guidance, and statistics, final authority must be official or primary.",
            "Secondary literature is interpretive support, not a substitute for primary verification.",
            "Proprietary legal databases and aggregators may be used only as navigational support.",
            "Build or update the evidence pack and claim map so each significant claim has an evidence trace or an explicit analytical status.",
            "Draft only from verified support or clearly marked analytical conclusions.",
            "Run citation checking, counterargument critique, and submission evaluation before finalization.",
            "If blockers remain, use bounded repair logic and do not overstate readiness.",
            "If strong primary gaps remain, downgrade to strong-draft-with-blockers.",
            "Finish with final markdown, checklist, and DOCX export via scripts/export_academic_docx.sh --work {work.slug}.",
            "If relevant official raw formatting standards are missing or conflicting, reflect that as a blocker in the checklist and do not overstate formal submission readiness.",
        ),
        deliverables=(
            "Update the managed article bundle directly.",
            "End with the explicit status submission-ready, strong-draft, or strong-draft-with-blockers.",
            "Summarize changed files, verification performed, exported outputs, and remaining blockers.",
        ),
        required_checkpoints=(
            "brief-normalized",
            "evidence-updated",
            "claim-map-updated",
            "draft-updated",
            "reviewed",
            "final-status-issued",
        ),
        terminal_statuses=ARTICLE_TERMINAL_STATUSES,
        transitions=(
            ExecutionTransition("validated", "briefed", "Brief exists in managed bundle."),
            ExecutionTransition("briefed", "evidence-verified", "Evidence and claim map updated."),
            ExecutionTransition("evidence-verified", "reviewed", "Evaluator pass completed."),
            ExecutionTransition("reviewed", "finalized", "Final markdown/checklist ready or honestly downgraded."),
        ),
        quality_gates=ARTICLE_QUALITY_GATES,
        repair_policy=RepairPolicy(
            eligible=True,
            max_iterations=2,
            safe_only=False,
            triggers=("evaluator blockers", "primary support gaps", "standards blockers"),
            terminal_reasons=COMMON_REPAIR_TERMINAL_REASONS,
        ),
        target_validation="Validated by workspace target normalization for article workflow inputs.",
    ),
    ("article", "review"): ActionSpec(
        lane="article",
        action="review",
        title="Article review",
        summary="Findings-first article review with evaluator verdict and repair eligibility.",
        target_kind="article draft or final markdown",
        prompt_rules=(
            "Treat this as an article-lane review scoped to the active work.",
            "Review source integrity, primary support, dynamic materials, counterarguments, composition, citations, and checklist blockers.",
            "Use templates/article-review-sheet.md and update the managed review file.",
            "Verify dynamic legal material against current official or primary sources when needed.",
            "Output a findings-first review with the verdict submission-ready, strong-draft, or strong-draft-with-blockers.",
            "Do not broadly rewrite the target file; only make tiny safe citation or factual fixes if they are obvious and necessary.",
        ),
        deliverables=(
            "Update or create the managed review sheet.",
            "End with the key findings first, then the explicit verdict and next repair priorities.",
        ),
        required_checkpoints=(
            "context-loaded",
            "review-sheet-updated",
            "verdict-issued",
        ),
        terminal_statuses=ARTICLE_TERMINAL_STATUSES,
        transitions=(
            ExecutionTransition("validated", "context-loaded", "Required context opened."),
            ExecutionTransition("context-loaded", "reviewed", "Findings-first review completed."),
            ExecutionTransition("reviewed", "completed", "Verdict saved."),
        ),
        quality_gates=ARTICLE_QUALITY_GATES,
        repair_policy=RepairPolicy(
            eligible=True,
            max_iterations=2,
            safe_only=True,
            triggers=("review blockers", "unsupported claims", "citation issues"),
            terminal_reasons=COMMON_REPAIR_TERMINAL_REASONS,
        ),
        target_validation="Validated by workspace target normalization for article review actions.",
    ),
    ("article", "repair"): ActionSpec(
        lane="article",
        action="repair",
        title="Article repair",
        summary="Bounded article repair loop with re-evaluation and honest downgrade rules.",
        target_kind="article draft, final markdown, or review input",
        prompt_rules=(
            "Prioritize primary-source blockers, unsupported claims, and missing caveats before style or polish.",
            "Use the companion review file when it exists.",
            "Keep the repair inside article-lane artifacts only.",
            "Do not hide unresolved blockers behind nicer prose.",
            "Re-run evaluator logic before finalization.",
            "If relevant raw formatting standards are still missing or conflicting, preserve that blocker in the checklist.",
            "Finish by updating the active draft or final markdown, the checklist, and DOCX export when justified.",
            "If blockers remain after reasonable repair, keep or downgrade the status to strong-draft-with-blockers.",
        ),
        deliverables=(
            "Update the relevant article bundle files directly.",
            "End with the explicit post-repair status, changed files, and remaining blockers.",
        ),
        required_checkpoints=(
            "repair-plan-formed",
            "repair-applied",
            "re-evaluated",
            "terminal-status-issued",
        ),
        terminal_statuses=ARTICLE_TERMINAL_STATUSES,
        transitions=(
            ExecutionTransition("validated", "repairing", "Repair run started."),
            ExecutionTransition("repairing", "re-evaluated", "Evaluator pass rerun."),
            ExecutionTransition("re-evaluated", "completed", "Bounded repair ended."),
        ),
        quality_gates=ARTICLE_QUALITY_GATES,
        repair_policy=RepairPolicy(
            eligible=True,
            max_iterations=2,
            safe_only=False,
            triggers=("existing blockers", "failed evaluator gates"),
            terminal_reasons=COMMON_REPAIR_TERMINAL_REASONS,
        ),
        target_validation="Validated by workspace target normalization for article repair actions.",
    ),
}


def list_action_specs(lane: str | None = None) -> tuple[ActionSpec, ...]:
    if lane is None:
        return tuple(spec for _, spec in sorted(_ACTION_SPECS.items()))
    lane_text = lane.strip().lower()
    return tuple(spec for (item_lane, _), spec in sorted(_ACTION_SPECS.items()) if item_lane == lane_text)


def resolve_action_spec(lane: str, action: str) -> ActionSpec:
    key = (lane.strip().lower(), action.strip().lower())
    spec = _ACTION_SPECS.get(key)
    if spec is None:
        raise KeyError(f"Unknown action spec: {lane}/{action}")
    return spec


def execution_contract_from_payload(payload: dict[str, Any] | None) -> ExecutionContract | None:
    if not isinstance(payload, dict):
        return None
    lane = _optional_text(payload.get("lane"))
    action = _optional_text(payload.get("action"))
    title = _optional_text(payload.get("title"))
    summary = _optional_text(payload.get("summary"))
    target_kind = _optional_text(payload.get("target_kind"))
    target_validation = _optional_text(payload.get("target_validation"))
    repair_policy_payload = payload.get("repair_policy")
    if not all((lane, action, title, summary, target_kind, target_validation)) or not isinstance(repair_policy_payload, dict):
        return None
    return ExecutionContract(
        lane=lane,
        action=action,
        title=title,
        summary=summary,
        target_kind=target_kind,
        target_validation=target_validation,
        prompt_rules=_tuple_of_text(payload.get("prompt_rules")),
        deliverables=_tuple_of_text(payload.get("deliverables")),
        required_context=_required_artifacts_from_payload(payload.get("required_context")),
        allowed_write_scopes=_allowed_writes_from_payload(payload.get("allowed_write_scopes")),
        required_outputs=_required_artifacts_from_payload(payload.get("required_outputs")),
        required_checkpoints=_tuple_of_text(payload.get("required_checkpoints")),
        terminal_statuses=_tuple_of_text(payload.get("terminal_statuses")),
        quality_gates=_quality_gates_from_payload(payload.get("quality_gates")),
        repair_policy=_repair_policy_from_payload(repair_policy_payload),
        transitions=_transitions_from_payload(payload.get("transitions")),
        metadata=_metadata_from_payload(payload.get("metadata")),
    )


def build_thesis_execution_contract(
    *,
    work: WorkConfig,
    profile: StandardProfileResolution,
    action: str,
    target_path: Path,
    target_rel: str,
    related_context: list[Path],
    review_path: Path | None,
    sync_hint_path: Path | None,
) -> ExecutionContract:
    spec = resolve_action_spec("thesis", action)
    assert work.thesis is not None
    context = _contract_context(related_context[:8], required_names=("AGENTS", "workspace", "work config", "canon", "target"))
    allowed_writes = _thesis_allowed_writes(work, action, target_path, review_path, sync_hint_path)
    outputs = _thesis_required_outputs(work, action, target_path, review_path, sync_hint_path)
    metadata = (
        ("work_id", work.slug),
        ("profile_id", profile.resolved_profile_id),
        ("target_relative", target_rel),
        ("target_path", str(target_path)),
    )
    return ExecutionContract(
        lane=spec.lane,
        action=spec.action,
        title=spec.title,
        summary=spec.summary,
        target_kind=spec.target_kind,
        target_validation=spec.target_validation,
        prompt_rules=_format_with_work(spec.prompt_rules, work),
        deliverables=_format_with_work(spec.deliverables, work),
        required_context=context,
        allowed_write_scopes=allowed_writes,
        required_outputs=outputs,
        required_checkpoints=spec.required_checkpoints,
        terminal_statuses=spec.terminal_statuses,
        quality_gates=spec.quality_gates,
        repair_policy=spec.repair_policy,
        transitions=spec.transitions,
        metadata=metadata,
    )


def build_article_execution_contract(
    *,
    work: WorkConfig,
    profile: StandardProfileResolution,
    action: str,
    related_context: list[Path],
    bundle: dict[str, Path],
    topic: str | None,
    input_brief_path: Path | None,
    target_path: Path | None,
    target_rel: str | None,
) -> ExecutionContract:
    spec = resolve_action_spec("article", action)
    assert work.article is not None
    context = _contract_context(related_context[:10], required_names=("AGENTS", "workspace", "work config", "canon"))
    allowed_writes = _article_allowed_writes(work, action, bundle, target_path)
    outputs = _article_required_outputs(action, bundle)
    metadata_items = [
        ("work_id", work.slug),
        ("profile_id", profile.resolved_profile_id),
    ]
    if topic:
        metadata_items.append(("topic", topic))
    if input_brief_path:
        metadata_items.append(("input_brief", str(input_brief_path)))
    if target_path:
        metadata_items.append(("target_path", str(target_path)))
    if target_rel:
        metadata_items.append(("target_relative", target_rel))
    return ExecutionContract(
        lane=spec.lane,
        action=spec.action,
        title=spec.title,
        summary=spec.summary,
        target_kind=spec.target_kind,
        target_validation=spec.target_validation,
        prompt_rules=_format_with_work(spec.prompt_rules, work),
        deliverables=_format_with_work(spec.deliverables, work),
        required_context=context,
        allowed_write_scopes=allowed_writes,
        required_outputs=outputs,
        required_checkpoints=spec.required_checkpoints,
        terminal_statuses=spec.terminal_statuses,
        quality_gates=spec.quality_gates,
        repair_policy=spec.repair_policy,
        transitions=spec.transitions,
        metadata=tuple(metadata_items),
    )


def _required_artifacts_from_payload(payload: object) -> tuple[RequiredArtifact, ...]:
    if not isinstance(payload, list):
        return ()
    items: list[RequiredArtifact] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = _optional_text(item.get("name"))
        path = _optional_text(item.get("path"))
        requirement = _optional_text(item.get("requirement"))
        description = _optional_text(item.get("description"))
        if not all((name, path, requirement, description)):
            continue
        items.append(
            RequiredArtifact(
                name=name,
                path=path,
                requirement=requirement,
                description=description,
            )
        )
    return tuple(items)


def _allowed_writes_from_payload(payload: object) -> tuple[AllowedWriteScope, ...]:
    if not isinstance(payload, list):
        return ()
    items: list[AllowedWriteScope] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = _optional_text(item.get("name"))
        path = _optional_text(item.get("path"))
        description = _optional_text(item.get("description"))
        if not all((name, path, description)):
            continue
        items.append(AllowedWriteScope(name=name, path=path, description=description))
    return tuple(items)


def _quality_gates_from_payload(payload: object) -> tuple[QualityGate, ...]:
    if not isinstance(payload, list):
        return ()
    items: list[QualityGate] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        gate_id = _optional_text(item.get("gate_id"))
        description = _optional_text(item.get("description"))
        if not all((gate_id, description)):
            continue
        items.append(
            QualityGate(
                gate_id=gate_id,
                description=description,
                blocks_statuses=_tuple_of_text(item.get("blocks_statuses")),
            )
        )
    return tuple(items)


def _repair_policy_from_payload(payload: dict[str, Any]) -> RepairPolicy:
    max_iterations = payload.get("max_iterations")
    return RepairPolicy(
        eligible=bool(payload.get("eligible")),
        max_iterations=max_iterations if isinstance(max_iterations, int) else 0,
        safe_only=bool(payload.get("safe_only")),
        triggers=_tuple_of_text(payload.get("triggers")),
        terminal_reasons=_tuple_of_text(payload.get("terminal_reasons")),
    )


def _transitions_from_payload(payload: object) -> tuple[ExecutionTransition, ...]:
    if not isinstance(payload, list):
        return ()
    items: list[ExecutionTransition] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        from_phase = _optional_text(item.get("from_phase"))
        to_phase = _optional_text(item.get("to_phase"))
        completion_signal = _optional_text(item.get("completion_signal"))
        if not all((from_phase, to_phase, completion_signal)):
            continue
        items.append(
            ExecutionTransition(
                from_phase=from_phase,
                to_phase=to_phase,
                completion_signal=completion_signal,
            )
        )
    return tuple(items)


def _metadata_from_payload(payload: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(payload, list):
        return ()
    items: list[tuple[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            key_text = _optional_text(key)
            value_text = _optional_text(value)
            if key_text and value_text:
                items.append((key_text, value_text))
    return tuple(items)


def _tuple_of_text(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, list | tuple):
        return ()
    result: list[str] = []
    for item in payload:
        text = _optional_text(item)
        if text:
            result.append(text)
    return tuple(result)


def _optional_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _contract_context(paths: list[Path], *, required_names: tuple[str, ...]) -> tuple[RequiredArtifact, ...]:
    result: list[RequiredArtifact] = []
    for index, path in enumerate(paths, start=1):
        label = required_names[index - 1] if index <= len(required_names) else f"context-{index}"
        result.append(
            RequiredArtifact(
                name=label.lower().replace(" ", "-"),
                path=str(path),
                requirement="required" if index <= len(required_names) else "optional",
                description=f"Context artifact: {label}.",
            )
        )
    return tuple(result)


def _thesis_allowed_writes(
    work: WorkConfig,
    action: str,
    target_path: Path,
    review_path: Path | None,
    sync_hint_path: Path | None,
) -> tuple[AllowedWriteScope, ...]:
    assert work.thesis is not None
    items = [
        AllowedWriteScope("canonical-target", str(target_path), "Primary thesis target for this action."),
    ]
    if action == "full-cycle":
        items.extend(
            [
                AllowedWriteScope("chapters", str(work.thesis.chapters_dir), "Chapter briefs and architecture notes."),
                AllowedWriteScope("sources", str(work.thesis.sources_dir), "Thesis source packages."),
                AllowedWriteScope("sections", str(work.thesis.manuscript_sections_dir), "Canonical thesis sections."),
                AllowedWriteScope("reviews", str(work.thesis.reviews_dir), "Review sheets."),
                AllowedWriteScope("sync", str(work.thesis.sync_dir), "Checkpoint sync artifacts."),
                AllowedWriteScope("full-draft", str(work.thesis.full_draft_path), "Assembled manuscript output."),
                AllowedWriteScope("docx", str(work.thesis.export_docx_path), "Optional DOCX export target."),
            ]
        )
    elif action in {"source-pack", "verify", "write-section"}:
        items.append(AllowedWriteScope("sync", str(work.thesis.sync_dir), "Sync checkpoints for this work."))
    elif action == "review-section" and review_path:
        items.append(AllowedWriteScope("review-artifact", str(review_path), "Dedicated review sheet."))
        items.append(AllowedWriteScope("sync", str(work.thesis.sync_dir), "Sync checkpoints for this work."))
    if action in {"write-section", "style-pass"}:
        items.append(AllowedWriteScope("full-draft", str(work.thesis.full_draft_path), "Assembled manuscript output."))
    if sync_hint_path is not None and action != "full-cycle":
        items.append(AllowedWriteScope("sync-hint", str(sync_hint_path), "Preferred sync checkpoint path."))
    return tuple(_dedupe_allowed_writes(items))


def _thesis_required_outputs(
    work: WorkConfig,
    action: str,
    target_path: Path,
    review_path: Path | None,
    sync_hint_path: Path | None,
) -> tuple[RequiredArtifact, ...]:
    assert work.thesis is not None
    outputs = [
        RequiredArtifact("target-file", str(target_path), "required", "Primary target affected by the action."),
    ]
    if action in {"write-section", "style-pass", "full-cycle"}:
        outputs.append(
            RequiredArtifact("assembled-manuscript", str(work.thesis.full_draft_path), "conditional", "Rebuilt after section changes.")
        )
    if action == "review-section" and review_path:
        outputs.append(
            RequiredArtifact("review-sheet", str(review_path), "required", "Dedicated findings-first review artifact.")
        )
    if sync_hint_path is not None:
        outputs.append(
            RequiredArtifact("sync-checkpoint", str(sync_hint_path), "conditional", "Sync trace if assumptions or baseline changed.")
        )
    return tuple(outputs)


def _article_allowed_writes(
    work: WorkConfig,
    action: str,
    bundle: dict[str, Path],
    target_path: Path | None,
) -> tuple[AllowedWriteScope, ...]:
    assert work.article is not None
    items = [
        AllowedWriteScope("article-root", str(work.article.paths.root_dir), "Managed article bundle root."),
        AllowedWriteScope("brief", str(bundle["brief"]), "Managed brief path."),
        AllowedWriteScope("evidence-pack", str(bundle["evidence_pack"]), "Managed evidence pack."),
        AllowedWriteScope("claim-map", str(bundle["claim_map"]), "Managed claim map."),
        AllowedWriteScope("draft", str(bundle["draft"]), "Managed draft."),
        AllowedWriteScope("review", str(bundle["review"]), "Managed review sheet."),
        AllowedWriteScope("final-markdown", str(bundle["final_markdown"]), "Managed final markdown."),
        AllowedWriteScope("checklist", str(bundle["checklist"]), "Managed checklist."),
        AllowedWriteScope("docx", str(bundle["docx"]), "Expected DOCX export target."),
    ]
    if target_path is not None:
        items.append(AllowedWriteScope("requested-target", str(target_path), "Explicit article target input."))
    if action == "review":
        return tuple(
            item for item in _dedupe_allowed_writes(items) if item.name in {"article-root", "review", "requested-target", "checklist"}
        )
    return tuple(_dedupe_allowed_writes(items))


def _article_required_outputs(action: str, bundle: dict[str, Path]) -> tuple[RequiredArtifact, ...]:
    if action == "article":
        return (
            RequiredArtifact("brief", str(bundle["brief"]), "required", "Normalized article brief."),
            RequiredArtifact("evidence-pack", str(bundle["evidence_pack"]), "required", "Evidence pack with verified support."),
            RequiredArtifact("claim-map", str(bundle["claim_map"]), "required", "Coverage and claim map."),
            RequiredArtifact("draft", str(bundle["draft"]), "required", "Working article draft."),
            RequiredArtifact("review-sheet", str(bundle["review"]), "conditional", "Evaluator or critique sheet."),
            RequiredArtifact("final-markdown", str(bundle["final_markdown"]), "conditional", "Final markdown when ready."),
            RequiredArtifact("checklist", str(bundle["checklist"]), "conditional", "Submission checklist and blockers."),
            RequiredArtifact("docx", str(bundle["docx"]), "conditional", "DOCX export when justified."),
        )
    if action == "review":
        return (
            RequiredArtifact("review-sheet", str(bundle["review"]), "required", "Findings-first review output."),
            RequiredArtifact("checklist", str(bundle["checklist"]), "conditional", "Checklist blockers if updated during review."),
        )
    return (
        RequiredArtifact("draft", str(bundle["draft"]), "conditional", "Updated article draft."),
        RequiredArtifact("final-markdown", str(bundle["final_markdown"]), "conditional", "Updated final markdown."),
        RequiredArtifact("checklist", str(bundle["checklist"]), "required", "Updated checklist and blockers."),
        RequiredArtifact("docx", str(bundle["docx"]), "conditional", "DOCX export when justified."),
    )


def _format_with_work(items: tuple[str, ...], work: WorkConfig) -> tuple[str, ...]:
    return tuple(item.format(work=work) for item in items)


def _dedupe_allowed_writes(items: list[AllowedWriteScope]) -> list[AllowedWriteScope]:
    result: list[AllowedWriteScope] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.name, item.path)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
