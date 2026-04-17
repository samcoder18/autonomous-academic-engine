from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Any
import json
import os
import re
import shlex
import subprocess
import sys

from .workspace import (
    WorkspaceConfig,
    WorkspaceConfigError,
    WorkConfig,
    article_bundle_paths,
    derive_review_path,
    list_targets_for_action,
    load_work_config,
    load_workspace_config,
    normalize_target_for_action,
    relative_to_workspace,
    resolve_work_config,
)


THESIS_PRESETS = (
    "full-cycle",
    "source-pack",
    "verify",
    "write-section",
    "review-section",
    "style-pass",
)
ARTICLE_COMMANDS = ("article", "review", "repair")


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Work-aware launchers and exporters.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    thesis = subparsers.add_parser("launch-thesis")
    thesis.add_argument("preset", choices=THESIS_PRESETS)
    thesis.add_argument("target")
    thesis.add_argument("--work", dest="work_id")
    thesis.add_argument("--notes")
    thesis.add_argument("--dry-run", action="store_true")
    thesis.add_argument("--search", dest="search_override", action="store_const", const=True)
    thesis.add_argument("--no-search", dest="search_override", action="store_const", const=False)
    thesis.add_argument("--model")

    academic = subparsers.add_parser("launch-academic")
    academic.add_argument("workflow", choices=ARTICLE_COMMANDS)
    academic.add_argument("target", nargs="?")
    academic.add_argument("--work", dest="work_id")
    academic.add_argument("--topic")
    academic.add_argument("--brief")
    academic.add_argument("--notes")
    academic.add_argument("--profile")
    academic.add_argument("--dry-run", action="store_true")
    academic.add_argument("--search", dest="search_override", action="store_const", const=True)
    academic.add_argument("--no-search", dest="search_override", action="store_const", const=False)
    academic.add_argument("--model")

    assemble = subparsers.add_parser("assemble-thesis")
    assemble.add_argument("--work", dest="work_id")

    export_thesis = subparsers.add_parser("export-thesis-docx")
    export_thesis.add_argument("--work", dest="work_id")

    export_article = subparsers.add_parser("export-article-docx")
    export_article.add_argument("input_md")
    export_article.add_argument("output_docx", nargs="?")
    export_article.add_argument("--work", dest="work_id")

    args = parser.parse_args(argv)
    root_dir = Path(__file__).resolve().parents[1]

    try:
        if args.command == "launch-thesis":
            return launch_thesis(root_dir, args)
        if args.command == "launch-academic":
            return launch_academic(root_dir, args)
        if args.command == "assemble-thesis":
            return assemble_thesis(root_dir, args.work_id)
        if args.command == "export-thesis-docx":
            return export_thesis_docx(root_dir, args.work_id)
        if args.command == "export-article-docx":
            return export_article_docx(root_dir, args.input_md, args.output_docx, args.work_id)
    except WorkspaceConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


def launch_thesis(root_dir: Path, args: Any) -> int:
    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=args.work_id, target=args.target)
    if not work.thesis:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает thesis lane.")

    target_rel = normalize_target_for_action(workspace, work, "thesis", args.preset, args.target)
    if target_rel == relative_to_workspace(workspace, work.thesis.full_draft_path):
        raise WorkspaceConfigError("Use manuscript/sections as the editable target, not the assembled full draft.")

    target_path = workspace.root_dir / target_rel
    target_state = "existing" if target_path.exists() else "missing"
    use_search = _resolve_search(args.search_override, args.preset in {"full-cycle", "source-pack", "verify", "write-section"})
    review_path = derive_review_path(workspace, work, target_rel)
    sync_hint_path = _sync_path_for_target(work, args.preset, target_rel)
    related_context = _thesis_related_context(workspace, work, target_path)
    notes_content = _read_notes(root_dir, args.notes)
    prompt = _build_thesis_prompt(
        workspace,
        work,
        args.preset,
        target_path,
        target_rel,
        target_state,
        use_search,
        related_context,
        review_path,
        sync_hint_path,
        notes_content,
    )

    if args.dry_run:
        _print_thesis_dry_run(
            work,
            args.preset,
            target_path,
            target_rel,
            target_state,
            use_search,
            review_path,
            sync_hint_path,
            args.model,
            related_context,
            prompt,
        )
        return 0

    output_dir = work.thesis.paths.output_runs_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = output_dir / f"{timestamp}-{args.preset}.md"
    manifest_file = output_dir / f"{timestamp}-{args.preset}.meta.json"
    _run_codex(root_dir, prompt, out_file, use_search, args.model)
    _write_json(
        manifest_file,
        {
            "timestamp": timestamp,
            "preset": args.preset,
            "work_id": work.slug,
            "work_title": work.title,
            "target": {
                "absolute": str(target_path),
                "relative": target_rel,
                "state": target_state,
            },
            "search_enabled": use_search,
            "model": args.model or None,
            "root_dir": str(root_dir),
            "output_file": str(out_file),
            "expected_review_file": str(review_path) if review_path else None,
            "sync_hint_file": str(sync_hint_path) if sync_hint_path else None,
            "related_context": [str(path) for path in related_context],
        },
    )
    print(f"Saved final message to {out_file}")
    print(f"Saved run manifest to {manifest_file}")
    return 0


