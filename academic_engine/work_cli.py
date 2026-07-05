from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Any

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
from .autonomous_policy import AUTONOMOUS_MODES
from .engine_service import (
    CancelJobRequest,
    CreateWorkRequest,
    DispatchJobsRequest,
    EngineService,
    InspectJobRequest,
    ResumeJobRequest,
    RetryJobRequest,
    SubmitWorkflowJobRequest,
)
from .executors import ProviderExecutionError, build_executor_router, run_provider_smoke
from .job_queue import JobQueueError
from .orchestrator_exports import require_machine_gates_passed, require_submission_ready_workflow
from .orchestrator_support import WorkflowError
from .skill_source_map import audit_skill_source_map, sync_external_skill_sources
from .standards import (
    StandardProfileResolution,
    format_profile_resolution_lines,
    format_registry_overview_lines,
    resolve_standard_profile,
    resolve_status_profile,
    sync_standard_profile,
)
from .utils import resolve_executable
from .work_bootstrap import ALL_ARTIFACT_TYPES, WorkBootstrapError
from .work_cli_autonomous import handle_autonomous_cli
from .work_state import format_work_state_summary
from .workflow_engine import WorkflowEngine
from .workspace import (
    TargetResolution,
    WorkConfig,
    WorkspaceConfig,
    WorkspaceConfigError,
    article_bundle_paths,
    derive_review_path,
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
    "build-maps",
    "verify-claims",
    "counterargument-pass",
    "draft-author-position",
    "formal-artifacts",
)
ARTICLE_COMMANDS = ("article", "review", "repair", "finalize")


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
    thesis.add_argument("--workflow-id", dest="workflow_id", help="Preassigned workflow-run/v1 identifier.")

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
    academic.add_argument("--workflow-id", dest="workflow_id", help="Preassigned workflow-run/v1 identifier.")

    assemble = subparsers.add_parser("assemble-thesis")
    assemble.add_argument("--work", dest="work_id")

    export_thesis = subparsers.add_parser("export-thesis-docx")
    export_thesis.add_argument("--work", dest="work_id")

    vkr_front = subparsers.add_parser(
        "build-vkr-frontmatter",
        help="Generate title-page/abstract/keywords/task-sheet from thesis/metadata.toml.",
    )
    vkr_front.add_argument("--work", dest="work_id")

    dissertation_artifacts = subparsers.add_parser(
        "build-dissertation-artifacts",
        help="Generate dissertation author abstract and defense checklist from thesis/dissertation/metadata.toml.",
    )
    dissertation_artifacts.add_argument("--work", dest="work_id")

    one_shot = subparsers.add_parser(
        "one-shot-thesis",
        help="Run deterministic VKR gates and return machine-gates-passed or blocked.",
    )
    one_shot.add_argument("--work", dest="work_id")
    one_shot.add_argument(
        "--skip-docx",
        action="store_true",
        help="Legacy compatibility flag; strict mode still requires the DOCX conformance gate.",
    )
    one_shot.add_argument(
        "--corpus",
        dest="corpus_path",
        help="Path to the required originality corpus JSON. If omitted, the run is blocked.",
    )
    one_shot.add_argument(
        "--work-type",
        dest="work_type",
        help=(
            "Work-type profile (vkr-bachelor, vkr-specialist, master-thesis, "
            "dissertation-candidate, dissertation-doctor). Defaults to work.toml artifact_type."
        ),
    )

    one_shot_dissertation_parser = subparsers.add_parser(
        "one-shot-dissertation",
        help="Run dissertation-specific deterministic gates and return machine-gates-passed or blocked.",
    )
    one_shot_dissertation_parser.add_argument("--work", dest="work_id")
    one_shot_dissertation_parser.add_argument(
        "--skip-docx",
        action="store_true",
        help="Legacy compatibility flag; strict mode still requires the DOCX conformance gate.",
    )
    one_shot_dissertation_parser.add_argument(
        "--corpus",
        dest="corpus_path",
        help="Path to the required originality corpus JSON. If omitted, the run is blocked.",
    )
    one_shot_dissertation_parser.add_argument(
        "--work-type",
        dest="work_type",
        help="Override dissertation work-type profile. Defaults to work.toml artifact_type.",
    )

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

    work_status_parser = subparsers.add_parser("work-status")
    work_status_parser.add_argument("--work", dest="work_id")
    work_status_parser.add_argument("--json", action="store_true", dest="as_json")

    provider_smoke_parser = subparsers.add_parser("provider-smoke")
    provider_smoke_parser.add_argument("provider", choices=("openrouter",))

    runtime_index_parser = subparsers.add_parser("runtime-index")
    runtime_index_subparsers = runtime_index_parser.add_subparsers(dest="runtime_index_command", required=True)

    runtime_index_refresh = runtime_index_subparsers.add_parser("refresh")
    runtime_index_refresh.add_argument("--json", action="store_true", dest="as_json")

    runtime_index_status = runtime_index_subparsers.add_parser("status")
    runtime_index_status.add_argument("--work", dest="work_id")
    runtime_index_status.add_argument("--limit", type=int, default=20)
    runtime_index_status.add_argument("--json", action="store_true", dest="as_json")

    jobs_parser = subparsers.add_parser("jobs")
    jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command", required=True)

    jobs_submit = jobs_subparsers.add_parser("submit-workflow")
    jobs_submit.add_argument("--work", dest="work_id", required=True)
    jobs_submit.add_argument("--lane", required=True, choices=("thesis", "article"))
    jobs_submit.add_argument("--action", required=True)
    jobs_submit.add_argument("--target", dest="target_or_topic", required=True)
    jobs_submit.add_argument("--notes")
    jobs_submit.add_argument("--search", dest="search_override", action="store_const", const=True)
    jobs_submit.add_argument("--no-search", dest="search_override", action="store_const", const=False)
    jobs_submit.add_argument("--model", dest="model_override")
    jobs_submit.add_argument("--profile", dest="profile_override")
    jobs_submit.add_argument("--json", action="store_true", dest="as_json")

    jobs_list = jobs_subparsers.add_parser("list")
    jobs_list.add_argument("--work", dest="work_id")
    jobs_list.add_argument("--status")
    jobs_list.add_argument("--json", action="store_true", dest="as_json")

    jobs_status = jobs_subparsers.add_parser("status")
    jobs_status.add_argument("job_id")
    jobs_status.add_argument("--json", action="store_true", dest="as_json")

    jobs_cancel = jobs_subparsers.add_parser("cancel")
    jobs_cancel.add_argument("job_id")
    jobs_cancel.add_argument("--reason", default="operator-cancelled")
    jobs_cancel.add_argument("--json", action="store_true", dest="as_json")

    jobs_retry = jobs_subparsers.add_parser("retry")
    jobs_retry.add_argument("job_id")
    jobs_retry.add_argument("--json", action="store_true", dest="as_json")

    jobs_resume = jobs_subparsers.add_parser("resume")
    jobs_resume.add_argument("job_id")
    jobs_resume.add_argument("--json", action="store_true", dest="as_json")

    jobs_dispatch = jobs_subparsers.add_parser("dispatch")
    jobs_dispatch.add_argument("--limit", type=int)
    jobs_dispatch.add_argument("--json", action="store_true", dest="as_json")

    job_inspect = subparsers.add_parser("job-inspect")
    job_inspect.add_argument("job_id")
    job_inspect.add_argument("--json", action="store_true", dest="as_json")

    export_explain_parser = subparsers.add_parser("export-explain")
    export_explain_parser.add_argument("subject")
    export_explain_parser.add_argument("--work", dest="work_id")
    export_explain_parser.add_argument("--json", action="store_true", dest="as_json")

    work_parser = subparsers.add_parser("work")
    work_subparsers = work_parser.add_subparsers(dest="work_command", required=True)

    work_init_parser = work_subparsers.add_parser("init")
    work_init_parser.add_argument("slug")
    work_init_parser.add_argument(
        "--artifact-type",
        required=True,
        choices=sorted(ALL_ARTIFACT_TYPES),
        dest="artifact_type",
    )
    work_init_parser.add_argument("--title", required=True)
    work_init_parser.add_argument("--topic", default="")
    work_init_parser.add_argument("--language", default="ru")
    work_init_parser.add_argument(
        "--lanes",
        default=None,
        help="Comma-separated lanes (e.g. 'thesis' or 'thesis,article'). Defaults by artifact_type.",
    )
    work_init_parser.add_argument("--thesis-profile", dest="thesis_profile", default=None)
    work_init_parser.add_argument("--article-profile", dest="article_profile", default=None)
    work_init_parser.add_argument(
        "--set-default",
        dest="set_default",
        action="store_true",
        help="Replace default_work in workspace.toml with the new slug.",
    )
    work_init_parser.add_argument("--json", action="store_true", dest="as_json")

    skill_source_parser = subparsers.add_parser("skill-source-map")
    skill_source_subparsers = skill_source_parser.add_subparsers(dest="skill_source_command", required=True)

    skill_audit_parser = skill_source_subparsers.add_parser("audit")
    skill_audit_parser.add_argument("--skills-root")
    skill_audit_parser.add_argument("--json", action="store_true", dest="as_json")

    skill_sync_parser = skill_source_subparsers.add_parser("sync-external")
    skill_sync_parser.add_argument("--skills-root", required=True)
    skill_sync_parser.add_argument("--write", action="store_true")
    skill_sync_parser.add_argument("--json", action="store_true", dest="as_json")

    autonomous = subparsers.add_parser("autonomous")
    autonomous_subparsers = autonomous.add_subparsers(dest="autonomous_command", required=True)
    for autonomous_command in ("plan", "explain"):
        autonomous_parser = autonomous_subparsers.add_parser(autonomous_command)
        autonomous_parser.add_argument("--work", dest="work_id")
        autonomous_parser.add_argument("--mode", choices=AUTONOMOUS_MODES, default="autonomous-safe")
        autonomous_parser.add_argument("--max-steps", type=int, default=3)
        autonomous_parser.add_argument("--json", action="store_true", dest="as_json")

    autonomous_run_parser = autonomous_subparsers.add_parser("run")
    autonomous_run_parser.add_argument("--work", dest="work_id")
    autonomous_run_parser.add_argument("--mode", choices=AUTONOMOUS_MODES, default="autonomous-safe")
    autonomous_run_parser.add_argument("--max-steps", type=int, default=3)
    autonomous_run_parser.add_argument("--dry-run", action="store_true")
    autonomous_run_parser.add_argument("--execute", action="store_true")
    autonomous_run_parser.add_argument("--json", action="store_true", dest="as_json")

    autonomous_status_parser = autonomous_subparsers.add_parser("status")
    autonomous_status_parser.add_argument("--work", dest="work_id")
    autonomous_status_parser.add_argument("--json", action="store_true", dest="as_json")

    autonomous_stop_parser = autonomous_subparsers.add_parser("stop")
    autonomous_stop_parser.add_argument("--work", dest="work_id")
    autonomous_stop_parser.add_argument("--reason", default="operator-stop")
    autonomous_stop_parser.add_argument("--json", action="store_true", dest="as_json")

    autonomous_daemon = autonomous_subparsers.add_parser("daemon")
    daemon_subparsers = autonomous_daemon.add_subparsers(dest="daemon_command", required=True)
    for daemon_command in ("start", "run", "tick"):
        daemon_parser = daemon_subparsers.add_parser(daemon_command)
        daemon_parser.add_argument("--work", dest="work_id")
        daemon_parser.add_argument("--works", dest="works_scope")
        daemon_parser.add_argument("--mode", choices=AUTONOMOUS_MODES, default="autonomous-full")
        daemon_parser.add_argument("--poll-seconds", type=int, default=30)
        daemon_parser.add_argument("--max-cycles", type=int, default=50)
        daemon_parser.add_argument("--max-runtime-minutes", type=int, default=240)
        daemon_parser.add_argument(
            "--stuck-after-minutes",
            type=int,
            default=None,
            help=(
                "Emit daemon/run-stuck critical alert and stop if no new command is issued "
                "within this many minutes. Overrides DAEMON_STUCK_AFTER_MINUTES env var."
            ),
        )
        daemon_parser.add_argument("--json", action="store_true", dest="as_json")

    daemon_status_parser = daemon_subparsers.add_parser("status")
    daemon_status_parser.add_argument("--work", dest="work_id")
    daemon_status_parser.add_argument("--works", dest="works_scope")
    daemon_status_parser.add_argument("--json", action="store_true", dest="as_json")

    daemon_stop_parser = daemon_subparsers.add_parser("stop")
    daemon_stop_parser.add_argument("--work", dest="work_id")
    daemon_stop_parser.add_argument("--works", dest="works_scope")
    daemon_stop_parser.add_argument("--reason", default="operator-stop")
    daemon_stop_parser.add_argument("--json", action="store_true", dest="as_json")

    daemon_launchd_parser = daemon_subparsers.add_parser("launchd")
    daemon_launchd_subparsers = daemon_launchd_parser.add_subparsers(dest="daemon_launchd_command", required=True)
    for launchd_command in ("install", "start", "restart", "status", "stop", "uninstall"):
        launchd_parser = daemon_launchd_subparsers.add_parser(launchd_command)
        launchd_parser.add_argument("--works", dest="works_scope", default="all")
        launchd_parser.add_argument("--label")
        launchd_parser.add_argument("--json", action="store_true", dest="as_json")
        if launchd_command == "install":
            launchd_parser.add_argument("--mode", choices=AUTONOMOUS_MODES, default="autonomous-full")
            launchd_parser.add_argument("--poll-seconds", type=int, default=30)
            launchd_parser.add_argument("--max-cycles", type=int, default=50)
            launchd_parser.add_argument("--max-runtime-minutes", type=int, default=240)

    args = parser.parse_args(argv)
    root_path = Path(root_dir).expanduser().resolve() if root_dir is not None else Path.cwd().resolve()

    try:
        if args.command == "launch-thesis":
            return launch_thesis(root_path, args)
        if args.command == "launch-academic":
            return launch_academic(root_path, args)
        if args.command == "assemble-thesis":
            return assemble_thesis(root_path, args.work_id)
        if args.command == "export-thesis-docx":
            return export_thesis_docx(root_path, args.work_id)
        if args.command == "build-vkr-frontmatter":
            return build_vkr_frontmatter(root_path, args.work_id)
        if args.command == "build-dissertation-artifacts":
            return build_dissertation_artifacts(root_path, args.work_id)
        if args.command == "one-shot-thesis":
            return one_shot_thesis(
                root_path,
                args.work_id,
                skip_docx=args.skip_docx,
                corpus_path=args.corpus_path,
                work_type=args.work_type,
            )
        if args.command == "one-shot-dissertation":
            return one_shot_dissertation(
                root_path,
                args.work_id,
                skip_docx=args.skip_docx,
                corpus_path=args.corpus_path,
                work_type=args.work_type,
            )
        if args.command == "export-article-docx":
            return export_article_docx(root_path, args.input_md, args.output_docx, args.work_id)
        if args.command == "standards-intake":
            return standards_intake(root_path, args.profile_id)
        if args.command == "standards-refresh":
            return standards_refresh(root_path, args.profile_id)
        if args.command == "standards-status":
            return standards_status(root_path, args.profile_id)
        if args.command == "work-status":
            return work_status(root_path, args.work_id, as_json=args.as_json)
        if args.command == "provider-smoke":
            return provider_smoke_cli(args.provider)
        if args.command == "runtime-index":
            return runtime_index_cli(root_path, args)
        if args.command == "jobs":
            return jobs_cli(root_path, args)
        if args.command == "job-inspect":
            return job_inspect_cli(root_path, args.job_id, as_json=args.as_json)
        if args.command == "export-explain":
            return export_explain_cli(root_path, args.subject, args.work_id, as_json=args.as_json)
        if args.command == "work":
            return work_cli(root_path, args)
        if args.command == "skill-source-map":
            return skill_source_map_cli(root_path, args)
        if args.command == "autonomous":
            return handle_autonomous_cli(root_path, args)
    except (WorkspaceConfigError, WorkflowError, JobQueueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


def work_cli(root_dir: Path, args: Any) -> int:
    if args.work_command == "init":
        return work_init(root_dir, args)
    return 1


def work_init(root_dir: Path, args: Any) -> int:
    lanes: tuple[str, ...] | None = None
    if args.lanes:
        lanes_raw = [lane.strip() for lane in str(args.lanes).split(",") if lane.strip()]
        if not lanes_raw:
            print("--lanes must not be empty when provided", file=sys.stderr)
            return 2
        lanes = tuple(lanes_raw)

    topic = args.topic.strip() if args.topic else args.title
    request = CreateWorkRequest(
        slug=args.slug,
        title=args.title,
        topic=topic,
        artifact_type=args.artifact_type,
        language=args.language,
        lanes=lanes,
        thesis_profile=args.thesis_profile,
        article_profile=args.article_profile,
        set_default=bool(args.set_default),
    )

    try:
        payload = EngineService(root_dir).create_work(request)
    except WorkBootstrapError as exc:
        print(f"work init failed: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "as_json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        resolved_root = root_dir.resolve()
        work_dir = Path(str(payload["work_dir"]))
        rel = work_dir.relative_to(resolved_root) if work_dir.is_absolute() else work_dir
        print(f"Created work `{payload['slug']}` at {rel}")
        print(f"  work.toml: {Path(str(payload['work_toml'])).relative_to(resolved_root)}")
        print(f"  work-canon.md: {Path(str(payload['work_canon'])).relative_to(resolved_root)}")
        print(f"  registered in: {Path(str(payload['workspace_toml'])).relative_to(resolved_root)}")
        if payload["set_default"]:
            print(f"  default_work switched to `{payload['default_work']}`")
        else:
            print(f"  default_work remains `{payload['default_work']}`")
        print("Next step: заполнить work-canon.md и положить источники / бриф в соответствующую lane.")
    return 0


def skill_source_map_cli(root_dir: Path, args: Any) -> int:
    if args.skill_source_command == "audit":
        report = audit_skill_source_map(root_dir, external_skills_root=args.skills_root)
        payload = {
            "kind": "skill-source-audit",
            "version": "v1",
            "ok": report.ok,
            "declared_skill_count": len(report.declared_skills),
            "manifest_skill_count": len(report.entries),
            "external_skill_files_checked": list(report.external_skill_files_checked),
            "issues": [
                {
                    "code": item.code,
                    "skill_name": item.skill_name,
                    "message": item.message,
                }
                for item in report.issues
            ],
        }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Skill source audit: ok={'yes' if report.ok else 'no'}")
            print(f"Declared skills: {len(report.declared_skills)}")
            print(f"Manifest skills: {len(report.entries)}")
            print(f"External skill files checked: {len(report.external_skill_files_checked)}")
            if report.issues:
                print("Issues:")
                for item in report.issues:
                    print(f"- {item.skill_name}: {item.code} - {item.message}")
        return 0

    if args.skill_source_command == "sync-external":
        report = sync_external_skill_sources(root_dir, args.skills_root, write=args.write)
        if args.as_json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(
                "Skill source sync: "
                f"root={report.external_skills_root} "
                f"updated={report.updated_count} "
                f"candidates={report.update_candidate_count} "
                f"missing={report.missing_external_count}"
            )
            for item in report.items:
                if item.status in {"updated", "would-update"}:
                    print(f"- {item.skill_name}: {item.status}")
        return 0

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
    use_search = _resolve_search(
        args.search_override,
        args.preset
        in {
            "full-cycle",
            "source-pack",
            "verify",
            "write-section",
            "build-maps",
            "verify-claims",
            "counterargument-pass",
            "draft-author-position",
        },
    )
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

    if not args.workflow_id:
        return _enqueue_role_workflow(
            root_dir=root_dir,
            work_id=work.slug,
            lane="thesis",
            action=args.preset,
            target_or_topic=target_rel,
            notes=args.notes,
            search_override=args.search_override,
            model_override=args.model,
        )

    output_dir = work.thesis.paths.output_runs_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = output_dir / f"{timestamp}-{args.preset}.md"
    manifest_file = output_dir / f"{timestamp}-{args.preset}.meta.json"
    workflow_run = _run_role_workflow(
        workflow_id=args.workflow_id,
        root_dir=root_dir,
        work=work,
        lane="thesis",
        action=args.preset,
        contract=contract,
        prompt=prompt,
        use_search=use_search,
        model=args.model,
        metadata={
            "target": target_rel,
            "target_resolution": target_resolution.to_dict(),
            "profile_id": profile.resolved_profile_id,
            "profile_conflict_flag": profile.conflict_flag,
        },
    )
    _copy_workflow_output(workflow_run, out_file)
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
            "workflow_id": workflow_run.workflow_id,
            "workflow_path": str(Path(workflow_run.workflow_dir) / "workflow.json"),
            "execution_status": workflow_run.execution_status,
            "readiness_status": workflow_run.readiness_status,
            "promotion_status": workflow_run.promotion.status if workflow_run.promotion else "not-run",
        },
    )
    print(f"Workflow ID: {workflow_run.workflow_id}")
    print(f"Execution status: {workflow_run.execution_status}")
    print(f"Readiness status: {workflow_run.readiness_status}")
    print(f"Saved final message to {out_file}")
    print(f"Saved run manifest to {manifest_file}")
    return 0 if workflow_run.execution_status == "succeeded" else 1


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
            raise WorkspaceConfigError(
                "Для команды article нужно указать ровно один из аргументов: --topic или --brief."
            )
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
    elif args.workflow == "finalize":
        prompt = _build_finalize_prompt(
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

    if not args.workflow_id:
        target_or_topic = target_rel or topic or ""
        return _enqueue_role_workflow(
            root_dir=root_dir,
            work_id=work.slug,
            lane="article",
            action=args.workflow,
            target_or_topic=target_or_topic,
            notes=args.notes,
            search_override=args.search_override,
            model_override=args.model,
            profile_override=args.profile,
        )

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
        workflow_run = _run_role_workflow(
            workflow_id=args.workflow_id,
            root_dir=root_dir,
            work=work,
            lane="article",
            action=args.workflow,
            contract=contract,
            prompt=prompt,
            use_search=use_search,
            model=args.model,
            metadata={
                "article_slug": article_slug,
                "topic": topic,
                "target": target_rel_value,
                "target_resolution": target_resolution.to_dict() if target_resolution else None,
                "profile_id": profile.resolved_profile_id,
                "profile_conflict_flag": profile.conflict_flag,
            },
        )
        _copy_workflow_output(workflow_run, out_file)
        manifest_payload.update(
            {
                "workflow_id": workflow_run.workflow_id,
                "workflow_path": str(Path(workflow_run.workflow_dir) / "workflow.json"),
                "execution_status": workflow_run.execution_status,
                "readiness_status": workflow_run.readiness_status,
                "promotion_status": workflow_run.promotion.status if workflow_run.promotion else "not-run",
            }
        )
        _write_json(manifest_file, manifest_payload)
        completed_bundle_state = build_article_bundle_state(
            work_id=work.slug,
            article_slug=article_slug,
            bundle=bundle,
            profile_id=profile.resolved_profile_id,
            last_action=args.workflow,
            last_run_status=workflow_run.execution_status,
            latest_run_manifest=str(manifest_file),
            latest_output_file=str(out_file),
            execution_contract=contract.to_dict(),
            topic=topic,
            input_brief=input_brief_rel,
            target_path=target_rel_value,
            previous_state=load_article_bundle_state(bundle_state_path),
        )
        write_article_bundle_state(bundle_state_path, completed_bundle_state)
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        OSError,
        TypeError,
        ValueError,
    ):
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
    print(f"Workflow ID: {workflow_run.workflow_id}")
    print(f"Execution status: {workflow_run.execution_status}")
    print(f"Readiness status: {workflow_run.readiness_status}")
    return 0 if workflow_run.execution_status == "succeeded" else 1


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


