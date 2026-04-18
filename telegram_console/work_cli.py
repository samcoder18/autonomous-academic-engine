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

from .action_specs import (
    ExecutionContract,
    build_article_execution_contract,
    build_thesis_execution_contract,
)
from .article_bundle_state import (
    article_bundle_manifest_path,
    build_article_bundle_state,
    load_article_bundle_state,
    write_article_bundle_state,
)
from .standards import (
    StandardProfileResolution,
    format_profile_resolution_lines,
    format_registry_overview_lines,
    resolve_standard_profile,
    resolve_status_profile,
    sync_standard_profile,
)
from .workspace import (
    TargetResolution,
    WorkspaceConfig,
    WorkspaceConfigError,
    WorkConfig,
    article_bundle_paths,
    derive_review_path,
    list_targets_for_action,
    load_work_config,
    load_workspace_config,
    relative_to_workspace,
    resolve_target_for_action,
    resolve_target_path,
    resolve_work_config,
    resolve_work_selection,
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


def main(argv: list[str] | None = None, *, root_dir: str | Path | None = None) -> int:
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

    standards_intake_parser = subparsers.add_parser("standards-intake")
    standards_intake_parser.add_argument("profile_id")

    standards_refresh_parser = subparsers.add_parser("standards-refresh")
    standards_refresh_parser.add_argument("profile_id")

    standards_status_parser = subparsers.add_parser("standards-status")
    standards_status_parser.add_argument("profile_id", nargs="?")

    args = parser.parse_args(argv)
    root_path = Path(root_dir).expanduser().resolve() if root_dir is not None else Path(__file__).resolve().parents[1]

    try:
        if args.command == "launch-thesis":
            return launch_thesis(root_path, args)
        if args.command == "launch-academic":
            return launch_academic(root_path, args)
        if args.command == "assemble-thesis":
            return assemble_thesis(root_path, args.work_id)
        if args.command == "export-thesis-docx":
            return export_thesis_docx(root_path, args.work_id)
        if args.command == "export-article-docx":
            return export_article_docx(root_path, args.input_md, args.output_docx, args.work_id)
        if args.command == "standards-intake":
            return standards_intake(root_path, args.profile_id)
        if args.command == "standards-refresh":
            return standards_refresh(root_path, args.profile_id)
        if args.command == "standards-status":
            return standards_status(root_path, args.profile_id)
    except WorkspaceConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


def launch_thesis(root_dir: Path, args: Any) -> int:
    workspace = load_workspace_config(root_dir)
    work_selection = resolve_work_selection(workspace, work_id=args.work_id, target=args.target)
    work = work_selection.work
    if not work.thesis:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает thesis lane.")
    profile = resolve_standard_profile(root_dir, workspace, work, lane="thesis", requested_profile_id=None)

    target_resolution = resolve_target_for_action(
        workspace,
        work,
        "thesis",
        args.preset,
        args.target,
        work_source=work_selection.source,
    )
    target_rel = target_resolution.normalized_path
    if target_rel == relative_to_workspace(workspace, work.thesis.full_draft_path):
        raise WorkspaceConfigError("Use manuscript/sections as the editable target, not the assembled full draft.")

    target_path = workspace.root_dir / target_rel
    target_state = "existing" if target_path.exists() else "missing"
    use_search = _resolve_search(args.search_override, args.preset in {"full-cycle", "source-pack", "verify", "write-section"})
    review_path = derive_review_path(workspace, work, target_rel)
    sync_hint_path = _sync_path_for_target(work, args.preset, target_rel)
    related_context = _thesis_related_context(workspace, work, target_path, profile)
    notes_content = _read_notes(root_dir, args.notes)
    contract = build_thesis_execution_contract(
        work=work,
        profile=profile,
        action=args.preset,
        target_path=target_path,
        target_rel=target_rel,
        related_context=related_context,
        review_path=review_path,
        sync_hint_path=sync_hint_path,
    )
    prompt = _build_thesis_prompt(
        workspace,
        work,
        profile,
        contract,
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
            profile,
            contract,
            target_path,
            target_rel,
            target_state,
            use_search,
            review_path,
            sync_hint_path,
            args.model,
            target_resolution,
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
            "target_resolution": target_resolution.to_dict(),
            "requested_profile_id": profile.requested_profile_id,
            "resolved_profile_id": profile.resolved_profile_id,
            "fallback_profile_id": profile.fallback_profile_id,
            "profile_raw_dir": str(profile.raw_dir),
            "profile_conflict_flag": profile.conflict_flag,
            "profile_status": profile.profile_status,
            "search_enabled": use_search,
            "model": args.model or None,
            "root_dir": str(root_dir),
            "output_file": str(out_file),
            "expected_review_file": str(review_path) if review_path else None,
            "sync_hint_file": str(sync_hint_path) if sync_hint_path else None,
            "related_context": [str(path) for path in related_context],
            "execution_contract": contract.to_dict(),
        },
    )
    print(f"Saved final message to {out_file}")
    print(f"Saved run manifest to {manifest_file}")
    return 0