def launch_academic(root_dir: Path, args: Any) -> int:
    workspace = load_workspace_config(root_dir)

    target_hint = args.brief or args.target
    work = resolve_work_config(workspace, work_id=args.work_id, target=target_hint)
    if not work.article:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает article lane.")

    profile_id = _resolve_profile_id(workspace, work, args.profile)
    profile_path = _resolve_profile_path(root_dir, profile_id)
    use_search = _resolve_search(args.search_override, True)
    notes_content = _read_notes(root_dir, args.notes)

    topic: str | None = None
    input_brief_path: Path | None = None
    target_path: Path | None = None
    target_rel: str | None = None

    if args.workflow == "article":
        if bool(args.topic) == bool(args.brief):
            raise WorkspaceConfigError("Для команды article нужно указать ровно один из аргументов: --topic или --brief.")
        if args.brief:
            target_rel = normalize_target_for_action(workspace, work, "article", "article-brief", args.brief)
            input_brief_path = workspace.root_dir / target_rel
        else:
            topic = args.topic.strip()
            if not topic:
                raise WorkspaceConfigError("Тема статьи не может быть пустой.")
    else:
        if not args.target:
            raise WorkspaceConfigError(f"Команда `{args.workflow}` ожидает target-файл.")
        target_rel = normalize_target_for_action(workspace, work, "article", args.workflow, args.target)
        target_path = workspace.root_dir / target_rel

    article_slug = _slugify_text(
        (Path(target_rel).stem if target_rel else None)
        or (input_brief_path.stem if input_brief_path else None)
        or topic
        or "article-topic"
    )
    bundle = article_bundle_paths(work, article_slug)
    related_context = _article_related_context(
        workspace,
        work,
        profile_path,
        input_brief_path,
        target_path,
        bundle,
    )

    if args.workflow == "article":
        prompt = _build_article_prompt(
            workspace,
            work,
            profile_id,
            profile_path,
            use_search,
            topic,
            input_brief_path,
            bundle,
            related_context,
            notes_content,
        )
    elif args.workflow == "review":
        prompt = _build_review_prompt(
            workspace,
            work,
            profile_id,
            profile_path,
            use_search,
            target_path,
            target_rel,
            bundle,
            related_context,
            notes_content,
        )
    else:
        prompt = _build_repair_prompt(
            workspace,
            work,
            profile_id,
            profile_path,
            use_search,
            target_path,
            target_rel,
            bundle,
            related_context,
            notes_content,
        )

    if args.dry_run:
        _print_academic_dry_run(
            work,
            args.workflow,
            profile_id,
            use_search,
            topic,
            input_brief_path,
            target_path,
            target_rel,
            article_slug,
            args.model,
            bundle,
            related_context,
            prompt,
        )
        return 0

    output_dir = work.article.paths.output_runs_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = output_dir / f"{timestamp}-{args.workflow}-{article_slug}.md"
    manifest_file = output_dir / f"{timestamp}-{args.workflow}-{article_slug}.meta.json"
    _run_codex(root_dir, prompt, out_file, use_search, args.model)
    _write_json(
        manifest_file,
        {
            "timestamp": timestamp,
            "command": args.workflow,
            "work_id": work.slug,
            "work_title": work.title,
            "profile_id": profile_id,
            "search_enabled": use_search,
            "topic": topic,
            "input_brief": relative_to_workspace(workspace, input_brief_path) if input_brief_path else None,
            "target_path": relative_to_workspace(workspace, target_path) if target_path else None,
            "root_dir": str(root_dir),
            "output_file": str(out_file),
            "bundle": {
                "slug": article_slug,
                "brief": str(bundle["brief"]),
                "evidence_pack": str(bundle["evidence_pack"]),
                "claim_map": str(bundle["claim_map"]),
                "draft": str(bundle["draft"]),
                "review": str(bundle["review"]),
                "final_markdown": str(bundle["final_markdown"]),
                "checklist": str(bundle["checklist"]),
                "docx": str(bundle["docx"]),
            },
            "related_context": [str(path) for path in related_context],
        },
    )
    print(f"Saved final message to {out_file}")
    print(f"Saved run manifest to {manifest_file}")
    return 0