def work_status(root_dir: Path, work_id: str | None, *, as_json: bool = False) -> int:
    state = EngineService(root_dir).get_work_status(work_id=work_id)
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(format_work_state_summary(state))
    return 0


def provider_smoke_cli(provider: str) -> int:
    try:
        result = run_provider_smoke(provider)
    except ProviderExecutionError as exc:
        print(f"[provider-smoke] {exc.blocker_code}: {exc}", file=sys.stderr)
        return 1
    print(f"[provider-smoke] provider: {result.provider_id}")
    print(f"[provider-smoke] model: {result.model}")
    print(f"[provider-smoke] response_chars: {result.content_length}")
    print(f"[provider-smoke] preview: {result.preview}")
    return 0


def runtime_index_cli(root_dir: Path, args: Any) -> int:
    service = EngineService(root_dir)
    if args.runtime_index_command == "refresh":
        payload = service.refresh_runtime_index()
    elif args.runtime_index_command == "status":
        payload = service.get_runtime_index(work_id=args.work_id, limit=args.limit)
    else:
        return 1
    _print_runtime_index_payload(payload, as_json=args.as_json)
    return 1 if payload.get("status") == "failed" else 0


def _print_runtime_index_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json or payload.get("status") == "failed":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    kind = payload.get("kind")
    if kind == "runtime-index-refresh":
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        print(f"Runtime index refreshed: {payload.get('status')}")
        print(f"Works: {payload.get('works_indexed') or 0}")
        print(f"Recent runs: {payload.get('runs_indexed') or 0}")
        print(f"Blockers: {payload.get('blockers_indexed') or 0}")
        print(f"Artifacts: {payload.get('artifacts_indexed') or 0}")
        print(f"Warnings: {len(warnings)}")
        for warning in warnings[:5]:
            if not isinstance(warning, dict):
                continue
            print(f"- {warning.get('code')}: {warning.get('path')}")
        print(f"Index path: {payload.get('index_path')}")
        return

    works = payload.get("works") if isinstance(payload.get("works"), list) else []
    recent_runs = payload.get("recent_runs") if isinstance(payload.get("recent_runs"), list) else []
    blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
    print(f"Runtime index: {payload.get('status')}")
    print(f"Refreshed at: {payload.get('refreshed_at') or 'n/a'}")
    print(f"Works: {len(works)}")
    print(f"Recent runs: {len(recent_runs)}")
    print(f"Blockers: {len(blockers)}")
    print(f"Artifacts: {len(artifacts)}")