def launch_academic(root_dir: Path, args: Any) -> int:
    workspace = load_workspace_config(root_dir)

    target_hint = args.brief or args.target
    work_selection = resolve_work_selection(workspace, work_id=args.work_id, target=target_hint)
    work = work_selection.work
    if not work.article:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает article lane.")

    profile = resolve_standard_profile(
        root_dir,
        workspace,
        work,
        lane="article",
        requested_profile_id=args.profile,
    )
    use_search = _resolve_search(args.search_override, True)
    notes_content = _read_notes(root_dir, args.notes)

    topic: str | None = None
    input_brief_path: Path | None = None
    target_path: Path | None = None
    target_rel: str | None = None
    target_resolution: TargetResolution | None = None

    if args.workflow == "article":
        if bool(args.topic) == bool(args.brief):
            raise WorkspaceConfigError("Для команды article нужно указать ровно один из аргументов: --topic или --brief.")
        if args.brief:
            target_resolution = resolve_target_for_action(
                workspace,
                work,
                "article",
                "article-brief",
                args.brief,
                work_source=work_selection.source,
            )
            target_rel = target_resolution.normalized_path
            input_brief_path = workspace.root_dir / target_rel
        else:
            topic = args.topic.strip()
            if not topic:
                raise WorkspaceConfigError("Тема статьи не может быть пустой.")
    else:
        if not args.target:
            raise WorkspaceConfigError(f"Команда `{args.workflow}` ожидает target-файл.")
        target_resolution = resolve_target_for_action(
            workspace,
            work,
            "article",
            args.workflow,
            args.target,
            work_source=work_selection.source,
        )
        target_rel = target_resolution.normalized_path
        target_path = workspace.root_dir / target_rel

    article_slug = _slugify_text(
        (Path(target_rel).stem if target_rel else None)
        or (input_brief_path.stem if input_brief_path else None)
        or topic
        or "article-topic"
    )
    bundle = article_bundle_paths(work, article_slug)
    bundle_state_path = article_bundle_manifest_path(work, article_slug)
    related_context = _article_related_context(
        workspace,
        work,
        profile,
        input_brief_path,
        target_path,
        bundle,
        bundle_state_path,
    )
    contract = build_article_execution_contract(
        work=work,
        profile=profile,
        action=args.workflow,
        related_context=related_context,
        bundle=bundle,
        topic=topic,
        input_brief_path=input_brief_path,
        target_path=target_path,
        target_rel=target_rel,
    )

    if args.workflow == "article":
        prompt = _build_article_prompt(
            workspace,
            work,
            profile,
            contract,
            use_search,
            topic,
            input_brief_path,
            bundle,
            bundle_state_path,
            related_context,
            notes_content,
        )
    elif args.workflow == "review":
        prompt = _build_review_prompt(
            workspace,
            work,
            profile,
            contract,
            use_search,
            target_path,
            target_rel,
            bundle,
            bundle_state_path,
            related_context,
            notes_content,
        )
    else:
        prompt = _build_repair_prompt(
            workspace,
            work,
            profile,
            contract,
            use_search,
            target_path,
            target_rel,
            bundle,
            bundle_state_path,
            related_context,
            notes_content,
        )

    if args.dry_run:
        _print_academic_dry_run(
            work,
            contract,
            profile,
            use_search,
            topic,
            input_brief_path,
            target_path,
            target_rel,
            article_slug,
            args.model,
            target_resolution,
            bundle,
            bundle_state_path,
            related_context,
            prompt,
        )
        return 0

    output_dir = work.article.paths.output_runs_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = output_dir / f"{timestamp}-{args.workflow}-{article_slug}.md"
    manifest_file = output_dir / f"{timestamp}-{args.workflow}-{article_slug}.meta.json"
    input_brief_rel = relative_to_workspace(workspace, input_brief_path) if input_brief_path else None
    target_rel_value = relative_to_workspace(workspace, target_path) if target_path else None
    manifest_payload = {
        "timestamp": timestamp,
        "command": args.workflow,
        "work_id": work.slug,
        "work_title": work.title,
        "profile_id": profile.resolved_profile_id,
        "requested_profile_id": profile.requested_profile_id,
        "resolved_profile_id": profile.resolved_profile_id,
        "fallback_profile_id": profile.fallback_profile_id,
        "profile_raw_dir": str(profile.raw_dir),
        "profile_conflict_flag": profile.conflict_flag,
        "profile_status": profile.profile_status,
        "search_enabled": use_search,
        "topic": topic,
        "input_brief": input_brief_rel,
        "target_path": target_rel_value,
        "target_resolution": target_resolution.to_dict() if target_resolution else None,
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
            "state_manifest": str(bundle_state_path),
        },
        "related_context": [str(path) for path in related_context],
        "execution_contract": contract.to_dict(),
    }
    initial_bundle_state = build_article_bundle_state(
        work_id=work.slug,
        article_slug=article_slug,
        bundle=bundle,
        profile_id=profile.resolved_profile_id,
        last_action=args.workflow,
        last_run_status="started",
        latest_run_manifest=str(manifest_file),
        latest_output_file=str(out_file),
        execution_contract=contract.to_dict(),
        topic=topic,
        input_brief=input_brief_rel,
        target_path=target_rel_value,
        previous_state=load_article_bundle_state(bundle_state_path),
    )
    write_article_bundle_state(bundle_state_path, initial_bundle_state)
    try:
        _run_codex(root_dir, prompt, out_file, use_search, args.model)
        _write_json(manifest_file, manifest_payload)
        completed_bundle_state = build_article_bundle_state(
            work_id=work.slug,
            article_slug=article_slug,
            bundle=bundle,
            profile_id=profile.resolved_profile_id,
            last_action=args.workflow,
            last_run_status="succeeded",
            latest_run_manifest=str(manifest_file),
            latest_output_file=str(out_file),
            execution_contract=contract.to_dict(),
            topic=topic,
            input_brief=input_brief_rel,
            target_path=target_rel_value,
            previous_state=load_article_bundle_state(bundle_state_path),
        )
        write_article_bundle_state(bundle_state_path, completed_bundle_state)
    except Exception:
        failed_bundle_state = build_article_bundle_state(
            work_id=work.slug,
            article_slug=article_slug,
            bundle=bundle,
            profile_id=profile.resolved_profile_id,
            last_action=args.workflow,
            last_run_status="failed",
            latest_run_manifest=str(manifest_file),
            latest_output_file=str(out_file),
            execution_contract=contract.to_dict(),
            topic=topic,
            input_brief=input_brief_rel,
            target_path=target_rel_value,
            previous_state=load_article_bundle_state(bundle_state_path),
        )
        write_article_bundle_state(bundle_state_path, failed_bundle_state)
        raise
    print(f"Saved final message to {out_file}")
    print(f"Saved run manifest to {manifest_file}")
    print(f"Saved article bundle state to {bundle_state_path}")
    return 0