def assemble_thesis(root_dir: Path, work_id: str | None) -> int:
    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id)
    if not work.thesis:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает thesis lane.")

    work.thesis.full_draft_path.parent.mkdir(parents=True, exist_ok=True)
    parts = [f"<!-- Generated by scripts/assemble_thesis.sh for {work.slug} on {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')} -->", ""]
    for section in work.thesis.section_order:
        if section.exists():
            parts.append(section.read_text(encoding="utf-8").rstrip())
            parts.append("")
    work.thesis.full_draft_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    print(f"Assembled {work.thesis.full_draft_path}")
    return 0


def export_thesis_docx(root_dir: Path, work_id: str | None) -> int:
    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id)
    if not work.thesis:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает thesis lane.")

    assemble_thesis(root_dir, work.slug)
    work.thesis.export_docx_path.parent.mkdir(parents=True, exist_ok=True)
    _run_pandoc(work.thesis.full_draft_path, work.thesis.export_docx_path)
    print(f"Exported {work.thesis.export_docx_path}")
    return 0


def export_article_docx(root_dir: Path, raw_input: str, raw_output: str | None, work_id: str | None) -> int:
    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id, target=raw_input)

    input_rel = normalize_target_path_for_export(workspace, work, raw_input)
    input_path = workspace.root_dir / input_rel
    if raw_output:
        output_path = _resolve_path(root_dir, raw_output)
    else:
        output_path = _default_article_docx_path(work, input_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_pandoc(input_path, output_path)
    print(f"Exported {output_path}")
    return 0


def normalize_target_path_for_export(workspace: WorkspaceConfig, work: WorkConfig, raw_input: str) -> str:
    raw_path = Path(raw_input).expanduser()
    candidates = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.append(workspace.root_dir / raw_path)
        candidates.append(work.work_dir / raw_path)
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            continue
        return resolved.relative_to(workspace.root_dir).as_posix()
    raise WorkspaceConfigError(f"Input markdown not found: {raw_input}")


def _build_thesis_prompt(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    preset: str,
    target_path: Path,
    target_rel: str,
    target_state: str,
    use_search: bool,
    related_context: list[Path],
    review_path: Path | None,
    sync_hint_path: Path | None,
    notes_content: str,
) -> str:
    search_state = "enabled by launcher" if use_search else "disabled by launcher"
    nearby_context = _format_paths_block(related_context)
    review_trace = f"- Preferred review artifact path: {review_path}" if review_path else "- No dedicated review artifact path was precomputed for this run."
    sync_trace = f"- Preferred sync checkpoint path: {sync_hint_path}" if sync_hint_path else "- No sync checkpoint path was precomputed for this run."

    prompts = {
        "full-cycle": f"""Use $thesis-workflow-orchestrator to handle this thesis task end-to-end in {workspace.root_dir}.

Active work:
- Work ID: {work.slug}
- Work title: {work.title}
- Work root: {work.work_dir}
- Work canon: {work.work_canon_path}
- Work config: {work.work_dir / 'work.toml'}

Target artifact: {target_path}
Target path (relative): {target_rel}
Target state: {target_state}
Web search: {search_state}

Nearby context candidates:
{nearby_context}

Execution rules:
- Open AGENTS.md, workspace.toml, the active work's work.toml, work-canon.md, and meta/master-protocol.md before editing.
- Use the appropriate internal chain across structure, research, verification, drafting, citations, criticism, and style.
- Write canonical thesis text only inside the active work bundle under works/{work.slug}/thesis/.
- For dynamic legal material, verify against up-to-date official or primary sources and use web search when needed.
- If you safely skip a workflow step, record the reason in a sync artifact inside the active work.
- If you update a manuscript section, rebuild with scripts/assemble_thesis.sh --work {work.slug}.
- If the task explicitly asks for Word output or reaches a polished section checkpoint, export DOCX with scripts/export_docx.sh --work {work.slug}.
- Do not optimize for detector bypass. Optimize for independent analysis, reliable sourcing, and natural academic prose.

Operational trace:
{sync_trace}
{review_trace}

Additional notes:
{notes_content}

Deliverable:
- Make the changes directly in files.
- Update the work-local sync/ if the run produces a meaningful checkpoint.
- End with a concise summary of changed files, verification performed, and remaining risks.""",
        "source-pack": f"""Use $thesis-research-synthesizer and $thesis-source-verifier for this thesis source-package task in the active work `{work.slug}`.

Target source package: {target_path}
Target path (relative): {target_rel}
Target state: {target_state}
Web search: {search_state}

Nearby context candidates:
{nearby_context}

Execution rules:
- Build or update the package using templates/source-package-passport.md.
- Prefer primary and official sources for law, case law, regulator guidance, and statistics.
- Record verification dates for dynamic materials.
- Mark what is verified, what still needs re-checking, and what remains analytical rather than factual.
- Keep the package compact and thesis-oriented rather than encyclopedic.

Operational trace:
{sync_trace}

Additional notes:
{notes_content}

Deliverable:
- Update the target package directly.
- Update the work-local sync/ if the package meaningfully changes the working baseline.
- End with a concise summary of sources added, sources verified, and gaps that still remain.""",
        "verify": f"""Use $thesis-source-verifier and $thesis-citation-checker for this verification pass in the active work `{work.slug}`.

Target file: {target_path}
Target path (relative): {target_rel}
Target state: {target_state}
Web search: {search_state}

Nearby context candidates:
{nearby_context}

Execution rules:
- Check significant legal, factual, and statistical claims for source support.
- For dynamic materials, verify against current official or primary sources and use web search when needed.
- Narrow or mark unsafe claims instead of leaving them overstated.
- Strengthen citations or footnote hygiene where appropriate.
- Do not do a broad stylistic rewrite unless a wording change is necessary to restore accuracy.

Operational trace:
{sync_trace}
{review_trace}

Additional notes:
{notes_content}

Deliverable:
- Update the target file if factual or citation fixes are needed.
- If verification materially changes work assumptions, update the work-local sync/.
- End with a concise summary of what was verified, what was corrected, and what still needs follow-up.""",
        "write-section": f"""Use $thesis-draft-writer, $thesis-source-verifier, and $thesis-citation-checker to draft or expand this thesis section in the active work `{work.slug}`.

Target section: {target_path}
Target path (relative): {target_rel}
Target state: {target_state}
Web search: {search_state}

Nearby context candidates:
{nearby_context}

Execution rules:
- Open the relevant brief, source packages, workspace docs, and work canon before writing.
- Draft only from verified sources or clearly marked analytical conclusions.
- Keep the voice academic, specific, and legally grounded.
- Add or maintain Markdown footnotes where source support is already pinned.
- If you safely skip a workflow step, record the reason in the work-local sync/.
- If the target is inside the manuscript sections, rebuild the manuscript after changes.

Operational trace:
{sync_trace}
{review_trace}

Additional notes:
{notes_content}

Deliverable:
- Update the section directly.
- Update the work-local sync/ if the section reaches a meaningful checkpoint.
- End with a concise summary of what was written, which sources were relied on, and what remains unverified or incomplete.""",
        "review-section": f"""Use $thesis-argument-critic and $thesis-citation-checker to review this thesis section in the active work `{work.slug}`.

Target section: {target_path}
Target path (relative): {target_rel}
Target state: {target_state}
Web search: {search_state}

Nearby context candidates:
{nearby_context}

Execution rules:
- Review the section for logic gaps, overclaims, repetition, weak transitions, and citation issues.
- Create or update the review artifact exactly here: {review_path}
- Use templates/chapter-review-sheet.md as the review structure.
- Keep the primary output findings-first.
- Do not rewrite the manuscript broadly; only make trivial citation-hygiene fixes if they are obvious and safe.

Operational trace:
{sync_trace}

Additional notes:
{notes_content}

Deliverable:
- Update or create {review_path}
- Update the work-local sync/ if the review changes priorities or safely skips any expected check.
- End with the key findings first, then a brief note on any small fixes made.""",
        "style-pass": f"""Use $thesis-style-editor for a final style refinement pass on this checked thesis text in the active work `{work.slug}`.

Target file: {target_path}
Target path (relative): {target_rel}
Target state: {target_state}
Web search: {search_state}

Nearby context candidates:
{nearby_context}

Execution rules:
- Improve natural academic Russian, paragraph rhythm, specificity, and authorial voice.
- Do not change the substantive meaning of claims unless a tiny narrowing is needed for credibility.
- Do not optimize for detector bypass or mechanical uniqueness.
- Remove stock transitions and machine-flat phrasing where possible.
- If the target is inside manuscript sections, rebuild the manuscript after changes.

Operational trace:
{sync_trace}
{review_trace}

Additional notes:
{notes_content}

Deliverable:
- Update the target file directly.
- End with a concise summary of stylistic improvements and any residual sections that still sound too generic.""",
    }
    return prompts[preset]


def _build_article_prompt(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    profile_id: str,
    profile_path: Path,
    use_search: bool,
    topic: str | None,
    input_brief_path: Path | None,
    bundle: dict[str, Path],
    related_context: list[Path],
    notes_content: str,
) -> str:
    search_state = "enabled by launcher" if use_search else "disabled by launcher"
    input_block = f"Input brief source: {input_brief_path}" if input_brief_path else f"Input topic: {topic}"
    return f"""Use $academic-workflow-orchestrator to run a full legal-academic article workflow in {workspace.root_dir}.

Active work:
- Work ID: {work.slug}
- Work title: {work.title}
- Work root: {work.work_dir}
- Work canon: {work.work_canon_path}
- Work config: {work.work_dir / 'work.toml'}

Article lane:
- Work only inside works/{work.slug}/articles/ and the work-specific output paths.
- Never write article artifacts into thesis manuscript sections.

Execution context:
{input_block}
Publication profile: {profile_id}
Profile file: {profile_path}
Web search: {search_state}
Relevant raw standards directory: {workspace.root_dir / 'meta' / 'standards' / 'raw'}

Managed article bundle paths:
{_format_bundle_block(bundle)}

Nearby context candidates:
{_format_paths_block(related_context)}

Workflow requirements:
- Open README.md, AGENTS.md, workspace.toml, the active work's work.toml, work-canon.md, meta/master-protocol.md, the active profile, and the article templates before editing.
- Start with $academic-intake and normalize the request into the managed brief path.
- Then use $academic-source-acquirer, $academic-source-verifier, and $academic-evidence-cartographer before serious drafting.
- For law, case law, regulator guidance, and statistics, final authority must be official or primary.
- Secondary literature is interpretive support, not a substitute for primary verification.
- Proprietary legal databases and aggregators may be used only as navigational support.
- Build or update the evidence pack and claim map so each significant claim has an evidence trace or an explicit analytical status.
- Draft with $academic-draft-writer only from verified support or clearly marked analytical conclusions.
- Run $academic-citation-checker, $academic-counterargument-critic, and $academic-submission-evaluator before finalization.
- If blockers remain, use $academic-repair-orchestrator and do not overstate readiness.
- Repair logic must stay finite. If strong primary gaps remain, downgrade to `strong-draft-with-blockers`.
- Finish with $academic-finalizer: produce final Markdown, checklist, and DOCX via scripts/export_academic_docx.sh --work {work.slug}.
- If relevant official raw formatting standards are missing or conflicting, reflect that as a blocker in the checklist and do not overstate formal submission readiness.

Additional notes:
{notes_content}

Deliverable:
- Update the managed article bundle directly.
- End with the explicit status `submission-ready`, `strong-draft`, or `strong-draft-with-blockers`.
- Summarize changed files, verification performed, exported outputs, and remaining blockers."""


def _build_review_prompt(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    profile_id: str,
    profile_path: Path,
    use_search: bool,
    target_path: Path | None,
    target_rel: str | None,
    bundle: dict[str, Path],
    related_context: list[Path],
    notes_content: str,
) -> str:
    search_state = "enabled by launcher" if use_search else "disabled by launcher"
    return f"""Use $academic-submission-evaluator, $academic-counterargument-critic, and $academic-citation-checker to review this legal-academic article bundle in {workspace.root_dir}.

Active work:
- Work ID: {work.slug}
- Work title: {work.title}
- Work canon: {work.work_canon_path}

Target file: {target_path}
Target path (relative): {target_rel}
Publication profile: {profile_id}
Profile file: {profile_path}
Web search: {search_state}

Managed article bundle paths:
{_format_bundle_block(bundle)}

Nearby context candidates:
{_format_paths_block(related_context)}

Execution rules:
- Treat this as an article-lane review scoped to the active work.
- Review source integrity, primary support, dynamic materials, counterarguments, composition, citations, and checklist blockers.
- Use templates/article-review-sheet.md and update the review file exactly here: {bundle['review']}
- Verify dynamic legal material against current official or primary sources when needed.
- Output a findings-first review with the verdict `submission-ready`, `strong-draft`, or `strong-draft-with-blockers`.
- Do not broadly rewrite the target file; only make tiny safe citation or factual fixes if they are obvious and necessary.

Additional notes:
{notes_content}

Deliverable:
- Update or create {bundle['review']}
- End with the key findings first, then the explicit verdict and next repair priorities."""


def _build_repair_prompt(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    profile_id: str,
    profile_path: Path,
    use_search: bool,
    target_path: Path | None,
    target_rel: str | None,
    bundle: dict[str, Path],
    related_context: list[Path],
    notes_content: str,
) -> str:
    search_state = "enabled by launcher" if use_search else "disabled by launcher"
    return f"""Use $academic-repair-orchestrator, $academic-source-verifier, $academic-citation-checker, $academic-submission-evaluator, and $academic-finalizer to repair this legal-academic article bundle in {workspace.root_dir}.

Active work:
- Work ID: {work.slug}
- Work title: {work.title}
- Work canon: {work.work_canon_path}

Repair input: {target_path}
Repair input (relative): {target_rel}
Publication profile: {profile_id}
Profile file: {profile_path}
Web search: {search_state}

Managed article bundle paths:
{_format_bundle_block(bundle)}

Nearby context candidates:
{_format_paths_block(related_context)}

Execution rules:
- Prioritize primary-source blockers, unsupported claims, and missing caveats before style or polish.
- Use the companion review file if it exists: {bundle['review']}
- Keep the repair inside article-lane artifacts only.
- Do not hide unresolved blockers behind nicer prose.
- Re-run evaluator logic before finalization.
- If relevant raw formatting standards are still missing or conflicting, preserve that blocker in the checklist.
- Finish by updating the active draft or final markdown, the checklist, and DOCX export when justified.
- If blockers remain after reasonable repair, keep or downgrade the status to `strong-draft-with-blockers`.

Additional notes:
{notes_content}

Deliverable:
- Update the relevant article bundle files directly.
- End with the explicit post-repair status, changed files, and remaining blockers."""


def _thesis_related_context(workspace: WorkspaceConfig, work: WorkConfig, target_path: Path) -> list[Path]:
    assert work.thesis is not None
    paths: list[Path] = [
        workspace.root_dir / "AGENTS.md",
        workspace.root_dir / "README.md",
        workspace.root_dir / "workspace.toml",
        workspace.root_dir / "meta" / "master-protocol.md",
        work.work_dir / "work.toml",
        work.work_canon_path,
        work.thesis.paths.root_dir / "README.md",
        work.thesis.manuscript_dir / "README.md",
        workspace.root_dir / "templates" / "source-package-passport.md",
        workspace.root_dir / "templates" / "chapter-brief.md",
        workspace.root_dir / "templates" / "chapter-review-sheet.md",
        workspace.root_dir / "templates" / "chat-sync.md",
        target_path,
    ]
    keywords = _target_keywords(target_path)
    for directory in (
        work.thesis.chapters_dir,
        work.thesis.sources_dir,
        work.thesis.manuscript_sections_dir,
        work.thesis.reviews_dir,
        work.thesis.sync_dir,
    ):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            if path == target_path or path.name == "README.md":
                continue
            if _matches_keywords(path, keywords):
                paths.append(path)
    return _dedupe_existing(paths)


def _article_related_context(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    profile_path: Path,
    input_brief_path: Path | None,
    target_path: Path | None,
    bundle: dict[str, Path],
) -> list[Path]:
    assert work.article is not None
    paths: list[Path] = [
        workspace.root_dir / "AGENTS.md",
        workspace.root_dir / "README.md",
        workspace.root_dir / "workspace.toml",
        workspace.root_dir / "meta" / "master-protocol.md",
        workspace.root_dir / "meta" / "standards" / "README.md",
        workspace.root_dir / "meta" / "standards" / "raw" / "README.md",
        profile_path,
        work.work_dir / "work.toml",
        work.work_canon_path,
        work.article.paths.root_dir / "README.md",
        workspace.root_dir / "templates" / "article-brief.md",
        workspace.root_dir / "templates" / "evidence-pack.md",
        workspace.root_dir / "templates" / "claim-map.md",
        workspace.root_dir / "templates" / "article-review-sheet.md",
        workspace.root_dir / "templates" / "submission-checklist.md",
    ]
    if input_brief_path:
        paths.append(input_brief_path)
    if target_path:
        paths.append(target_path)
    paths.extend(bundle.values())
    return _dedupe_existing(paths)


def _print_thesis_dry_run(
    work: WorkConfig,
    preset: str,
    target_path: Path,
    target_rel: str,
    target_state: str,
    use_search: bool,
    review_path: Path | None,
    sync_hint_path: Path | None,
    model: str | None,
    related_context: list[Path],
    prompt: str,
) -> None:
    print(f"Work: {work.slug}")
    print(f"Preset: {preset}")
    print(f"Target: {target_path}")
    print(f"Target (relative): {target_rel}")
    print(f"Target state: {target_state}")
    print(f"Search enabled: {'yes' if use_search else 'no'}")
    if review_path:
        print(f"Expected review file: {review_path}")
    if sync_hint_path:
        print(f"Sync hint file: {sync_hint_path}")
    if model:
        print(f"Model: {model}")
    print(f"Related context:\n{_format_paths_block(related_context)}")
    print()
    print(prompt)


def _print_academic_dry_run(
    work: WorkConfig,
    workflow: str,
    profile_id: str,
    use_search: bool,
    topic: str | None,
    input_brief_path: Path | None,
    target_path: Path | None,
    target_rel: str | None,
    article_slug: str,
    model: str | None,
    bundle: dict[str, Path],
    related_context: list[Path],
    prompt: str,
) -> None:
    print(f"Work: {work.slug}")
    print(f"Command: {workflow}")
    print(f"Profile: {profile_id}")
    print(f"Search enabled: {'yes' if use_search else 'no'}")
    if topic:
        print(f"Topic: {topic}")
    if input_brief_path:
        print(f"Input brief: {input_brief_path}")
    if target_path:
        print(f"Target: {target_path}")
    if target_rel:
        print(f"Target (relative): {target_rel}")
    print(f"Article slug: {article_slug}")
    if model:
        print(f"Model: {model}")
    print(f"Managed bundle paths:\n{_format_bundle_block(bundle)}")
    print(f"Related context:\n{_format_paths_block(related_context)}")
    print()
    print(prompt)


def _resolve_profile_id(workspace: WorkspaceConfig, work: WorkConfig, raw_profile: str | None) -> str:
    if raw_profile:
        return raw_profile
    if work.article_profile:
        return work.article_profile
    return workspace.default_profiles.get("article", "ru-law-article-v1")


def _resolve_profile_path(root_dir: Path, profile_id: str) -> Path:
    profile_path = root_dir / "meta" / "standards" / "normalized" / f"{profile_id}.md"
    if not profile_path.exists():
        raise WorkspaceConfigError(f"Unknown academic profile: {profile_id}\nExpected file: {profile_path}")
    return profile_path


def _default_article_docx_path(work: WorkConfig, input_path: Path) -> Path:
    if work.article:
        stem = input_path.stem[:-10] if input_path.stem.endswith("-checklist") else input_path.stem
        return work.article.paths.output_docx_dir / f"{stem}.docx"
    return work.work_dir / f"{input_path.stem}.docx"


def _resolve_search(override: bool | None, default_value: bool) -> bool:
    if override is None:
        return default_value
    return override


def _run_codex(root_dir: Path, prompt: str, out_file: Path, use_search: bool, model: str | None) -> None:
    codex_bin = os.environ.get("CODEX_BIN", "codex")
    cmd = [codex_bin]
    if use_search:
        cmd.append("--search")
    cmd.extend(["exec", "-C", str(root_dir), "--skip-git-repo-check", "--full-auto", "-o", str(out_file)])
    chosen_model = model or os.environ.get("CODEX_MODEL")
    if chosen_model:
        cmd.extend(["-m", chosen_model])
    subprocess.run(cmd + ["-"], input=prompt, text=True, check=True)


def _run_pandoc(input_md: Path, output_docx: Path) -> None:
    subprocess.run(
        [
            "pandoc",
            str(input_md),
            "--from",
            "markdown+footnotes",
            "--to",
            "docx",
            "--output",
            str(output_docx),
        ],
        check=True,
    )


def _read_notes(root_dir: Path, raw: str | None) -> str:
    if not raw:
        return "None provided."
    direct = Path(raw).expanduser()
    if direct.is_file():
        return direct.read_text(encoding="utf-8")
    rooted = (root_dir / raw).resolve()
    if rooted.is_file():
        return rooted.read_text(encoding="utf-8")
    return raw


def _slugify_text(raw: str | None) -> str:
    base = (raw or "").strip().lower()
    slug = re.sub(r"[^\w]+", "-", base, flags=re.UNICODE).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:80] or "article-topic"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _format_paths_block(paths: list[Path]) -> str:
    if not paths:
        return "- none detected"
    return "\n".join(f"- {path}" for path in paths)