def jobs_cli(root_dir: Path, args: Any) -> int:
    service = EngineService(root_dir)
    if args.jobs_command == "submit-workflow":
        payload = service.submit_workflow_job(
            SubmitWorkflowJobRequest(
                work_id=args.work_id,
                lane=args.lane,
                action=args.action,
                target_or_topic=args.target_or_topic,
                notes=args.notes,
                search_override=args.search_override,
                model_override=args.model_override,
                profile_override=args.profile_override,
            )
        )
    elif args.jobs_command == "list":
        payload = service.list_jobs(work_id=args.work_id, status=args.status)
    elif args.jobs_command == "status":
        payload = service.get_job(args.job_id)
    elif args.jobs_command == "cancel":
        payload = service.cancel_job(CancelJobRequest(args.job_id, reason=args.reason))
    elif args.jobs_command == "retry":
        payload = service.retry_job(RetryJobRequest(args.job_id))
    elif args.jobs_command == "resume":
        payload = service.resume_job(ResumeJobRequest(args.job_id))
    elif args.jobs_command == "dispatch":
        payload = service.dispatch_jobs(DispatchJobsRequest(limit=args.limit))
    else:
        return 1
    _print_job_payload(payload, as_json=args.as_json)
    return 0


def job_inspect_cli(root_dir: Path, job_id: str, *, as_json: bool = False) -> int:
    payload = EngineService(root_dir).inspect_job(InspectJobRequest(job_id))
    _print_job_payload(payload, as_json=as_json)
    return 0