def standards_intake(root_dir: Path, profile_id: str) -> int:
    result = sync_standard_profile(root_dir, profile_id, force_refresh=False)
    lines = [
        f"Operation: {result.operation}",
        f"Downloaded sources: {result.downloaded_count}",
        f"Reused sources: {result.reused_count}",
        f"Failed sources: {result.failed_count}",
        f"Manifest path: {result.manifest_path}",
    ]
    lines.extend(format_profile_resolution_lines(result.resolution))
    print("\n".join(lines))
    return 0


def standards_refresh(root_dir: Path, profile_id: str) -> int:
    result = sync_standard_profile(root_dir, profile_id, force_refresh=True)
    lines = [
        f"Operation: {result.operation}",
        f"Downloaded sources: {result.downloaded_count}",
        f"Reused sources: {result.reused_count}",
        f"Failed sources: {result.failed_count}",
        f"Manifest path: {result.manifest_path}",
    ]
    lines.extend(format_profile_resolution_lines(result.resolution))
    print("\n".join(lines))
    return 0


def standards_status(root_dir: Path, profile_id: str | None) -> int:
    if not profile_id:
        print("\n".join(format_registry_overview_lines(root_dir)))
        return 0
    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace)
    resolution = resolve_status_profile(root_dir, profile_id, workspace=workspace, work=work)
    print("\n".join(format_profile_resolution_lines(resolution)))
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
    work_selection = resolve_work_selection(workspace, work_id=work_id, target=raw_input)
    work = work_selection.work

    input_rel = normalize_target_path_for_export(workspace, work, raw_input, work_source=work_selection.source)
    input_path = workspace.root_dir / input_rel
    if raw_output:
        output_path = _resolve_path(root_dir, raw_output)
    else:
        output_path = _default_article_docx_path(work, input_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_pandoc(input_path, output_path)
    print(f"Exported {output_path}")
    return 0


def normalize_target_path_for_export(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    raw_input: str,
    *,
    work_source: str = "explicit",
) -> str:
    try:
        return resolve_target_path(workspace, work, raw_input, work_source=work_source).normalized_path
    except WorkspaceConfigError as exc:
        raise WorkspaceConfigError(f"Input markdown not found: {raw_input}") from exc


def _build_thesis_prompt(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    profile: StandardProfileResolution,
    contract: ExecutionContract,
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
    profile_trace = _format_profile_trace(profile)
    action_intro = {
        "full-cycle": f"Use $thesis-workflow-orchestrator to handle this thesis task end-to-end in {workspace.root_dir}.",
        "source-pack": f"Use $thesis-research-synthesizer and $thesis-source-verifier for this thesis source-package task in the active work `{work.slug}`.",
        "verify": f"Use $thesis-source-verifier and $thesis-citation-checker for this verification pass in the active work `{work.slug}`.",
        "write-section": f"Use $thesis-draft-writer, $thesis-source-verifier, and $thesis-citation-checker to draft or expand this thesis section in the active work `{work.slug}`.",
        "review-section": f"Use $thesis-argument-critic and $thesis-citation-checker to review this thesis section in the active work `{work.slug}`.",
        "style-pass": f"Use $thesis-style-editor for a final style refinement pass on this checked thesis text in the active work `{work.slug}`.",
    }
    target_label = {
        "full-cycle": "Target artifact",
        "source-pack": "Target source package",
        "verify": "Target file",
        "write-section": "Target section",
        "review-section": "Target section",
        "style-pass": "Target file",
    }[contract.action]
    standards_block = f"Standards profile:\n{profile_trace}\n" if contract.action != "review-section" else ""
    return f"""{action_intro[contract.action]}

Active work:
- Work ID: {work.slug}
- Work title: {work.title}
- Work root: {work.work_dir}
- Work canon: {work.work_canon_path}
- Work config: {work.work_dir / 'work.toml'}

{target_label}: {target_path}
Target path (relative): {target_rel}
Target state: {target_state}
Web search: {search_state}
{standards_block}Nearby context candidates:
{nearby_context}

Execution contract:
{_format_execution_contract_block(contract)}

Execution rules:
{_format_string_bullets(contract.prompt_rules)}

Operational trace:
{sync_trace}
{review_trace}

Additional notes:
{notes_content}

Deliverable:
{_format_string_bullets(contract.deliverables)}"""


def _build_article_prompt(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    profile: StandardProfileResolution,
    contract: ExecutionContract,
    use_search: bool,
    topic: str | None,
    input_brief_path: Path | None,
    bundle: dict[str, Path],
    bundle_state_path: Path,
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
Publication profile: {profile.resolved_profile_id}
Profile file: {profile.normalized_path}
Web search: {search_state}
Relevant raw standards directory: {profile.raw_dir}
Profile trace:
{_format_profile_trace(profile)}

Managed article bundle paths:
{_format_bundle_block(bundle, bundle_state_path)}

Nearby context candidates:
{_format_paths_block(related_context)}

Execution contract:
{_format_execution_contract_block(contract)}

Workflow requirements:
{_format_string_bullets(contract.prompt_rules)}

Additional notes:
{notes_content}

Deliverable:
{_format_string_bullets(contract.deliverables)}"""


def _build_review_prompt(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    profile: StandardProfileResolution,
    contract: ExecutionContract,
    use_search: bool,
    target_path: Path | None,
    target_rel: str | None,
    bundle: dict[str, Path],
    bundle_state_path: Path,
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
Publication profile: {profile.resolved_profile_id}
Profile file: {profile.normalized_path}
Web search: {search_state}
Profile trace:
{_format_profile_trace(profile)}

Managed article bundle paths:
{_format_bundle_block(bundle, bundle_state_path)}

Nearby context candidates:
{_format_paths_block(related_context)}

Execution contract:
{_format_execution_contract_block(contract)}

Execution rules:
{_format_string_bullets(contract.prompt_rules)}

Additional notes:
{notes_content}

Deliverable:
{_format_string_bullets(contract.deliverables)}"""


def _build_repair_prompt(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    profile: StandardProfileResolution,
    contract: ExecutionContract,
    use_search: bool,
    target_path: Path | None,
    target_rel: str | None,
    bundle: dict[str, Path],
    bundle_state_path: Path,
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
Publication profile: {profile.resolved_profile_id}
Profile file: {profile.normalized_path}
Web search: {search_state}
Profile trace:
{_format_profile_trace(profile)}

Managed article bundle paths:
{_format_bundle_block(bundle, bundle_state_path)}

Nearby context candidates:
{_format_paths_block(related_context)}

Execution contract:
{_format_execution_contract_block(contract)}

Execution rules:
{_format_string_bullets(contract.prompt_rules)}

Additional notes:
{notes_content}

Deliverable:
{_format_string_bullets(contract.deliverables)}"""


def _thesis_related_context(
    workspace: WorkspaceConfig,
    work: WorkConfig,
    target_path: Path,
    profile: StandardProfileResolution,
) -> list[Path]:
    assert work.thesis is not None
    paths: list[Path] = [
        workspace.root_dir / "AGENTS.md",
        workspace.root_dir / "README.md",
        workspace.root_dir / "workspace.toml",
        workspace.root_dir / "meta" / "master-protocol.md",
        profile.normalized_path,
        profile.raw_manifest_path,
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
    profile: StandardProfileResolution,
    input_brief_path: Path | None,
    target_path: Path | None,
    bundle: dict[str, Path],
    bundle_state_path: Path,
) -> list[Path]:
    assert work.article is not None
    paths: list[Path] = [
        workspace.root_dir / "AGENTS.md",
        workspace.root_dir / "README.md",
        workspace.root_dir / "workspace.toml",
        workspace.root_dir / "meta" / "master-protocol.md",
        workspace.root_dir / "meta" / "standards" / "README.md",
        workspace.root_dir / "meta" / "standards" / "raw" / "README.md",
        profile.normalized_path,
        profile.raw_manifest_path,
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
    paths.append(bundle_state_path)
    paths.extend(bundle.values())
    return _dedupe_existing(paths)


def _print_thesis_dry_run(
    work: WorkConfig,
    profile: StandardProfileResolution,
    contract: ExecutionContract,
    target_path: Path,
    target_rel: str,
    target_state: str,
    use_search: bool,
    review_path: Path | None,
    sync_hint_path: Path | None,
    model: str | None,
    target_resolution: TargetResolution,
    related_context: list[Path],
    prompt: str,
) -> None:
    print(f"Work: {work.slug}")
    for line in format_profile_resolution_lines(profile):
        print(line)
    print(f"Preset: {contract.action}")
    print(f"Target: {target_path}")
    print(f"Target (relative): {target_rel}")
    print(f"Target state: {target_state}")
    print(f"Target resolution mode: {target_resolution.resolution_mode}")
    print(f"Target work source: {target_resolution.work_source}")
    if target_resolution.warning_message:
        print(f"Legacy target warning: {target_resolution.warning_message}")
    print(f"Search enabled: {'yes' if use_search else 'no'}")
    if review_path:
        print(f"Expected review file: {review_path}")
    if sync_hint_path:
        print(f"Sync hint file: {sync_hint_path}")
    if model:
        print(f"Model: {model}")
    print(f"Execution contract:\n{_format_execution_contract_block(contract)}")
    print(f"Related context:\n{_format_paths_block(related_context)}")
    print()
    print(prompt)


def _print_academic_dry_run(
    work: WorkConfig,
    contract: ExecutionContract,
    profile: StandardProfileResolution,
    use_search: bool,
    topic: str | None,
    input_brief_path: Path | None,
    target_path: Path | None,
    target_rel: str | None,
    article_slug: str,
    model: str | None,
    target_resolution: TargetResolution | None,
    bundle: dict[str, Path],
    bundle_state_path: Path,
    related_context: list[Path],
    prompt: str,
) -> None:
    print(f"Work: {work.slug}")
    print(f"Command: {contract.action}")
    for line in format_profile_resolution_lines(profile):
        print(line)
    print(f"Search enabled: {'yes' if use_search else 'no'}")
    if topic:
        print(f"Topic: {topic}")
    if input_brief_path:
        print(f"Input brief: {input_brief_path}")
    if target_path:
        print(f"Target: {target_path}")
    if target_rel:
        print(f"Target (relative): {target_rel}")
    if target_resolution:
        print(f"Target resolution mode: {target_resolution.resolution_mode}")
        print(f"Target work source: {target_resolution.work_source}")
        if target_resolution.warning_message:
            print(f"Legacy target warning: {target_resolution.warning_message}")
    print(f"Article slug: {article_slug}")
    print(f"Bundle state manifest: {bundle_state_path}")
    if model:
        print(f"Model: {model}")
    print(f"Execution contract:\n{_format_execution_contract_block(contract)}")
    print(f"Managed bundle paths:\n{_format_bundle_block(bundle, bundle_state_path)}")
    print(f"Related context:\n{_format_paths_block(related_context)}")
    print()
    print(prompt)


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


def _format_bundle_block(bundle: dict[str, Path], bundle_state_path: Path) -> str:
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
            f"- Bundle state manifest: {bundle_state_path}",
        ]
    )


def _format_execution_contract_block(contract: ExecutionContract) -> str:
    lines = [
        f"- Action: {contract.lane}/{contract.action}",
        f"- Title: {contract.title}",
        f"- Summary: {contract.summary}",
        f"- Target kind: {contract.target_kind}",
        f"- Target validation: {contract.target_validation}",
        f"- Required checkpoints: {', '.join(contract.required_checkpoints)}",
        f"- Terminal statuses: {', '.join(contract.terminal_statuses)}",
        (
            "- Repair policy: "
            f"eligible={'yes' if contract.repair_policy.eligible else 'no'}, "
            f"max_iterations={contract.repair_policy.max_iterations}, "
            f"safe_only={'yes' if contract.repair_policy.safe_only else 'no'}"
        ),
    ]
    if contract.required_context:
        lines.append("- Required context:")
        lines.extend(
            f"  - {artifact.name}: {artifact.path} [{artifact.requirement}]"
            for artifact in contract.required_context
        )
    if contract.allowed_write_scopes:
        lines.append("- Allowed writes:")
        lines.extend(
            f"  - {item.name}: {item.path}"
            for item in contract.allowed_write_scopes
        )
    if contract.required_outputs:
        lines.append("- Required outputs:")
        lines.extend(
            f"  - {artifact.name}: {artifact.path} [{artifact.requirement}]"
            for artifact in contract.required_outputs
        )
    if contract.quality_gates:
        lines.append("- Quality gates:")
        lines.extend(
            f"  - {gate.gate_id}: {gate.description}"
            for gate in contract.quality_gates
        )
    if contract.transitions:
        lines.append("- Transitions:")
        lines.extend(
            f"  - {item.from_phase} -> {item.to_phase}: {item.completion_signal}"
            for item in contract.transitions
        )
    return "\n".join(lines)


def _format_string_bullets(items: tuple[str, ...]) -> str:
    if not items:
        return "- none"
    return "\n".join(f"- {item}" for item in items)


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


def _format_profile_trace(profile: StandardProfileResolution) -> str:
    lines = [
        f"- Requested profile: {profile.requested_profile_id}",
        f"- Resolved profile: {profile.resolved_profile_id}",
        f"- Profile file: {profile.normalized_path}",
        f"- Raw directory: {profile.raw_dir}",
        f"- Raw status: {profile.raw_status}",
        f"- Official-only: {'yes' if profile.official_only else 'no'}",
        f"- Conflict flag: {'yes' if profile.conflict_flag else 'no'}",
    ]
    if profile.fallback_profile_id:
        lines.insert(2, f"- Fallback profile: {profile.fallback_profile_id}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