def _format_bundle_block(bundle: dict[str, Path]) -> str:
    return "\n".join(
        [
            f"- Brief: {bundle['brief']}",
            f"- Evidence pack: {bundle['evidence_pack']}",
            f"- Claim map: {bundle['claim_map']}",
            f"- Draft: {bundle['draft']}",
            f"- Review: {bundle['review']}",
            f"- Final markdown: {bundle['final_markdown']}",
            f"- Final checklist: {bundle['checklist']}",
            f"- Expected DOCX: {bundle['docx']}",
        ]
    )


def _dedupe_existing(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        candidate = path.resolve()
        if not candidate.exists() or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def _target_keywords(target_path: Path) -> set[str]:
    stem = target_path.stem.lower()
    keywords = {stem}
    match = re.search(r"(chapter-\d+)", stem)
    if match:
        keywords.add(match.group(1))
    for token in ("introduction", "conclusion", "bibliography", "title"):
        if token in stem:
            keywords.add(token)
    for token in re.split(r"[^a-z0-9]+", stem):
        if token and token not in {"chapter", "section", "sections", "brief", "review"}:
            keywords.add(token)
    return keywords


def _matches_keywords(path: Path, keywords: set[str]) -> bool:
    stem = path.stem.lower()
    return any(keyword in stem for keyword in keywords)


def _sync_path_for_target(work: WorkConfig, preset: str, target_rel: str) -> Path | None:
    if not work.thesis:
        return None
    base_name = Path(target_rel).stem
    return work.thesis.sync_dir / f"{datetime.now().strftime('%Y%m%d')}-{preset}-{base_name}.md"


def _resolve_path(root_dir: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (root_dir / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