def export_explain_cli(root_dir: Path, subject: str, work_id: str | None, *, as_json: bool = False) -> int:
    payload = EngineService(root_dir).explain_export(subject, work_id=work_id)
    _print_job_payload(payload, as_json=as_json)
    return 0


def _print_job_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    kind = payload.get("kind")
    if kind == "job-list":
        jobs = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
        print(f"Jobs: {len(jobs)}")
        for job in jobs:
            if isinstance(job, dict):
                print(f"- {job.get('job_id')}: {job.get('status')} work={job.get('work_id')}")
        return

    if kind == "job-dispatch":
        _print_dispatch_group("Dispatched", payload.get("dispatched"))
        _print_dispatch_group("Skipped", payload.get("skipped"))
        _print_dispatch_group("Blocked", payload.get("blocked"))
        _print_dispatch_group("Reconciled", payload.get("reconciled"))
        return

    if kind == "job-inspection":
        job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
        print(f"Job: {job.get('job_id')} status={job.get('status')}")
        print(f"Timeline events: {len(payload.get('timeline') or [])}")
        print(f"Changed files: {len(payload.get('changed_files') or [])}")
        return

    if kind == "export-explanation":
        print(f"Export {payload.get('subject')}: {payload.get('status')}")
        for reason in payload.get("reasons") or []:
            if isinstance(reason, dict):
                print(f"- {reason.get('code')}: {reason.get('message')}")
        return

    print(f"Job: {payload.get('job_id')} status={payload.get('status')} work={payload.get('work_id')}")


def _print_dispatch_group(label: str, raw_items: object) -> None:
    items = raw_items if isinstance(raw_items, list) else []
    print(f"{label}: {len(items)}")
    for item in items:
        if isinstance(item, dict):
            details = [f"{item.get('job_id')}: {item.get('status')} work={item.get('work_id')}"]
            if item.get("reason"):
                details.append(f"reason={item.get('reason')}")
            if item.get("message"):
                details.append(f"message={item.get('message')}")
            print(f"  - {' '.join(details)}")


def assemble_thesis(root_dir: Path, work_id: str | None) -> int:
    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id)
    if not work.thesis:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает thesis lane.")

    work.thesis.full_draft_path.parent.mkdir(parents=True, exist_ok=True)
    gen_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
    parts = [
        f"<!-- Generated by scripts/assemble_thesis.sh for {work.slug} on {gen_ts} -->",
        "",
    ]
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

    require_submission_ready_workflow(root_dir, work.slug, "thesis")
    require_machine_gates_passed(work.thesis.reviews_dir)
    assemble_thesis(root_dir, work.slug)
    work.thesis.export_docx_path.parent.mkdir(parents=True, exist_ok=True)
    _run_pandoc(work.thesis.full_draft_path, work.thesis.export_docx_path)
    print(f"Exported {work.thesis.export_docx_path}")
    _run_thesis_standards_checks(work.thesis.full_draft_path, work.thesis.export_docx_path)
    return 0


def one_shot_thesis(
    root_dir: Path,
    work_id: str | None,
    *,
    skip_docx: bool = False,
    corpus_path: str | None = None,
    work_type: str | None = None,
) -> int:
    """Run thesis one-shot gates; dissertations dispatch to dissertation-specific checks."""
    return _run_one_shot_pipeline(
        root_dir,
        work_id,
        skip_docx=skip_docx,
        corpus_path=corpus_path,
        work_type=work_type,
        force_dissertation=False,
    )


def one_shot_dissertation(
    root_dir: Path,
    work_id: str | None,
    *,
    skip_docx: bool = False,
    corpus_path: str | None = None,
    work_type: str | None = None,
) -> int:
    """Run dissertation-specific deterministic gates and write a report."""
    return _run_one_shot_pipeline(
        root_dir,
        work_id,
        skip_docx=skip_docx,
        corpus_path=corpus_path,
        work_type=work_type,
        force_dissertation=True,
    )


def build_vkr_frontmatter(root_dir: Path, work_id: str | None) -> int:
    """Render title-page/abstract/keywords/task-sheet from thesis/metadata.toml."""
    from .dissertation_contour import is_dissertation_artifact_type
    from .vkr_artifacts import build_bundle, write_bundle

    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id)
    if not work.thesis:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает thesis lane.")
    if is_dissertation_artifact_type(work.artifact_type):
        raise WorkspaceConfigError(
            f"Work `{work.slug}` использует dissertation contour. Применяй build-dissertation-artifacts."
        )

    thesis_root = work.thesis.paths.root_dir
    metadata_path = thesis_root / "metadata.toml"
    destination = thesis_root / "frontmatter"
    bundle = build_bundle(metadata_path)
    if bundle.has_blockers:
        print(f"[vkr] Невозможно собрать frontmatter: {len(bundle.issues)} блокер(ов):")
        for issue in bundle.issues:
            print(f"  - [{issue.code}] {issue.message}")
        print(f"[vkr] Исправьте {metadata_path} и повторите.")
        return 1
    written = write_bundle(bundle, destination=destination)
    print(f"[vkr] Записано {len(written)} файл(ов) в {destination}:")
    for path in written:
        print(f"  - {path.relative_to(root_dir)}")
    return 0


def build_dissertation_artifacts(root_dir: Path, work_id: str | None) -> int:
    """Render dissertation author abstract and defense checklist from thesis/dissertation/metadata.toml."""
    from .dissertation_artifacts import build_bundle, write_bundle
    from .dissertation_contour import dissertation_paths, is_dissertation_artifact_type

    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id)
    if not work.thesis:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает thesis lane.")
    if not is_dissertation_artifact_type(work.artifact_type):
        raise WorkspaceConfigError(
            f"Work `{work.slug}` не является dissertation contour. Для ВКР используй build-vkr-frontmatter."
        )

    paths = dissertation_paths(work)
    bundle = build_bundle(paths.metadata_path)
    if bundle.has_blockers:
        print(f"[dissertation] Невозможно собрать artifacts: {len(bundle.issues)} блокер(ов):")
        for issue in bundle.issues:
            print(f"  - [{issue.code}] {issue.message}")
        print(f"[dissertation] Исправьте {paths.metadata_path} и повторите.")
        return 1
    written = write_bundle(bundle, destination=paths.artifacts_dir)
    print(f"[dissertation] Записано {len(written)} файл(ов) в {paths.artifacts_dir}:")
    for path in written:
        print(f"  - {path.relative_to(root_dir)}")
    return 0


def _run_one_shot_pipeline(
    root_dir: Path,
    work_id: str | None,
    *,
    skip_docx: bool,
    corpus_path: str | None,
    work_type: str | None,
    force_dissertation: bool,
) -> int:
    from .dissertation_contour import dissertation_paths, is_dissertation_artifact_type
    from .one_shot import OneShotConfig, run_one_shot, write_report
    from .work_type import resolve_profile

    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id)
    if not work.thesis:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает thesis lane.")

    effective_work_type = work_type or getattr(work, "artifact_type", None)
    profile = resolve_profile(effective_work_type)
    is_dissertation = bool(profile and profile.artifact_family == "dissertation") or (
        effective_work_type is None and is_dissertation_artifact_type(work.artifact_type)
    )
    if force_dissertation and not is_dissertation:
        raise WorkspaceConfigError(
            f"Work `{work.slug}` не является dissertation contour. Для thesis/VKR используй one-shot-thesis."
        )

    thesis_root = work.thesis.paths.root_dir
    manuscript = work.thesis.full_draft_path
    docx = work.thesis.export_docx_path
    corpus = _resolve_path(root_dir, corpus_path) if corpus_path else None

    dissertation = dissertation_paths(work) if is_dissertation else None
    metadata = thesis_root / "metadata.toml"
    frontmatter = thesis_root / "frontmatter"
    config = OneShotConfig(
        manuscript_md=manuscript,
        docx_path=docx,
        metadata_path=metadata if metadata.exists() else None,
        frontmatter_destination=frontmatter if metadata.exists() else None,
        dissertation_metadata_path=dissertation.metadata_path if dissertation else None,
        dissertation_artifacts_destination=dissertation.artifacts_dir if dissertation else None,
        dissertation_root=dissertation.root_dir if dissertation else None,
        corpus_path=corpus,
        require_docx=True,
        require_frontmatter=True,
        require_work_type=True,
        work_type=effective_work_type,
    )
    if skip_docx:
        print("[one-shot] strict mode: --skip-docx does not skip the mandatory DOCX conformance gate.")
    report = run_one_shot(config)

    reviews_dir = thesis_root / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    date_stamp = report.started_at.strftime("%Y-%m-%d")
    stem = "one-shot-dissertation-report" if is_dissertation else "one-shot-report"
    md_path = reviews_dir / f"{date_stamp}-{stem}.md"
    json_path = reviews_dir / f"{date_stamp}-{stem}.json"
    write_report(report, markdown_path=md_path, json_path=json_path)

    print(f"[one-shot] status: {report.status}")
    for gate in report.gates:
        marker = "PASS" if gate.passed else "FAIL"
        print(f"  [{marker}] {gate.name}: {gate.summary}")
    print(f"[one-shot] report: {md_path.relative_to(root_dir)}")
    return 0 if report.status == "machine-gates-passed" else 1


def _run_thesis_standards_checks(manuscript_md: Path, exported_docx: Path) -> None:
    """Run GOST-linter and DOCX-conformance after thesis export.

    Prints a short summary; does not raise. The orchestrator's repair kernel
    will consume these blockers via the runtime artifact.
    """
    from .docx_conformance import check_docx
    from .gost_linter import lint_bibliography

    try:
        manuscript_text = manuscript_md.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[standards] Не удалось прочитать manuscript: {exc}")
        return

    gost_report = lint_bibliography(manuscript_text)
    if gost_report.has_blockers:
        print(f"[standards] GOST bibliography: найдено {len(gost_report.issues)} замечаний:")
        for issue in gost_report.issues[:10]:
            print(f"  - #{issue.entry_index} [{issue.code}] {issue.message}")
    else:
        print("[standards] GOST bibliography: замечаний нет.")

    docx_report = check_docx(exported_docx)
    if docx_report.has_blockers:
        print(f"[standards] DOCX conformance: найдено {len(docx_report.issues)} отклонений:")
        for issue in docx_report.issues[:10]:
            print(f"  - [{issue.code}] {issue.message}")
    else:
        print("[standards] DOCX conformance: все проверки пройдены.")


def export_article_docx(root_dir: Path, raw_input: str, raw_output: str | None, work_id: str | None) -> int:
    workspace = load_workspace_config(root_dir)
    work_selection = resolve_work_selection(workspace, work_id=work_id, target=raw_input)
    work = work_selection.work
    require_submission_ready_workflow(root_dir, work.slug, "article")

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
    review_trace = (
        f"- Preferred review artifact path: {review_path}"
        if review_path
        else "- No dedicated review artifact path was precomputed for this run."
    )
    sync_trace = (
        f"- Preferred sync checkpoint path: {sync_hint_path}"
        if sync_hint_path
        else "- No sync checkpoint path was precomputed for this run."
    )
    profile_trace = _format_profile_trace(profile)
    root = workspace.root_dir
    slug = work.slug
    action_intro = {
        "full-cycle": (f"Use $thesis-workflow-orchestrator to handle this thesis task end-to-end in {root}."),
        "source-pack": (
            "Use $thesis-research-synthesizer and $thesis-source-verifier for this thesis "
            f"source-package task in the active work `{slug}`."
        ),
        "verify": (
            "Use $thesis-source-verifier and $thesis-citation-checker for this verification pass "
            f"in the active work `{slug}`."
        ),
        "write-section": (
            "Use $thesis-draft-writer, $thesis-source-verifier, and $thesis-citation-checker to "
            f"draft or expand this thesis section in the active work `{slug}`."
        ),
        "review-section": (
            "Use $thesis-argument-critic and $thesis-citation-checker to review this thesis "
            f"section in the active work `{slug}`."
        ),
        "style-pass": (
            "Use $thesis-style-editor for a final style refinement pass on this checked thesis "
            f"text in the active work `{slug}`."
        ),
        "build-maps": (
            "Use $thesis-structure-architect, $thesis-research-synthesizer, and "
            f"$thesis-source-verifier to build the dissertation research scaffold in `{slug}`."
        ),
        "verify-claims": (
            "Use $thesis-source-verifier and $thesis-citation-checker for this dissertation "
            f"claim verification pass in `{slug}`."
        ),
        "counterargument-pass": (
            "Use $thesis-argument-critic and the dissertation counterargument workflow to "
            f"stress-test the dissertation logic in `{slug}`."
        ),
        "draft-author-position": (
            "Use $thesis-draft-writer, $thesis-source-verifier, and $thesis-argument-critic "
            f"to draft the dissertation author position in `{slug}`."
        ),
        "formal-artifacts": (
            "Use the dissertation formal-artifact workflow to update metadata, publication evidence, "
            f"and generated dissertation artifacts in `{slug}`."
        ),
    }
    target_label = {
        "full-cycle": "Target artifact",
        "source-pack": "Target source package",
        "verify": "Target file",
        "write-section": "Target section",
        "review-section": "Target section",
        "style-pass": "Target file",
        "build-maps": "Target dissertation map",
        "verify-claims": "Target dissertation claim artifact",
        "counterargument-pass": "Target dissertation review artifact",
        "draft-author-position": "Target dissertation section",
        "formal-artifacts": "Target dissertation artifact",
    }[contract.action]
    standards_block = f"Standards profile:\n{profile_trace}\n" if contract.action != "review-section" else ""
    return f"""{action_intro[contract.action]}

Active work:
- Work ID: {work.slug}
- Work title: {work.title}
- Work root: {work.work_dir}
- Work canon: {work.work_canon_path}
- Work config: {work.work_dir / "work.toml"}

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
    head = f"Use $academic-workflow-orchestrator to run a full legal-academic article workflow in {workspace.root_dir}."
    return f"""{head}

Active work:
- Work ID: {work.slug}
- Work title: {work.title}
- Work root: {work.work_dir}
- Work canon: {work.work_canon_path}
- Work config: {work.work_dir / "work.toml"}

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
    head = (
        "Use $academic-submission-evaluator, $academic-counterargument-critic, and "
        f"$academic-citation-checker to review this legal-academic article bundle in {workspace.root_dir}."
    )
    return f"""{head}

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
    head = (
        "Use $academic-repair-orchestrator, $academic-source-verifier, "
        "$academic-citation-checker, $academic-submission-evaluator, and $academic-finalizer "
        f"to repair this legal-academic article bundle in {workspace.root_dir}."
    )
    return f"""{head}

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


def _build_finalize_prompt(
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
    head = (
        "Use $academic-finalizer, $academic-submission-evaluator, and $academic-citation-checker "
        f"to finalize this legal-academic article bundle in {workspace.root_dir}."
    )
    return f"""{head}

Active work:
- Work ID: {work.slug}
- Work title: {work.title}
- Work canon: {work.work_canon_path}

Finalization input: {target_path}
Finalization input (relative): {target_rel}
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
    from .dissertation_contour import chapter_contract_paths, dissertation_paths, is_dissertation_artifact_type

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
        workspace.root_dir / "templates" / "evidence-ledger.md",
        workspace.root_dir / "templates" / "chapter-brief.md",
        workspace.root_dir / "templates" / "chapter-review-sheet.md",
        workspace.root_dir / "templates" / "chat-sync.md",
        target_path,
    ]
    if is_dissertation_artifact_type(work.artifact_type):
        dissertation = dissertation_paths(work)
        paths.extend(
            [
                workspace.root_dir / "templates" / "claim-map.md",
                workspace.root_dir / "templates" / "dissertation-historiography-map.md",
                workspace.root_dir / "templates" / "dissertation-novelty-map.md",
                workspace.root_dir / "templates" / "dissertation-chapter-contract.md",
                workspace.root_dir / "templates" / "dissertation-review-sheet.md",
                workspace.root_dir / "templates" / "dissertation-publication-evidence.md",
                workspace.root_dir / "templates" / "dissertation-publication-claim-matrix.md",
                workspace.root_dir / "templates" / "dissertation-author-abstract.md",
                dissertation.metadata_path,
                dissertation.historiography_map_path,
                dissertation.novelty_map_path,
                dissertation.claim_map_path,
                dissertation.counterargument_review_path,
                dissertation.dissertation_review_path,
                dissertation.publication_evidence_path,
                dissertation.publication_claim_matrix_path,
                dissertation.leading_organization_path,
                dissertation.opponents_path,
            ]
        )
        paths.extend(chapter_contract_paths(work))
    keywords = _target_keywords(target_path)
    for directory in (
        work.thesis.chapters_dir,
        work.thesis.sources_dir,
        work.thesis.ledgers_dir,
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


def _run_role_workflow(
    *,
    workflow_id: str | None,
    root_dir: Path,
    work: WorkConfig,
    lane: str,
    action: str,
    contract: ExecutionContract,
    prompt: str,
    use_search: bool,
    model: str | None,
    metadata: dict[str, Any],
) -> Any:
    engine = WorkflowEngine(root_dir, executor_router=build_executor_router())
    return engine.run(
        workflow_id=workflow_id,
        work_id=work.slug,
        work_dir=work.work_dir,
        lane=lane,
        action=action,
        contract=contract,
        base_prompt=prompt,
        use_search=use_search,
        model=model,
        metadata=metadata,
    )


def _enqueue_role_workflow(
    *,
    root_dir: Path,
    work_id: str,
    lane: str,
    action: str,
    target_or_topic: str,
    notes: str | None,
    search_override: bool | None,
    model_override: str | None,
    profile_override: str | None = None,
) -> int:
    from .orchestrator import WorkflowOrchestrator

    active = WorkflowOrchestrator(root_dir).start_run(
        lane,
        action,
        target_or_topic,
        notes=notes,
        search_override=search_override,
        model_override=model_override,
        profile_override=profile_override,
        work_id=work_id,
    )
    print("Enqueue status: queued")
    print(f"Workflow ID: {active['workflow_id']}")
    print(f"Run ID: {active['run_id']}")
    print(f"Work ID: {active['work_id']}")
    return 0


def _copy_workflow_output(workflow_run: Any, destination: Path) -> None:
    for role in reversed(workflow_run.role_runs):
        if not role.output_file:
            continue
        source = Path(role.output_file)
        if not source.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        (
            f"Workflow {workflow_run.workflow_id}\n\n"
            f"Execution status: {workflow_run.execution_status}\n"
            f"Readiness status: {workflow_run.readiness_status}\n"
        ),
        encoding="utf-8",
    )


def _run_pandoc(input_md: Path, output_docx: Path) -> None:
    pandoc_bin = _resolve_pandoc_bin()
    if pandoc_bin is None:
        print(
            "Ошибка: утилита pandoc не найдена в PATH. Установите Pandoc: https://pandoc.org",
            file=sys.stderr,
        )
        raise FileNotFoundError("pandoc")
    subprocess.run(
        [
            pandoc_bin,
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


def _resolve_pandoc_bin() -> str | None:
    return resolve_executable(
        os.environ.get("PANDOC_BIN"),
        "pandoc",
        extra_candidates=("/opt/homebrew/bin/pandoc", "/usr/local/bin/pandoc"),
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
            f"  - {artifact.name}: {artifact.path} [{artifact.requirement}]" for artifact in contract.required_context
        )
    if contract.allowed_write_scopes:
        lines.append("- Allowed writes:")
        lines.extend(f"  - {item.name}: {item.path}" for item in contract.allowed_write_scopes)
    if contract.required_outputs:
        lines.append("- Required outputs:")
        lines.extend(
            f"  - {artifact.name}: {artifact.path} [{artifact.requirement}]" for artifact in contract.required_outputs
        )
    if contract.quality_gates:
        lines.append("- Quality gates:")
        lines.extend(f"  - {gate.gate_id}: {gate.description}" for gate in contract.quality_gates)
    if contract.transitions:
        lines.append("- Transitions:")
        lines.extend(
            f"  - {item.from_phase} -> {item.to_phase}: {item.completion_signal}" for item in contract.transitions
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
