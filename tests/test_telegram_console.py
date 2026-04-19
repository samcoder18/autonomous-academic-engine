from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import os
import re
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from telegram_console.agent_chat import (
    AgentBusyError,
    AgentChatService,
    AgentTurnNotification,
    ProjectChatState,
)
from telegram_console.article_bundle_state import article_bundle_manifest_path
from telegram_console.article_runtime_signals import extract_article_artifact_signals
from telegram_console.thesis_runtime_signals import extract_thesis_runtime_signals
from telegram_console.thesis_repair_planner import build_thesis_repair_plan
from telegram_console.guarded_prose import load_guarded_prose_rules
from telegram_console.action_specs import (
    build_article_execution_contract,
    build_thesis_execution_contract,
    list_action_specs,
)
from telegram_console.contract_gates import evaluate_contract_gates
from telegram_console.autonomous_policy import evaluate_autonomous_policy
from telegram_console.autonomous_planner import build_autonomous_plan
from telegram_console.finalization_engine import evaluate_article_finalization
from telegram_console.repair_kernel import (
    Blocker,
    build_repair_plan,
    run_bounded_repair_loop,
)
from telegram_console.bot import MAIN_MENU, TelegramConsoleBot, main
from telegram_console.config import TelegramConsoleConfig
from telegram_console.email_delivery import EmailDeliveryError, SmtpDocxSender, SmtpSettings
from telegram_console.launchd_service import DEFAULT_SERVICE_LABEL, LaunchdServiceManager
from telegram_console.orchestrator import RunBusyError, RunRecord, WorkflowOrchestrator
from telegram_console.prompting import PROFILE_EXPECTATIONS, PROFILE_LABELS, PromptBuilder
from telegram_console.projects import ProjectService
from telegram_console.runtime_status import build_runtime_status, record_from_payload
from telegram_console import chat_wrapper as chat_wrapper_module
from telegram_console import run_wrapper as run_wrapper_module
from telegram_console.standards import load_standards_registry, resolve_standard_profile
from telegram_console.telegram_api import TelegramApiError, TelegramBotApi
from telegram_console import work_cli as work_cli_module
from telegram_console.workspace import (
    article_bundle_paths,
    legacy_target_entries,
    legacy_target_prefixes,
    load_work_config,
    load_workspace_config,
    relative_to_workspace,
    resolve_target_for_action,
    resolve_work_selection,
)


TEST_WORK_ID = "demo-work"
TEST_WORK_ROOT = Path("works") / TEST_WORK_ID
TEST_THESIS_SECTION = TEST_WORK_ROOT / "thesis" / "manuscript" / "sections" / "01-introduction.md"
TEST_THESIS_REVIEW = TEST_WORK_ROOT / "thesis" / "reviews" / "01-introduction-review.md"
TEST_ARTICLE_BRIEF = TEST_WORK_ROOT / "articles" / "briefs" / "demo.md"
TEST_ARTICLE_DRAFT = TEST_WORK_ROOT / "articles" / "drafts" / "demo.md"
TEST_ARTICLE_FINAL = TEST_WORK_ROOT / "articles" / "final" / "demo.md"
TEST_ARTICLE_CHECKLIST = TEST_WORK_ROOT / "articles" / "final" / "demo-checklist.md"


def write_file(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def build_fake_repo(root: Path) -> None:
    write_file(root / "AGENTS.md", "# Agents\n")
    write_file(root / "meta/master-protocol.md", "# Master protocol\n")
    write_file(
        root / "workspace.toml",
        textwrap.dedent(
            f"""\
            version = 1
            default_work = "{TEST_WORK_ID}"
            supported_lanes = ["thesis", "article"]

            [default_profiles]
            thesis = "thesis-v1"
            article = "ru-law-article-v1"

            [outputs]
            runs_dir = "output/runs"
            docx_dir = "output/docx"

            [works]
            {TEST_WORK_ID} = "works/{TEST_WORK_ID}"
            """
        ),
    )
    write_file(
        root / TEST_WORK_ROOT / "work.toml",
        textwrap.dedent(
            f"""\
            version = 1
            slug = "{TEST_WORK_ID}"
            title = "Demo work"
            topic = "Demo topic"
            artifact_type = "vkr"
            language = "ru"
            active_lanes = ["thesis", "article"]
            work_canon = "work-canon.md"

            [standards]
            thesis_profile = "thesis-v1"
            article_profile = "ru-law-article-v1"

            [thesis]
            root_dir = "thesis"
            chapters_dir = "thesis/chapters"
            sources_dir = "thesis/sources"
            manuscript_dir = "thesis/manuscript"
            manuscript_sections_dir = "thesis/manuscript/sections"
            reviews_dir = "thesis/reviews"
            sync_dir = "thesis/sync"
            full_draft_path = "thesis/manuscript/full-draft.md"
            docx_filename = "thesis-draft.docx"
            section_order = ["thesis/manuscript/sections/01-introduction.md"]

            [article]
            root_dir = "articles"
            briefs_dir = "articles/briefs"
            evidence_dir = "articles/evidence"
            claim_maps_dir = "articles/claim-maps"
            drafts_dir = "articles/drafts"
            reviews_dir = "articles/reviews"
            final_dir = "articles/final"
            docx_subdir = "articles"
            """
        ),
    )
    write_file(root / TEST_WORK_ROOT / "work-canon.md", "# Canon\n")
    write_file(root / TEST_THESIS_SECTION, "# Intro\n")
    write_file(root / TEST_WORK_ROOT / "thesis" / "manuscript" / "README.md", "skip me\n")
    write_file(root / TEST_WORK_ROOT / "thesis" / "sources" / "source-pack.md", "# Sources\n")
    write_file(root / TEST_WORK_ROOT / "thesis" / "chapters" / "01-brief.md", "# Chapter\n")
    write_file(root / TEST_THESIS_REVIEW, "# Review\n")

    write_file(root / TEST_ARTICLE_BRIEF, "# Demo brief\n")
    write_file(root / TEST_ARTICLE_DRAFT, "# Demo draft\n")
    write_file(root / TEST_ARTICLE_FINAL, "# Demo final\n")
    write_file(root / TEST_ARTICLE_CHECKLIST, "# Demo checklist\n")
    write_file(root / "meta/standards/normalized/thesis-v1.md", "# thesis profile\n")
    write_file(root / "meta/standards/normalized/ru-law-article-v1.md", "# article profile\n")
    write_file(root / "meta/standards/raw/README.md", "# raw standards\n")
    write_file(root / "meta/standards/README.md", "# standards\n")

    write_file(
        root / "scripts/codex_thesis.sh",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail

            ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
            WORK_ID="demo-work"
            mkdir -p "$ROOT_DIR/output/runs/$WORK_ID/thesis"
            PRESET="${1:-}"
            TARGET="${2:-}"
            SLEEP_SECONDS="${TEST_SLEEP_SECONDS:-0}"
            if [[ "$SLEEP_SECONDS" != "0" ]]; then
              sleep "$SLEEP_SECONDS"
            fi
            TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
            OUT_FILE="$ROOT_DIR/output/runs/$WORK_ID/thesis/${TIMESTAMP}-${PRESET}.md"
            MANIFEST_FILE="$ROOT_DIR/output/runs/$WORK_ID/thesis/${TIMESTAMP}-${PRESET}.meta.json"
            printf 'thesis output\\n' > "$OUT_FILE"
            python3 - "$MANIFEST_FILE" "$TIMESTAMP" "$PRESET" "$ROOT_DIR" "$TARGET" "$OUT_FILE" "$WORK_ID" <<'PY'
            import json
            import sys
            manifest = {
                "timestamp": sys.argv[2],
                "preset": sys.argv[3],
                "work_id": sys.argv[7],
                "work_title": "Demo work",
                "target": {
                    "absolute": f"{sys.argv[4]}/{sys.argv[5]}",
                    "relative": sys.argv[5],
                    "state": "existing",
                },
                "search_enabled": False,
                "output_file": sys.argv[6],
            }
            with open(sys.argv[1], "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
                handle.write("\\n")
            PY
            """
        ),
        executable=True,
    )

    write_file(
        root / "scripts/codex_academic.sh",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail

            ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
            WORK_ID="demo-work"
            mkdir -p "$ROOT_DIR/output/runs/$WORK_ID/article"
            COMMAND="${1:-}"
            shift || true
            TARGET_PATH=""
            TOPIC=""
            INPUT_BRIEF=""
            if [[ "$COMMAND" == "article" ]]; then
              while [[ $# -gt 0 ]]; do
                case "$1" in
                  --topic)
                    TOPIC="$2"
                    shift 2
                    ;;
                  --brief)
                    INPUT_BRIEF="$2"
                    TARGET_PATH="$2"
                    shift 2
                    ;;
                  *)
                    shift 1
                    ;;
                esac
              done
            else
              TARGET_PATH="${1:-}"
            fi
            TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
            OUT_FILE="$ROOT_DIR/output/runs/$WORK_ID/article/${TIMESTAMP}-${COMMAND}-demo.md"
            MANIFEST_FILE="$ROOT_DIR/output/runs/$WORK_ID/article/${TIMESTAMP}-${COMMAND}-demo.meta.json"
            printf 'article output\\n' > "$OUT_FILE"
            python3 - "$MANIFEST_FILE" "$TIMESTAMP" "$COMMAND" "$ROOT_DIR" "$TARGET_PATH" "$TOPIC" "$INPUT_BRIEF" "$OUT_FILE" "$WORK_ID" <<'PY'
            import json
            import sys
            manifest = {
                "timestamp": sys.argv[2],
                "command": sys.argv[3],
                "work_id": sys.argv[9],
                "work_title": "Demo work",
                "profile_id": "ru-law-article-v1",
                "search_enabled": False,
                "target_path": sys.argv[5] or None,
                "topic": sys.argv[6] or None,
                "input_brief": sys.argv[7] or None,
                "output_file": sys.argv[8],
                "bundle": {
                    "slug": "demo",
                    "brief": f"{sys.argv[4]}/works/demo-work/articles/briefs/demo.md",
                    "evidence_pack": f"{sys.argv[4]}/works/demo-work/articles/evidence/demo.md",
                    "claim_map": f"{sys.argv[4]}/works/demo-work/articles/claim-maps/demo.md",
                    "draft": f"{sys.argv[4]}/works/demo-work/articles/drafts/demo.md",
                    "review": f"{sys.argv[4]}/works/demo-work/articles/reviews/demo.md",
                    "final_markdown": f"{sys.argv[4]}/works/demo-work/articles/final/demo.md",
                    "checklist": f"{sys.argv[4]}/works/demo-work/articles/final/demo-checklist.md",
                    "docx": f"{sys.argv[4]}/output/docx/demo-work/articles/demo.docx",
                },
            }
            with open(sys.argv[1], "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
                handle.write("\\n")
            PY
            """
        ),
        executable=True,
    )

    write_file(
        root / "scripts/export_docx.sh",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
            mkdir -p "$ROOT_DIR/output/docx/demo-work"
            OUT="$ROOT_DIR/output/docx/demo-work/thesis-draft.docx"
            printf 'fake thesis docx' > "$OUT"
            printf 'Exported %s\\n' "$OUT"
            """
        ),
        executable=True,
    )
    write_file(
        root / "scripts/export_academic_docx.sh",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
            INPUT="${1:-}"
            mkdir -p "$ROOT_DIR/output/docx/demo-work/articles"
            STEM="$(basename "${INPUT%.md}")"
            OUT="$ROOT_DIR/output/docx/demo-work/articles/${STEM}.docx"
            printf 'fake article docx' > "$OUT"
            printf 'Exported %s\\n' "$OUT"
            """
        ),
        executable=True,
    )


def write_sample_standards_registry(root: Path) -> None:
    write_file(
        root / "meta/standards/registry.toml",
        textwrap.dedent(
            """\
            version = 1

            [fallback_profiles]
            thesis = "thesis-v1"
            article = "ru-law-article-v1"

            [profiles.thesis-v1]
            workflow_lane = "thesis"
            unit_kind = "generic"
            status = "provisional"
            normalized_path = "meta/standards/normalized/thesis-v1.md"
            raw_dir = "meta/standards/raw/thesis-v1"
            official_only = true
            notes = [
              "Legacy generic fallback profile.",
            ]

            [profiles.ru-law-article-v1]
            workflow_lane = "article"
            unit_kind = "generic"
            status = "provisional"
            normalized_path = "meta/standards/normalized/ru-law-article-v1.md"
            raw_dir = "meta/standards/raw/ru-law-article-v1"
            official_only = true
            notes = [
              "Legacy generic fallback profile.",
            ]

            [profiles.sogu-vkr-2025]
            workflow_lane = "thesis"
            unit_kind = "university"
            status = "official"
            normalized_path = "meta/standards/normalized/sogu-vkr-2025.md"
            raw_dir = "meta/standards/raw/sogu-vkr-2025"
            official_only = true
            conflict_flag = true
            notes = [
              "The 2025 methodology is official, but program-specific and may have applicability uncertainty outside the relevant program.",
            ]

            [[profiles.sogu-vkr-2025.sources]]
            id = "sogu-method-2025"
            label = "SOGU methodological recommendations 2025"
            url = "https://example.test/sogu-method-2025.pdf"
            date = "2025-01-01"

            [[profiles.sogu-vkr-2025.sources]]
            id = "sogu-regulation-2021"
            label = "SOGU VQR regulation 2021"
            url = "https://example.test/sogu-regulation-2021.pdf"
            date = "2021-01-01"

            [profiles.rf-dissertation-general]
            unit_kind = "dissertation-regulation"
            status = "official"
            normalized_path = "meta/standards/normalized/rf-dissertation-general.md"
            raw_dir = "meta/standards/raw/rf-dissertation-general"
            official_only = true

            [[profiles.rf-dissertation-general.sources]]
            id = "pravo-gov-2013"
            label = "Official legal publication"
            url = "https://example.test/pravo-gov-2013.html"
            date = "2013-10-01"

            [[profiles.rf-dissertation-general.sources]]
            id = "gost-rules"
            label = "GOST rules page"
            url = "https://example.test/gost-rules.html"

            [profiles.journal-jrp]
            workflow_lane = "article"
            unit_kind = "journal"
            status = "official"
            normalized_path = "meta/standards/normalized/journal-jrp.md"
            raw_dir = "meta/standards/raw/journal-jrp"
            official_only = true
            conflict_flag = true

            [[profiles.journal-jrp.sources]]
            id = "jrp-home"
            label = "JRP home"
            url = "https://example.test/jrp-home.html"
            date = "2024-04-01"

            [[profiles.journal-jrp.sources]]
            id = "jrp-rules"
            label = "JRP rules"
            url = "https://example.test/jrp-rules.html"
            date = "2025-02-01"

            [profiles.journal-gip]
            workflow_lane = "article"
            unit_kind = "journal"
            status = "official"
            normalized_path = "meta/standards/normalized/journal-gip.md"
            raw_dir = "meta/standards/raw/journal-gip"
            official_only = true

            [[profiles.journal-gip.sources]]
            id = "gip-submissions"
            label = "GIP submissions"
            url = "https://example.test/gip-submissions.html"

            [[profiles.journal-gip.sources]]
            id = "gip-author-rules"
            label = "GIP author rules"
            url = "https://example.test/gip-author-rules.html"

            [profiles.journal-kmp-yurist]
            workflow_lane = "article"
            unit_kind = "journal"
            status = "official"
            normalized_path = "meta/standards/normalized/journal-kmp-yurist.md"
            raw_dir = "meta/standards/raw/journal-kmp-yurist"
            official_only = true

            [[profiles.journal-kmp-yurist.sources]]
            id = "lawinfo-fresh"
            label = "Fresh issue"
            url = "https://example.test/lawinfo-fresh.html"

            [[profiles.journal-kmp-yurist.sources]]
            id = "lawinfo-authors"
            label = "For authors"
            url = "https://example.test/lawinfo-authors.html"

            [[profiles.journal-kmp-yurist.sources]]
            id = "lawinfo-formatting"
            label = "Formatting rules"
            url = "https://example.test/lawinfo-formatting.html"
            """
        ),
    )


def write_sample_normalized_profiles(root: Path) -> None:
    write_file(root / "meta/standards/normalized/sogu-vkr-2025.md", "# sogu-vkr-2025\n")
    write_file(root / "meta/standards/normalized/rf-dissertation-general.md", "# rf-dissertation-general\n")
    write_file(root / "meta/standards/normalized/journal-jrp.md", "# journal-jrp\n")
    write_file(root / "meta/standards/normalized/journal-gip.md", "# journal-gip\n")
    write_file(root / "meta/standards/normalized/journal-kmp-yurist.md", "# journal-kmp-yurist\n")


def write_raw_manifest(root: Path, profile_id: str, synced_at: str = "2026-04-18T10:00:00+00:00") -> None:
    raw_dir = root / "meta" / "standards" / "raw" / profile_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "manifest.json").write_text(
        json.dumps(
            {
                "profile_id": profile_id,
                "synced_at": synced_at,
                "sources": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def rewrite_work_profiles(root: Path, *, thesis_profile: str | None = None, article_profile: str | None = None) -> None:
    work_path = root / TEST_WORK_ROOT / "work.toml"
    content = work_path.read_text(encoding="utf-8")
    if thesis_profile is not None:
        content = re.sub(
            r'thesis_profile = "[^"]+"',
            f'thesis_profile = "{thesis_profile}"',
            content,
            count=1,
        )
    if article_profile is not None:
        content = re.sub(
            r'article_profile = "[^"]+"',
            f'article_profile = "{article_profile}"',
            content,
            count=1,
        )
    work_path.write_text(content, encoding="utf-8")


def add_empty_work_scaffold(root: Path, slug: str = "empty-work") -> None:
    workspace_file = root / "workspace.toml"
    workspace_text = workspace_file.read_text(encoding="utf-8")
    workspace_file.write_text(
        workspace_text + f'{slug} = "works/{slug}"\n',
        encoding="utf-8",
    )

    work_root = Path("works") / slug
    write_file(
        root / work_root / "work.toml",
        textwrap.dedent(
            f"""\
            version = 1
            slug = "{slug}"
            title = "Empty scaffold"
            topic = "Empty topic"
            artifact_type = "vkr"
            language = "ru"
            active_lanes = ["thesis", "article"]
            work_canon = "work-canon.md"

            [standards]
            thesis_profile = "thesis-v1"
            article_profile = "ru-law-article-v1"

            [thesis]
            root_dir = "thesis"
            chapters_dir = "thesis/chapters"
            sources_dir = "thesis/sources"
            manuscript_dir = "thesis/manuscript"
            manuscript_sections_dir = "thesis/manuscript/sections"
            reviews_dir = "thesis/reviews"
            sync_dir = "thesis/sync"
            full_draft_path = "thesis/manuscript/full-draft.md"
            docx_filename = "thesis-draft.docx"
            section_order = []

            [article]
            root_dir = "articles"
            briefs_dir = "articles/briefs"
            evidence_dir = "articles/evidence"
            claim_maps_dir = "articles/claim-maps"
            drafts_dir = "articles/drafts"
            reviews_dir = "articles/reviews"
            final_dir = "articles/final"
            docx_subdir = "articles"
            """
        ),
    )
    write_file(root / work_root / "work-canon.md", "# Empty work canon\n")
    write_file(root / work_root / "thesis" / "README.md", "# Empty thesis scaffold\n")
    write_file(root / work_root / "articles" / "README.md", "# Empty article scaffold\n")


def build_fake_launchd_files(root: Path) -> None:
    write_file(
        root / "scripts/run_telegram_console_launchd.sh",
        "#!/usr/bin/env bash\nexit 0\n",
        executable=True,
    )
    write_file(
        root / "deploy/local-telegram-console.plist",
        textwrap.dedent(
            """\
            <?xml version="1.0" encoding="UTF-8"?>
            <plist version="1.0">
            <dict>
              <key>Label</key>
              <string>__LABEL__</string>
              <key>ProgramArguments</key>
              <array>
                <string>__SHELL__</string>
                <string>__PROGRAM__</string>
              </array>
              <key>WorkingDirectory</key>
              <string>__WORKDIR__</string>
              <key>StandardOutPath</key>
              <string>__STDOUT__</string>
              <key>StandardErrorPath</key>
              <string>__STDERR__</string>
            </dict>
            </plist>
            """
        ),
    )


def build_fake_codex(path: Path) -> None:
    write_file(
        path,
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            from __future__ import annotations

            import json
            import os
            import sys
            import time
            from pathlib import Path


            def main() -> int:
                args = sys.argv[1:]
                if not args or args[0] != "exec":
                    print("unsupported command", file=sys.stderr)
                    return 2
                args = args[1:]
                project_root = ""
                output_path = None
                resume = False
                session_id = None
                model = None
                while args:
                    token = args.pop(0)
                    if token == "-C":
                        project_root = args.pop(0)
                    elif token in {"--skip-git-repo-check", "--full-auto", "--json"}:
                        continue
                    elif token == "-o":
                        output_path = args.pop(0)
                    elif token == "-m":
                        model = args.pop(0)
                    elif token == "resume":
                        resume = True
                    elif token == "-":
                        break
                    elif resume and session_id is None:
                        session_id = token
                    else:
                        continue

                prompt = sys.stdin.read().strip()
                if resume and session_id == "broken-session":
                    print("session not found", file=sys.stderr)
                    return 1

                sleep_seconds = float(os.getenv("FAKE_CODEX_SLEEP_SECONDS", "0") or "0")
                if sleep_seconds:
                    time.sleep(sleep_seconds)

                root_name = Path(project_root).name or "project"
                thread_id = session_id or f"session-{root_name}"
                prefix = f"resume({thread_id})" if resume else f"new({thread_id})"
                if model:
                    prefix += f" model={model}"
                reply = f"{prefix}: {prompt}".strip()

                if output_path:
                    Path(output_path).write_text(reply + "\\n", encoding="utf-8")

                events = [
                    {"type": "thread.started", "thread_id": thread_id},
                    {"type": "turn.started"},
                    {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": reply}},
                    {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}},
                ]
                for item in events:
                    print(json.dumps(item, ensure_ascii=False))
                return 0


            if __name__ == "__main__":
                raise SystemExit(main())
            """
        ),
        executable=True,
    )


def write_projects_registry(bot_home: Path, projects: list[dict[str, object]]) -> Path:
    path = bot_home / "output" / "telegram" / "projects.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"projects": projects}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_thesis_manifest(root: Path, timestamp: str, output_label: str = "verify") -> None:
    manifest = root / "output" / "runs" / TEST_WORK_ID / "thesis" / f"{timestamp}-{output_label}.meta.json"
    output = root / "output" / "runs" / TEST_WORK_ID / "thesis" / f"{timestamp}-{output_label}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("trace\n", encoding="utf-8")
    payload = {
        "timestamp": timestamp,
        "preset": output_label,
        "work_id": TEST_WORK_ID,
        "work_title": "Demo work",
        "target": {
            "absolute": str(root / TEST_THESIS_SECTION),
            "relative": TEST_THESIS_SECTION.as_posix(),
            "state": "existing",
        },
        "output_file": str(output),
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_runtime_status_fixture(
    runtime_dir: Path,
    *,
    record_id: str,
    entity_kind: str,
    project_id: str,
    project_title: str,
    project_root: Path,
    work_id: str | None = None,
    work_title: str | None = None,
    lane: str | None = None,
    profile: str | None = None,
    action: str | None = None,
    status: str = "succeeded",
    stage: str = "completed",
    summary: str = "Runtime finished successfully.",
    attachments: dict[str, str] | None = None,
    failure: dict[str, object] | None = None,
    blockers: list[dict[str, object]] | None = None,
    repair_decision: dict[str, object] | None = None,
    repair_iteration: int | None = None,
    terminal_reason: str | None = None,
    thesis_repair_plan: dict[str, object] | None = None,
    contract_gates: list[dict[str, object]] | None = None,
    checkpoints: list[dict[str, object]] | None = None,
) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    attachment_payload: dict[str, dict[str, object]] = {}
    for name, raw_path in (attachments or {}).items():
        path = Path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(f"{name}\n", encoding="utf-8")
        attachment_payload[name] = {
            "path": str(path),
            "exists": path.exists(),
        }
    status_path = runtime_dir / "status.json"
    attachment_payload["status"] = {
        "path": str(status_path),
        "exists": True,
    }
    payload = {
        "version": "v2",
        "record_id": record_id,
        "entity_kind": entity_kind,
        "status": status,
        "stage": stage,
        "project_id": project_id,
        "project_title": project_title,
        "project_root": str(project_root),
        "work_id": work_id,
        "work_title": work_title,
        "lane": lane,
        "profile": profile,
        "action": action,
        "started_at": "2026-04-18T10:00:00+00:00",
        "finished_at": "2026-04-18T10:01:00+00:00",
        "summary": summary,
        "failure": failure,
        "blockers": blockers or [],
        "repair_decision": repair_decision,
        "repair_iteration": repair_iteration,
        "terminal_reason": terminal_reason,
        "thesis_repair_plan": thesis_repair_plan,
        "contract_gates": contract_gates or [],
        "checkpoints": checkpoints
        or [
            {
                "name": "finished",
                "status": status,
                "stage": stage,
                "timestamp": "2026-04-18T10:01:00+00:00",
                "message": summary,
            }
        ],
        "attachments": attachment_payload,
    }
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status_path


class FakeApi:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.documents: list[dict[str, object]] = []
        self.callback_answers: list[dict[str, object]] = []

    def send_message(self, chat_id: int, text: str, *, reply_markup: dict | None = None) -> dict:
        payload = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
        self.messages.append(payload)
        return payload

    def send_document(self, chat_id: int, file_path: str | Path, *, caption: str | None = None) -> dict:
        payload = {"chat_id": chat_id, "file_path": str(file_path), "caption": caption}
        self.documents.append(payload)
        return payload

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict:
        payload = {"callback_query_id": callback_query_id, "text": text}
        self.callback_answers.append(payload)
        return payload


class FakeChatService:
    def __init__(self) -> None:
        self.started: list[dict[str, object]] = []
        self.exports: list[dict[str, str]] = []
        self.notifications: list[AgentTurnNotification] = []
        self.states: dict[str, ProjectChatState] = {}
        self.raise_busy = False
        self.prompt_builder = PromptBuilder()

    def get_project_state(self, project_id: str) -> ProjectChatState:
        return self.states.get(project_id, ProjectChatState(project_id=project_id))

    def describe_project_focus(self, project_id: str) -> str:
        state = self.get_project_state(project_id)
        return state.last_assistant_summary or state.last_user_message or "Пока без истории."

    def start_turn(self, project_id: str, prompt: str) -> dict[str, object]:
        if self.raise_busy:
            raise AgentBusyError("⏳ Я уже отвечаю в другом проекте.")
        profile = self.prompt_builder.classify_intent(prompt)
        payload = {
            "project_id": project_id,
            "prompt": prompt,
            "profile": profile,
            "detected_intent": PROFILE_LABELS[profile],
            "expected_output": PROFILE_EXPECTATIONS[profile],
        }
        self.started.append(payload)
        self.states[project_id] = ProjectChatState(
            project_id=project_id,
            session_id="fake-session",
            last_activity_at="2026-04-17T10:00:00+00:00",
            last_user_message=prompt,
            last_assistant_summary=self.get_project_state(project_id).last_assistant_summary,
            busy=True,
            last_export_path=self.get_project_state(project_id).last_export_path,
        )
        return payload

    def sync_active_task(self) -> list[AgentTurnNotification]:
        return []

    def drain_notifications(self) -> list[AgentTurnNotification]:
        items = list(self.notifications)
        self.notifications.clear()
        return items

    def record_export(self, project_id: str, export_path: str | Path) -> None:
        state = self.get_project_state(project_id)
        self.exports.append({"project_id": project_id, "path": str(export_path)})
        self.states[project_id] = ProjectChatState(
            project_id=project_id,
            session_id=state.session_id,
            last_activity_at=state.last_activity_at,
            last_user_message=state.last_user_message,
            last_assistant_summary=state.last_assistant_summary,
            busy=False,
            last_export_path=str(export_path),
        )

    def describe_active_task(self, payload: dict[str, object]) -> str:
        return "⏳ Я уже отвечаю в другом проекте."


class FakeMailer:
    def __init__(self, recipient_email: str = "reader@example.com", error: Exception | None = None) -> None:
        self.recipient_email = recipient_email
        self.error = error
        self.calls: list[dict[str, str]] = []

    def send_export(self, file_path: str | Path, artifact_kind: str) -> None:
        self.calls.append({"file_path": str(file_path), "artifact_kind": artifact_kind})
        if self.error:
            raise self.error


class FakeCommandResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeLaunchctl:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.loaded = False
        self.pid = 4242

    def __call__(self, command: list[str]) -> FakeCommandResult:
        self.commands.append(command)
        if command[:2] == ["launchctl", "print"]:
            if self.loaded:
                return FakeCommandResult(stdout=f"service = {{\n\tpid = {self.pid}\n}}\n")
            return FakeCommandResult(returncode=113, stderr="service not loaded")
        if command[:2] == ["launchctl", "bootstrap"]:
            self.loaded = True
            return FakeCommandResult()
        if command[:2] == ["launchctl", "kickstart"]:
            self.loaded = True
            return FakeCommandResult()
        if command[:2] == ["launchctl", "bootout"]:
            if self.loaded:
                self.loaded = False
                return FakeCommandResult()
            return FakeCommandResult(returncode=36, stderr="service not loaded")
        return FakeCommandResult()


class DummySmtpClient:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: int,
        kind: str,
        sink: list["DummySmtpClient"],
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.kind = kind
        self.starttls_called = False
        self.ehlo_calls = 0
        self.login_called_with: tuple[str, str] | None = None
        self.sent_messages: list[object] = []
        sink.append(self)

    def __enter__(self) -> "DummySmtpClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def ehlo(self) -> None:
        self.ehlo_calls += 1

    def starttls(self, context=None) -> None:
        self.starttls_called = True

    def login(self, username: str, password: str) -> None:
        self.login_called_with = (username, password)

    def send_message(self, message: object) -> None:
        self.sent_messages.append(message)


class TelegramConsoleConfigTests(unittest.TestCase):
    def test_smtp_is_disabled_when_required_env_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "test-token",
                    "TELEGRAM_ALLOWED_CHAT_ID": "1",
                },
                clear=True,
            ):
                config = TelegramConsoleConfig.from_env(tempdir)

        self.assertEqual(config.bot_home_dir, Path(tempdir).resolve())
        self.assertIsNone(config.smtp_settings)

    def test_smtp_settings_are_loaded_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "test-token",
                    "TELEGRAM_ALLOWED_CHAT_ID": "1",
                    "SMTP_HOST": "smtp.example.com",
                    "SMTP_PORT": "2525",
                    "SMTP_SECURITY": "ssl",
                    "SMTP_USERNAME": "mailer",
                    "SMTP_PASSWORD": "secret",
                    "SMTP_FROM_EMAIL": "bot@example.com",
                    "SMTP_TO_EMAIL": "reader@example.com",
                    "SMTP_TIMEOUT_SECONDS": "45",
                },
                clear=True,
            ):
                config = TelegramConsoleConfig.from_env(tempdir)

        settings = config.smtp_settings
        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual(settings.host, "smtp.example.com")
        self.assertEqual(settings.port, 2525)
        self.assertEqual(settings.security, "ssl")
        self.assertEqual(settings.username, "mailer")
        self.assertEqual(settings.password, "secret")
        self.assertEqual(settings.from_email, "bot@example.com")
        self.assertEqual(settings.to_email, "reader@example.com")
        self.assertEqual(settings.timeout_seconds, 45)
        self.assertEqual(settings.from_name, "Академический штурман")


class SmtpDocxSenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.docx_path = self.root / "output" / "docx" / "demo.docx"
        self.docx_path.parent.mkdir(parents=True, exist_ok=True)
        self.docx_path.write_bytes(b"fake docx")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_build_export_message_contains_text_html_and_attachment(self) -> None:
        sender = SmtpDocxSender(
            SmtpSettings(
                host="smtp.example.com",
                from_email="bot@example.com",
                to_email="reader@example.com",
            )
        )

        message = sender.build_export_message(self.docx_path, "статья")

        self.assertEqual(message["Subject"], "Готовый DOCX: demo.docx")
        plain = message.get_body(preferencelist=("plain",))
        html = message.get_body(preferencelist=("html",))
        self.assertIsNotNone(plain)
        self.assertIsNotNone(html)
        self.assertIn("Готовый DOCX уже подготовлен", plain.get_content())
        self.assertIn("Тип результата: статья", plain.get_content())
        self.assertIn("demo.docx", html.get_content())
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "demo.docx")

    def test_send_export_uses_starttls_when_requested(self) -> None:
        clients: list[DummySmtpClient] = []
        sender = SmtpDocxSender(
            SmtpSettings(
                host="smtp.example.com",
                from_email="bot@example.com",
                to_email="reader@example.com",
                username="mailer",
                password="secret",
                security="starttls",
            )
        )

        with patch(
            "telegram_console.email_delivery.smtplib.SMTP",
            side_effect=lambda host, port, timeout: DummySmtpClient(
                host,
                port,
                timeout=timeout,
                kind="smtp",
                sink=clients,
            ),
        ):
            sender.send_export(self.docx_path, "диплом")

        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].starttls_called)
        self.assertEqual(clients[0].login_called_with, ("mailer", "secret"))
        self.assertEqual(len(clients[0].sent_messages), 1)

    def test_send_export_uses_ssl_client_when_requested(self) -> None:
        clients: list[DummySmtpClient] = []
        sender = SmtpDocxSender(
            SmtpSettings(
                host="smtp.example.com",
                from_email="bot@example.com",
                to_email="reader@example.com",
                security="ssl",
            )
        )

        with patch(
            "telegram_console.email_delivery.smtplib.SMTP",
            side_effect=AssertionError("SMTP should not be used for SSL"),
        ):
            with patch(
                "telegram_console.email_delivery.smtplib.SMTP_SSL",
                side_effect=lambda host, port, timeout: DummySmtpClient(
                    host,
                    port,
                    timeout=timeout,
                    kind="ssl",
                    sink=clients,
                ),
            ):
                sender.send_export(self.docx_path, "статья")

        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].kind, "ssl")
        self.assertFalse(clients[0].starttls_called)
        self.assertEqual(len(clients[0].sent_messages), 1)


class WorkflowOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)
        self.orchestrator = WorkflowOrchestrator(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def wait_for_completion(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.orchestrator.sync_active_run()
            if not self.orchestrator.store.get_active_run():
                return
            time.sleep(0.1)
        self.fail("active run did not complete in time")

    def test_list_targets_excludes_readme(self) -> None:
        targets = self.orchestrator.list_targets("thesis", "write-section")
        self.assertEqual(targets, ["manuscript/sections/01-introduction.md"])

    def test_article_bundle_status_reports_missing_files(self) -> None:
        status = self.orchestrator.get_artifact_status("article:demo")
        self.assertEqual(status["kind"], "article-bundle")
        self.assertTrue(status["files"]["brief"]["exists"])
        self.assertTrue(status["files"]["final"]["exists"])
        self.assertIn("evidence", status["missing"])
        self.assertIn("docx", status["missing"])
        self.assertEqual(status["summary"]["kind"], "article-bundle-summary")
        self.assertEqual(status["summary"]["slug"], "demo")
        self.assertEqual(status["summary"]["blocker_count"], 0)
        self.assertEqual(status["summary"]["suggested_next_action"], "review")

    def test_thesis_section_status_includes_compact_summary(self) -> None:
        status = self.orchestrator.get_artifact_status("thesis:manuscript/sections/01-introduction.md")

        self.assertEqual(status["kind"], "thesis-section")
        self.assertEqual(status["summary"]["kind"], "thesis-section-summary")
        self.assertEqual(status["summary"]["target"], TEST_THESIS_SECTION.as_posix())
        self.assertTrue(status["summary"]["review_present"])
        self.assertEqual(status["summary"]["blocker_count"], 0)
        self.assertEqual(status["summary"]["suggested_next_action"], "write-section")

    def test_work_state_reports_compact_state_and_next_safe_action(self) -> None:
        write_raw_manifest(self.root, "thesis-v1")
        write_raw_manifest(self.root, "ru-law-article-v1")

        state = self.orchestrator.get_artifact_status("work")

        self.assertEqual(state["kind"], "work-state")
        self.assertEqual(state["work_id"], TEST_WORK_ID)
        self.assertEqual(state["work_title"], "Demo work")
        self.assertEqual(state["active_lanes"], ["thesis", "article"])
        self.assertEqual(state["thesis"]["summary"]["section_count"], 1)
        self.assertEqual(state["article"]["summary"]["bundle_count"], 1)
        self.assertEqual(state["standards"]["profiles"]["article"]["raw_status"], "available")
        self.assertEqual(state["assessment_scope"]["depth"], "signals-only")
        self.assertIn("source-verification", state["assessment_scope"]["does_not_replace"])
        self.assertEqual(state["known_blocker_count"], 0)
        self.assertEqual(state["suggested_next_action"]["action_id"], "article-review")
        self.assertIn("launch-academic review", state["suggested_next_action"]["command"])
        self.assertNotIn("submission-ready", state["suggested_next_action"]["command"])

    def test_work_state_empty_work_avoids_export_suggestion(self) -> None:
        add_empty_work_scaffold(self.root)
        write_raw_manifest(self.root, "thesis-v1")
        write_raw_manifest(self.root, "ru-law-article-v1")

        state = self.orchestrator.get_artifact_status("work", work_id="empty-work")

        self.assertEqual(state["work_id"], "empty-work")
        self.assertEqual(state["thesis"]["sections"], [])
        self.assertEqual(state["article"]["bundles"], [])
        self.assertEqual(state["known_blocker_count"], 0)
        self.assertIsNotNone(state["suggested_next_action"])
        self.assertNotEqual(state["suggested_next_action"]["action_id"], "export-docx")

    def test_work_state_suggests_export_for_clean_reviewed_article_bundle(self) -> None:
        write_raw_manifest(self.root, "thesis-v1")
        write_raw_manifest(self.root, "ru-law-article-v1")
        write_file(self.root / TEST_WORK_ROOT / "articles" / "reviews" / "demo.md", "# Review\n")

        state = self.orchestrator.get_artifact_status("work")

        self.assertEqual(state["known_blocker_count"], 0)
        self.assertEqual(state["suggested_next_action"]["action_id"], "export-article-docx")
        self.assertIn("export-article-docx", state["suggested_next_action"]["command"])

    def test_work_state_routes_blockers_to_repair_before_export(self) -> None:
        write_raw_manifest(self.root, "thesis-v1")
        write_raw_manifest(self.root, "ru-law-article-v1")
        run_dir = self.orchestrator.store.runs_dir / "article-blocker-runtime"
        write_runtime_status_fixture(
            run_dir,
            record_id="default:20260418-article-review",
            entity_kind="workflow-run",
            project_id="default",
            project_title=self.root.name,
            project_root=self.root,
            work_id=TEST_WORK_ID,
            work_title="Demo work",
            lane="article",
            action="review",
            summary="Article review found a primary support blocker.",
            blockers=[
                {
                    "category": "primary-support",
                    "code": "unsupported-lead-claim",
                    "message": "Lead claim still needs primary support.",
                    "repairable": True,
                    "blocks_statuses": ["submission-ready"],
                }
            ],
            repair_decision={
                "action": "repair",
                "reason": "repairable-blockers-available",
                "repair_iteration": 1,
                "blocker_count": 1,
            },
            repair_iteration=0,
            terminal_reason="blocked-primary-support",
        )

        state = self.orchestrator.get_artifact_status("work")

        self.assertGreaterEqual(state["known_blocker_count"], 1)
        self.assertEqual(state["suggested_next_action"]["action_id"], "article-repair")
        self.assertEqual(state["suggested_next_action"]["lane"], "article")
        self.assertIn("launch-academic repair", state["suggested_next_action"]["command"])
        self.assertNotIn("export", state["suggested_next_action"]["command"])

    def test_work_state_flags_standards_raw_missing_and_conflicts(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        rewrite_work_profiles(self.root, article_profile="journal-jrp")

        state = self.orchestrator.get_artifact_status("work")

        self.assertEqual(state["standards"]["profiles"]["article"]["profile_id"], "journal-jrp")
        self.assertEqual(state["standards"]["profiles"]["article"]["raw_status"], "missing")
        blocker_codes = {item["code"] for item in state["known_blockers"]}
        self.assertIn("article-standards-raw-missing", blocker_codes)
        self.assertIn("article-standards-conflict", blocker_codes)
        self.assertEqual(state["suggested_next_action"]["action_id"], "standards-refresh")
        standards_action = state["suggested_next_action"]
        self.assertTrue(standards_action["blocks_export"])
        self.assertFalse(standards_action["blocks_workflow"])
        self.assertIn("export", standards_action["blocking_scope"])
        self.assertEqual(state["work_continuation_action"]["action_id"], "article-review")

        write_file(self.root / TEST_WORK_ROOT / "articles" / "reviews" / "demo.md", "# Review\n")
        state = self.orchestrator.get_artifact_status("work")

        self.assertEqual(state["suggested_next_action"]["action_id"], "standards-refresh")
        self.assertEqual(state["work_continuation_action"]["action_id"], "draft-next")

        write_raw_manifest(self.root, "thesis-v1")
        write_raw_manifest(self.root, "journal-jrp")
        state = self.orchestrator.get_artifact_status("work")

        self.assertEqual(state["standards"]["profiles"]["article"]["raw_status"], "available")
        blocker_codes = {item["code"] for item in state["known_blockers"]}
        self.assertNotIn("article-standards-raw-missing", blocker_codes)
        self.assertIn("article-standards-conflict", blocker_codes)
        self.assertEqual(state["suggested_next_action"]["action_id"], "standards-review")
        self.assertEqual(state["work_continuation_action"]["action_id"], "draft-next")

    def test_work_state_prioritizes_safe_work_step_when_standards_do_not_block_export_candidate(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        rewrite_work_profiles(self.root, article_profile="journal-jrp")
        (self.root / TEST_THESIS_REVIEW).unlink()

        state = self.orchestrator.get_artifact_status("work")

        self.assertEqual(state["suggested_next_action"]["action_id"], "article-review")
        standards_action = next(item for item in state["next_actions"] if item["action_id"] == "standards-refresh")
        self.assertTrue(standards_action["blocks_export"])
        self.assertFalse(standards_action["blocks_workflow"])
        self.assertEqual(state["work_continuation_action"]["action_id"], "article-review")

    def test_work_state_marks_checklist_finalization_as_public_action(self) -> None:
        write_raw_manifest(self.root, "thesis-v1")
        write_raw_manifest(self.root, "ru-law-article-v1")
        write_file(self.root / TEST_WORK_ROOT / "articles" / "reviews" / "demo.md", "# Review\n")
        (self.root / TEST_ARTICLE_CHECKLIST).unlink()

        state = self.orchestrator.get_artifact_status("work")

        action = state["suggested_next_action"]
        self.assertEqual(action["action_id"], "article-finalize")
        self.assertEqual(action["intent"], "finalize-checklist")
        self.assertIsNone(action["fallback_for"])
        self.assertIn("launch-academic finalize", action["command"])
        self.assertNotIn("launch-academic repair", action["command"])

    def test_single_active_run_and_manifest_resolution(self) -> None:
        previous_sleep = os.environ.get("TEST_SLEEP_SECONDS")
        os.environ["TEST_SLEEP_SECONDS"] = "1"
        try:
            active = self.orchestrator.start_run(
                "thesis",
                "write-section",
                "manuscript/sections/01-introduction.md",
                notes="check",
            )
            self.assertEqual(active["action"], "write-section")
            self.assertTrue(str(active["run_id"]).startswith("default:"))

            with self.assertRaises(RunBusyError):
                self.orchestrator.start_run(
                    "thesis",
                    "verify",
                    "manuscript/sections/01-introduction.md",
                )
        finally:
            if previous_sleep is None:
                os.environ.pop("TEST_SLEEP_SECONDS", None)
            else:
                os.environ["TEST_SLEEP_SECONDS"] = previous_sleep

        self.wait_for_completion()
        notices = self.orchestrator.drain_notifications()
        self.assertEqual(len(notices), 1)
        record = notices[0]
        self.assertEqual(record.status, "success")
        self.assertEqual(record.project_id, "default")
        self.assertTrue(record.manifest_path)
        self.assertTrue(record.output_file)
        self.assertTrue(Path(record.manifest_path).exists())
        self.assertTrue(Path(record.output_file).exists())

    def test_export_docx_uses_project_scripts(self) -> None:
        thesis = self.orchestrator.export_docx("thesis")
        article = self.orchestrator.export_docx("article:demo")
        self.assertTrue(Path(thesis["path"]).exists())
        self.assertTrue(Path(article["path"]).exists())

    def test_stale_active_run_becomes_interrupted(self) -> None:
        run_dir = self.orchestrator.store.runs_dir / "stale-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        request = {
            "run_id": "default:stale-run",
            "run_dir": str(run_dir),
            "lane": "thesis",
            "action": "verify",
            "started_at": "2026-01-01T00:00:00+00:00",
            "target": "manuscript/sections/01-introduction.md",
        }
        self.orchestrator.store.write_json(run_dir / "request.json", request)
        self.orchestrator.store.set_active_run(
            {
                "run_id": "default:stale-run",
                "run_dir": str(run_dir),
                "pid": 999999,
                "lane": "thesis",
                "action": "verify",
                "started_at": request["started_at"],
                "target": request["target"],
            }
        )

        completed = self.orchestrator.sync_active_run()
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].status, "interrupted")
        self.assertIsNone(self.orchestrator.store.get_active_run())
        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "interrupted")
        self.assertEqual(status["failure"]["category"], "runtime")
        self.assertEqual(status["failure"]["code"], "missing-result")

    def test_localized_errors_and_russian_article_aliases(self) -> None:
        self.assertEqual(self.orchestrator._resolve_article_input("тема: Биометрия"), ("topic", "Биометрия"))
        self.assertEqual(
            self.orchestrator._resolve_article_input("бриф:articles/briefs/demo.md"),
            ("brief", "articles/briefs/demo.md"),
        )

        with self.assertRaisesRegex(Exception, "Не нашла файл"):
            self.orchestrator._normalize_relative_path("missing.md")

        busy_text = self.orchestrator.describe_active_run(
            {
                "lane": "thesis",
                "action": "verify",
                "target": "manuscript/sections/01-introduction.md",
                "project_title": "Демо-проект",
            }
        )
        self.assertIn("Сейчас уже идет другой запуск", busy_text)
        self.assertIn("Демо-проект", busy_text)
        self.assertIn("проверка", busy_text)

    def test_article_repair_finalization_enriches_runtime_status_and_bundle_state(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        rewrite_work_profiles(self.root, article_profile="journal-jrp")
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        profile = resolve_standard_profile(self.root, workspace, work, lane="article", requested_profile_id=None)
        bundle = article_bundle_paths(work, "repair-demo")
        write_file(bundle["brief"], "# Repair brief\n")
        write_file(bundle["draft"], "# Repair draft\n")
        contract = build_article_execution_contract(
            work=work,
            profile=profile,
            action="repair",
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path],
            bundle=bundle,
            topic=None,
            input_brief_path=None,
            target_path=bundle["draft"],
            target_rel=relative_to_workspace(workspace, bundle["draft"]),
        )
        timestamp = "20260418-101500"
        output_file = work.article.paths.output_runs_dir / f"{timestamp}-repair-repair-demo.md"
        manifest_file = work.article.paths.output_runs_dir / f"{timestamp}-repair-repair-demo.meta.json"
        write_file(output_file, "Post-repair verdict: strong-draft-with-blockers\n")
        manifest_payload = {
            "timestamp": timestamp,
            "command": "repair",
            "work_id": work.slug,
            "work_title": work.title,
            "profile_id": profile.resolved_profile_id,
            "requested_profile_id": profile.requested_profile_id,
            "resolved_profile_id": profile.resolved_profile_id,
            "fallback_profile_id": profile.fallback_profile_id,
            "profile_raw_dir": str(profile.raw_dir),
            "profile_conflict_flag": profile.conflict_flag,
            "profile_status": profile.profile_status,
            "search_enabled": True,
            "topic": None,
            "input_brief": None,
            "target_path": relative_to_workspace(workspace, bundle["draft"]),
            "root_dir": str(self.root),
            "output_file": str(output_file),
            "bundle": {
                "slug": "repair-demo",
                "brief": str(bundle["brief"]),
                "evidence_pack": str(bundle["evidence_pack"]),
                "claim_map": str(bundle["claim_map"]),
                "draft": str(bundle["draft"]),
                "review": str(bundle["review"]),
                "final_markdown": str(bundle["final_markdown"]),
                "checklist": str(bundle["checklist"]),
                "docx": str(bundle["docx"]),
                "state_manifest": str(article_bundle_manifest_path(work, "repair-demo")),
            },
            "related_context": [str(self.root / "AGENTS.md")],
            "execution_contract": contract.to_dict(),
        }
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        run_dir = self.orchestrator.store.runs_dir / "article-repair-runtime"
        run_dir.mkdir(parents=True, exist_ok=True)
        request = {
            "run_id": "default:20260418-article-repair",
            "lane": "article",
            "action": "repair",
            "started_at": "2026-04-18T10:15:00+00:00",
            "project_id": "default",
            "project_title": self.root.name,
            "project_root": str(self.root),
            "work_id": work.slug,
            "work_title": work.title,
            "target": relative_to_workspace(workspace, bundle["draft"]),
        }
        result = {
            "status": "success",
            "returncode": 0,
            "started_at": request["started_at"],
            "finished_at": "2026-04-18T10:16:00+00:00",
            "log_path": str(run_dir / "launcher.log"),
        }

        record = self.orchestrator._finalize_runtime_run(run_dir, request, result)

        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["repair_iteration"], 1)
        self.assertEqual(status["repair_decision"]["action"], "repair")
        self.assertEqual(status["terminal_reason"], "blocked-standards")
        self.assertTrue(any(item["category"] == "standards-consistency" for item in status["blockers"]))
        self.assertTrue(any(item["category"] == "primary-support" for item in status["blockers"]))

        bundle_state = json.loads(article_bundle_manifest_path(work, "repair-demo").read_text(encoding="utf-8"))
        self.assertEqual(bundle_state["current_status"], "strong-draft-with-blockers")
        self.assertEqual(bundle_state["repair_iteration"], 1)
        self.assertEqual(bundle_state["repair_decision"]["action"], "repair")
        self.assertEqual(bundle_state["terminal_reason"], "blocked-standards")
        self.assertIn(record.record_id, bundle_state["latest_runtime_record_ids"])

    def test_article_review_finalization_extracts_primary_support_blockers_from_review_artifact(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        profile = resolve_standard_profile(self.root, workspace, work, lane="article", requested_profile_id=None)
        bundle = article_bundle_paths(work, "review-artifact-demo")
        write_file(bundle["brief"], "# Review artifact brief\n")
        write_file(bundle["evidence_pack"], "# Evidence\n")
        write_file(bundle["claim_map"], "# Claim map\n")
        write_file(bundle["draft"], "# Review artifact draft\n")
        write_file(
            bundle["review"],
            textwrap.dedent(
                """\
                # Review sheet

                - Verdict: `strong-draft-with-blockers`
                - Primary support is sufficient: no
                - Unsafe or overstated claims: Key causal claim still relies on secondary literature only.
                - Checklist blockers: Need primary-source support for the central doctrinal claim.
                """
            ),
        )
        contract = build_article_execution_contract(
            work=work,
            profile=profile,
            action="review",
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path],
            bundle=bundle,
            topic=None,
            input_brief_path=None,
            target_path=bundle["draft"],
            target_rel=relative_to_workspace(workspace, bundle["draft"]),
        )
        timestamp = "20260418-101800"
        output_file = work.article.paths.output_runs_dir / f"{timestamp}-review-review-artifact-demo.md"
        manifest_file = work.article.paths.output_runs_dir / f"{timestamp}-review-review-artifact-demo.meta.json"
        write_file(output_file, "Evaluator completed. See managed review artifact for the verdict.\n")
        manifest_payload = {
            "timestamp": timestamp,
            "command": "review",
            "work_id": work.slug,
            "work_title": work.title,
            "profile_id": profile.resolved_profile_id,
            "requested_profile_id": profile.requested_profile_id,
            "resolved_profile_id": profile.resolved_profile_id,
            "fallback_profile_id": profile.fallback_profile_id,
            "profile_raw_dir": str(profile.raw_dir),
            "profile_conflict_flag": profile.conflict_flag,
            "profile_status": profile.profile_status,
            "search_enabled": True,
            "topic": None,
            "input_brief": None,
            "target_path": relative_to_workspace(workspace, bundle["draft"]),
            "root_dir": str(self.root),
            "output_file": str(output_file),
            "bundle": {
                "slug": "review-artifact-demo",
                "brief": str(bundle["brief"]),
                "evidence_pack": str(bundle["evidence_pack"]),
                "claim_map": str(bundle["claim_map"]),
                "draft": str(bundle["draft"]),
                "review": str(bundle["review"]),
                "final_markdown": str(bundle["final_markdown"]),
                "checklist": str(bundle["checklist"]),
                "docx": str(bundle["docx"]),
                "state_manifest": str(article_bundle_manifest_path(work, "review-artifact-demo")),
            },
            "related_context": [str(self.root / "AGENTS.md")],
            "execution_contract": contract.to_dict(),
        }
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        run_dir = self.orchestrator.store.runs_dir / "article-review-artifact-runtime"
        run_dir.mkdir(parents=True, exist_ok=True)
        request = {
            "run_id": "default:20260418-article-review-artifact",
            "lane": "article",
            "action": "review",
            "started_at": "2026-04-18T10:18:00+00:00",
            "project_id": "default",
            "project_title": self.root.name,
            "project_root": str(self.root),
            "work_id": work.slug,
            "work_title": work.title,
            "target": relative_to_workspace(workspace, bundle["draft"]),
        }
        result = {
            "status": "success",
            "returncode": 0,
            "started_at": request["started_at"],
            "finished_at": "2026-04-18T10:19:00+00:00",
            "log_path": str(run_dir / "launcher.log"),
        }

        self.orchestrator._finalize_runtime_run(run_dir, request, result)

        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["terminal_reason"], "blocked-primary-support")
        self.assertEqual(status["repair_decision"]["action"], "repair")
        self.assertTrue(any(item["category"] == "primary-support" for item in status["blockers"]))

        bundle_state = json.loads(article_bundle_manifest_path(work, "review-artifact-demo").read_text(encoding="utf-8"))
        self.assertEqual(bundle_state["current_status"], "strong-draft-with-blockers")
        self.assertEqual(bundle_state["terminal_reason"], "blocked-primary-support")
        self.assertTrue(any(item["category"] == "primary-support" for item in bundle_state["blockers"]))

    def test_article_review_finalization_downgrades_submission_ready_from_checklist_blockers(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        profile = resolve_standard_profile(self.root, workspace, work, lane="article", requested_profile_id=None)
        bundle = article_bundle_paths(work, "checklist-blocker-demo")
        write_file(bundle["brief"], "# Checklist blocker brief\n")
        write_file(bundle["evidence_pack"], "# Evidence\n")
        write_file(bundle["claim_map"], "# Claim map\n")
        write_file(bundle["draft"], "# Checklist blocker draft\n")
        write_file(bundle["review"], "# Review sheet\n")
        write_file(bundle["final_markdown"], "# Final markdown\n")
        write_file(
            bundle["checklist"],
            textwrap.dedent(
                """\
                # Submission Checklist

                - Status: `submission-ready`
                - Formatting blockers: none
                - What still blocks formal submission: Need primary-source support for the lead empirical claim.
                """
            ),
        )
        contract = build_article_execution_contract(
            work=work,
            profile=profile,
            action="review",
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path],
            bundle=bundle,
            topic=None,
            input_brief_path=None,
            target_path=bundle["draft"],
            target_rel=relative_to_workspace(workspace, bundle["draft"]),
        )
        timestamp = "20260418-101900"
        output_file = work.article.paths.output_runs_dir / f"{timestamp}-review-checklist-blocker-demo.md"
        manifest_file = work.article.paths.output_runs_dir / f"{timestamp}-review-checklist-blocker-demo.meta.json"
        write_file(output_file, "Evaluator verdict: submission-ready\n")
        manifest_payload = {
            "timestamp": timestamp,
            "command": "review",
            "work_id": work.slug,
            "work_title": work.title,
            "profile_id": profile.resolved_profile_id,
            "requested_profile_id": profile.requested_profile_id,
            "resolved_profile_id": profile.resolved_profile_id,
            "fallback_profile_id": profile.fallback_profile_id,
            "profile_raw_dir": str(profile.raw_dir),
            "profile_conflict_flag": profile.conflict_flag,
            "profile_status": profile.profile_status,
            "search_enabled": True,
            "topic": None,
            "input_brief": None,
            "target_path": relative_to_workspace(workspace, bundle["draft"]),
            "root_dir": str(self.root),
            "output_file": str(output_file),
            "bundle": {
                "slug": "checklist-blocker-demo",
                "brief": str(bundle["brief"]),
                "evidence_pack": str(bundle["evidence_pack"]),
                "claim_map": str(bundle["claim_map"]),
                "draft": str(bundle["draft"]),
                "review": str(bundle["review"]),
                "final_markdown": str(bundle["final_markdown"]),
                "checklist": str(bundle["checklist"]),
                "docx": str(bundle["docx"]),
                "state_manifest": str(article_bundle_manifest_path(work, "checklist-blocker-demo")),
            },
            "related_context": [str(self.root / "AGENTS.md")],
            "execution_contract": contract.to_dict(),
        }
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        run_dir = self.orchestrator.store.runs_dir / "article-checklist-blocker-runtime"
        run_dir.mkdir(parents=True, exist_ok=True)
        request = {
            "run_id": "default:20260418-article-checklist-blocker",
            "lane": "article",
            "action": "review",
            "started_at": "2026-04-18T10:19:00+00:00",
            "project_id": "default",
            "project_title": self.root.name,
            "project_root": str(self.root),
            "work_id": work.slug,
            "work_title": work.title,
            "target": relative_to_workspace(workspace, bundle["draft"]),
        }
        result = {
            "status": "success",
            "returncode": 0,
            "started_at": request["started_at"],
            "finished_at": "2026-04-18T10:20:00+00:00",
            "log_path": str(run_dir / "launcher.log"),
        }

        self.orchestrator._finalize_runtime_run(run_dir, request, result)

        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["terminal_reason"], "blocked-primary-support")
        self.assertEqual(status["repair_decision"]["action"], "repair")
        self.assertTrue(any(item["category"] == "primary-support" for item in status["blockers"]))

        bundle_state = json.loads(article_bundle_manifest_path(work, "checklist-blocker-demo").read_text(encoding="utf-8"))
        self.assertEqual(bundle_state["current_status"], "strong-draft-with-blockers")
        self.assertEqual(bundle_state["terminal_reason"], "blocked-primary-support")
        self.assertTrue(any(item["category"] == "primary-support" for item in bundle_state["blockers"]))

    def test_article_review_finalization_marks_ready_with_caveats_for_strong_draft(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        profile = resolve_standard_profile(self.root, workspace, work, lane="article", requested_profile_id=None)
        bundle = article_bundle_paths(work, "review-demo")
        write_file(bundle["brief"], "# Review brief\n")
        write_file(bundle["evidence_pack"], "# Evidence\n")
        write_file(bundle["claim_map"], "# Claim map\n")
        write_file(bundle["draft"], "# Review draft\n")
        write_file(
            bundle["review"],
            textwrap.dedent(
                """\
                # Review sheet

                - Verdict: `strong-draft`
                - Primary support is sufficient: yes
                - Checklist blockers: none
                """
            ),
        )
        write_file(bundle["final_markdown"], "# Final markdown\n")
        write_file(
            bundle["checklist"],
            textwrap.dedent(
                """\
                # Submission Checklist

                - Status: `strong-draft`
                - Formatting blockers: none
                - What still blocks formal submission: none
                """
            ),
        )
        contract = build_article_execution_contract(
            work=work,
            profile=profile,
            action="review",
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path],
            bundle=bundle,
            topic=None,
            input_brief_path=None,
            target_path=bundle["draft"],
            target_rel=relative_to_workspace(workspace, bundle["draft"]),
        )
        timestamp = "20260418-102000"
        output_file = work.article.paths.output_runs_dir / f"{timestamp}-review-review-demo.md"
        manifest_file = work.article.paths.output_runs_dir / f"{timestamp}-review-review-demo.meta.json"
        write_file(output_file, "Evaluator verdict: strong-draft\n")
        manifest_payload = {
            "timestamp": timestamp,
            "command": "review",
            "work_id": work.slug,
            "work_title": work.title,
            "profile_id": profile.resolved_profile_id,
            "requested_profile_id": profile.requested_profile_id,
            "resolved_profile_id": profile.resolved_profile_id,
            "fallback_profile_id": profile.fallback_profile_id,
            "profile_raw_dir": str(profile.raw_dir),
            "profile_conflict_flag": profile.conflict_flag,
            "profile_status": profile.profile_status,
            "search_enabled": True,
            "topic": None,
            "input_brief": None,
            "target_path": relative_to_workspace(workspace, bundle["draft"]),
            "root_dir": str(self.root),
            "output_file": str(output_file),
            "bundle": {
                "slug": "review-demo",
                "brief": str(bundle["brief"]),
                "evidence_pack": str(bundle["evidence_pack"]),
                "claim_map": str(bundle["claim_map"]),
                "draft": str(bundle["draft"]),
                "review": str(bundle["review"]),
                "final_markdown": str(bundle["final_markdown"]),
                "checklist": str(bundle["checklist"]),
                "docx": str(bundle["docx"]),
                "state_manifest": str(article_bundle_manifest_path(work, "review-demo")),
            },
            "related_context": [str(self.root / "AGENTS.md")],
            "execution_contract": contract.to_dict(),
        }
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        run_dir = self.orchestrator.store.runs_dir / "article-review-runtime"
        run_dir.mkdir(parents=True, exist_ok=True)
        request = {
            "run_id": "default:20260418-article-review",
            "lane": "article",
            "action": "review",
            "started_at": "2026-04-18T10:20:00+00:00",
            "project_id": "default",
            "project_title": self.root.name,
            "project_root": str(self.root),
            "work_id": work.slug,
            "work_title": work.title,
            "target": relative_to_workspace(workspace, bundle["draft"]),
        }
        result = {
            "status": "success",
            "returncode": 0,
            "started_at": request["started_at"],
            "finished_at": "2026-04-18T10:21:00+00:00",
            "log_path": str(run_dir / "launcher.log"),
        }

        self.orchestrator._finalize_runtime_run(run_dir, request, result)

        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["terminal_reason"], "ready-with-caveats")
        self.assertEqual(status["blockers"], [])
        self.assertEqual(status["repair_decision"]["action"], "stop")
        self.assertIn("contract_gates", status)
        self.assertTrue(any(item["gate_id"] == "standards-raw" for item in status["contract_gates"]))
        self.assertEqual(status["target_resolution"], None)

        bundle_state = json.loads(article_bundle_manifest_path(work, "review-demo").read_text(encoding="utf-8"))
        self.assertEqual(bundle_state["current_status"], "strong-draft")
        self.assertEqual(bundle_state["terminal_reason"], "ready-with-caveats")
        self.assertEqual(bundle_state["blockers"], [])

    def test_article_finalize_runtime_records_deterministic_finalization_check(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        write_raw_manifest(self.root, "ru-law-article-v1")
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        profile = resolve_standard_profile(self.root, workspace, work, lane="article", requested_profile_id=None)
        bundle = article_bundle_paths(work, "finalize-runtime-demo")
        write_file(bundle["brief"], "# Finalize brief\n")
        write_file(bundle["draft"], "# Finalize draft\n")
        write_file(bundle["review"], "# Review sheet\n")
        write_file(bundle["final_markdown"], "# Final markdown\n")
        write_file(
            bundle["checklist"],
            textwrap.dedent(
                """\
                # Submission Checklist

                - Status: `strong-draft`
                - What still blocks formal submission: none
                """
            ),
        )
        contract = build_article_execution_contract(
            work=work,
            profile=profile,
            action="finalize",
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path],
            bundle=bundle,
            topic=None,
            input_brief_path=None,
            target_path=bundle["final_markdown"],
            target_rel=relative_to_workspace(workspace, bundle["final_markdown"]),
        )
        timestamp = "20260418-102100"
        output_file = work.article.paths.output_runs_dir / f"{timestamp}-finalize-finalize-runtime-demo.md"
        manifest_file = work.article.paths.output_runs_dir / f"{timestamp}-finalize-finalize-runtime-demo.meta.json"
        write_file(output_file, "Finalizer completed. No submission-ready claim.\n")
        manifest_payload = {
            "timestamp": timestamp,
            "command": "finalize",
            "work_id": work.slug,
            "work_title": work.title,
            "profile_id": profile.resolved_profile_id,
            "requested_profile_id": profile.requested_profile_id,
            "resolved_profile_id": profile.resolved_profile_id,
            "fallback_profile_id": profile.fallback_profile_id,
            "profile_raw_dir": str(profile.raw_dir),
            "profile_conflict_flag": profile.conflict_flag,
            "profile_status": profile.profile_status,
            "search_enabled": True,
            "topic": None,
            "input_brief": None,
            "target_path": relative_to_workspace(workspace, bundle["final_markdown"]),
            "root_dir": str(self.root),
            "output_file": str(output_file),
            "bundle": {
                "slug": "finalize-runtime-demo",
                "brief": str(bundle["brief"]),
                "evidence_pack": str(bundle["evidence_pack"]),
                "claim_map": str(bundle["claim_map"]),
                "draft": str(bundle["draft"]),
                "review": str(bundle["review"]),
                "final_markdown": str(bundle["final_markdown"]),
                "checklist": str(bundle["checklist"]),
                "docx": str(bundle["docx"]),
                "state_manifest": str(article_bundle_manifest_path(work, "finalize-runtime-demo")),
            },
            "related_context": [str(self.root / "AGENTS.md")],
            "execution_contract": contract.to_dict(),
        }
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        run_dir = self.orchestrator.store.runs_dir / "article-finalize-runtime"
        run_dir.mkdir(parents=True, exist_ok=True)
        request = {
            "run_id": "default:20260418-article-finalize",
            "lane": "article",
            "action": "finalize",
            "started_at": "2026-04-18T10:21:00+00:00",
            "project_id": "default",
            "project_title": self.root.name,
            "project_root": str(self.root),
            "work_id": work.slug,
            "work_title": work.title,
            "target": relative_to_workspace(workspace, bundle["final_markdown"]),
        }
        result = {
            "status": "success",
            "returncode": 0,
            "started_at": request["started_at"],
            "finished_at": "2026-04-18T10:22:00+00:00",
            "log_path": str(run_dir / "launcher.log"),
        }

        self.orchestrator._finalize_runtime_run(run_dir, request, result)

        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["finalization_check"]["status"], "pass")
        self.assertEqual(status["finalization_check"]["finalization_status"], "export-ready")
        self.assertEqual(status["finalization_check"]["readiness_claim"], "none")
        self.assertTrue(any(item["name"] == "finalization-check-evaluated" for item in status["checkpoints"]))

    def test_thesis_verify_finalization_enriches_runtime_status_with_repair_metadata(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        profile = resolve_standard_profile(self.root, workspace, work, lane="thesis", requested_profile_id=None)
        target_path = self.root / TEST_THESIS_SECTION
        review_path = self.root / TEST_THESIS_REVIEW
        write_file(
            review_path,
            textwrap.dedent(
                """\
                # Лист проверки главы

                - Есть ли утверждения без опоры: да
                - Что нужно дополнить источниками: Добавить первичную опору к ключевому тезису.
                """
            ),
        )
        contract = build_thesis_execution_contract(
            work=work,
            profile=profile,
            action="verify",
            target_path=target_path,
            target_rel=TEST_THESIS_SECTION.as_posix(),
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path, target_path],
            review_path=review_path,
            sync_hint_path=work.thesis.sync_dir / "{date}-verify-01-introduction.md",
        )
        timestamp = "20260418-102500"
        output_file = work.thesis.paths.output_runs_dir / f"{timestamp}-verify.md"
        manifest_file = work.thesis.paths.output_runs_dir / f"{timestamp}-verify.meta.json"
        write_file(output_file, "Terminal status: blocked-primary-support\n")
        manifest_payload = {
            "timestamp": timestamp,
            "preset": "verify",
            "work_id": work.slug,
            "work_title": work.title,
            "target": {
                "absolute": str(target_path),
                "relative": TEST_THESIS_SECTION.as_posix(),
                "state": "existing",
            },
            "requested_profile_id": profile.requested_profile_id,
            "resolved_profile_id": profile.resolved_profile_id,
            "fallback_profile_id": profile.fallback_profile_id,
            "profile_raw_dir": str(profile.raw_dir),
            "profile_conflict_flag": profile.conflict_flag,
            "profile_status": profile.profile_status,
            "search_enabled": True,
            "root_dir": str(self.root),
            "output_file": str(output_file),
            "expected_review_file": str(review_path),
            "execution_contract": contract.to_dict(),
        }
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        run_dir = self.orchestrator.store.runs_dir / "thesis-verify-runtime"
        run_dir.mkdir(parents=True, exist_ok=True)
        request = {
            "run_id": "default:20260418-thesis-verify",
            "lane": "thesis",
            "action": "verify",
            "started_at": "2026-04-18T10:25:00+00:00",
            "project_id": "default",
            "project_title": self.root.name,
            "project_root": str(self.root),
            "work_id": work.slug,
            "work_title": work.title,
            "target": TEST_THESIS_SECTION.as_posix(),
        }
        result = {
            "status": "success",
            "returncode": 0,
            "started_at": request["started_at"],
            "finished_at": "2026-04-18T10:26:00+00:00",
            "log_path": str(run_dir / "launcher.log"),
        }

        self.orchestrator._finalize_runtime_run(run_dir, request, result)

        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["repair_decision"]["action"], "repair")
        self.assertEqual(status["terminal_reason"], "blocked-primary-support")
        self.assertTrue(any(item["category"] == "primary-support" for item in status["blockers"]))
        self.assertEqual(status["thesis_repair_plan"]["kind"], "thesis-repair-plan")
        self.assertTrue(status["thesis_repair_plan"]["eligible"])
        self.assertEqual(status["thesis_repair_plan"]["suggested_action"], "verify")
        self.assertIn("launch-thesis verify", status["thesis_repair_plan"]["suggested_command"])
        self.assertEqual(status["thesis_repair_plan"]["readiness_claim"], "none")

        resolution = json.loads((run_dir / "resolution.json").read_text(encoding="utf-8"))
        self.assertEqual(resolution["thesis_runtime"]["summary_block"]["kind"], "thesis-section-summary")
        self.assertEqual(resolution["thesis_runtime"]["summary_block"]["blocker_count"], 1)
        self.assertEqual(resolution["thesis_runtime"]["thesis_repair_plan"]["suggested_action"], "verify")

        section_status = self.orchestrator.get_artifact_status(f"thesis:{TEST_THESIS_SECTION.as_posix()}")
        self.assertEqual(section_status["summary"]["blocker_count"], 1)
        self.assertEqual(section_status["summary"]["terminal_reason"], "blocked-primary-support")

        work_state = self.orchestrator.get_artifact_status("work")
        self.assertEqual(work_state["runtime"]["recent"][0]["repair_decision"]["action"], "repair")
        self.assertEqual(work_state["runtime"]["recent"][0]["thesis_repair_plan"]["suggested_action"], "verify")

    def test_thesis_repair_iteration_derives_from_previous_runtime_record(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        profile = resolve_standard_profile(self.root, workspace, work, lane="thesis", requested_profile_id=None)
        target_path = self.root / TEST_THESIS_SECTION
        review_path = self.root / TEST_THESIS_REVIEW
        write_file(
            review_path,
            "- Есть ли утверждения без опоры: да\n",
        )
        contract = build_thesis_execution_contract(
            work=work,
            profile=profile,
            action="verify",
            target_path=target_path,
            target_rel=TEST_THESIS_SECTION.as_posix(),
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path, target_path],
            review_path=review_path,
            sync_hint_path=work.thesis.sync_dir / "{date}-verify-01-introduction.md",
        )

        def finalize_verify(timestamp: str, run_name: str) -> dict[str, object]:
            output_file = work.thesis.paths.output_runs_dir / f"{timestamp}-verify.md"
            manifest_file = work.thesis.paths.output_runs_dir / f"{timestamp}-verify.meta.json"
            write_file(output_file, "Terminal status: blocked-primary-support\n")
            manifest_payload = {
                "timestamp": timestamp,
                "preset": "verify",
                "work_id": work.slug,
                "work_title": work.title,
                "target": {
                    "absolute": str(target_path),
                    "relative": TEST_THESIS_SECTION.as_posix(),
                    "state": "existing",
                },
                "output_file": str(output_file),
                "expected_review_file": str(review_path),
                "execution_contract": contract.to_dict(),
            }
            manifest_file.parent.mkdir(parents=True, exist_ok=True)
            manifest_file.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            run_dir = self.orchestrator.store.runs_dir / run_name
            run_dir.mkdir(parents=True, exist_ok=True)
            request = {
                "run_id": f"default:{timestamp}-thesis-verify",
                "lane": "thesis",
                "action": "verify",
                "started_at": "2026-04-18T10:30:00+00:00",
                "project_id": "default",
                "project_title": self.root.name,
                "project_root": str(self.root),
                "work_id": work.slug,
                "work_title": work.title,
                "target": TEST_THESIS_SECTION.as_posix(),
            }
            result = {
                "status": "success",
                "returncode": 0,
                "started_at": request["started_at"],
                "finished_at": "2026-04-18T10:31:00+00:00",
                "log_path": str(run_dir / "launcher.log"),
            }
            self.orchestrator._finalize_runtime_run(run_dir, request, result)
            return json.loads((run_dir / "status.json").read_text(encoding="utf-8"))

        first_status = finalize_verify("20260418-103000", "thesis-verify-first-runtime")
        second_status = finalize_verify("20260418-103200", "thesis-verify-second-runtime")

        self.assertEqual(first_status["repair_iteration"], 0)
        self.assertEqual(first_status["repair_decision"]["action"], "repair")
        self.assertEqual(second_status["repair_iteration"], 1)
        self.assertEqual(second_status["repair_decision"]["action"], "stop")
        self.assertEqual(second_status["repair_decision"]["reason"], "repair-limit-reached")
        self.assertFalse(second_status["thesis_repair_plan"]["eligible"])
        self.assertEqual(second_status["thesis_repair_plan"]["terminal_reason"], "max-repair-iterations")

    def test_thesis_write_section_finalization_skips_repair_metadata_for_noneligible_action(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        profile = resolve_standard_profile(self.root, workspace, work, lane="thesis", requested_profile_id=None)
        target_path = self.root / TEST_THESIS_SECTION
        review_path = self.root / TEST_THESIS_REVIEW
        contract = build_thesis_execution_contract(
            work=work,
            profile=profile,
            action="write-section",
            target_path=target_path,
            target_rel=TEST_THESIS_SECTION.as_posix(),
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path, target_path],
            review_path=review_path,
            sync_hint_path=work.thesis.sync_dir / "{date}-write-section-01-introduction.md",
        )
        timestamp = "20260418-102700"
        output_file = work.thesis.paths.output_runs_dir / f"{timestamp}-write-section.md"
        manifest_file = work.thesis.paths.output_runs_dir / f"{timestamp}-write-section.meta.json"
        write_file(output_file, "Terminal status: blocked-primary-support\n")
        manifest_payload = {
            "timestamp": timestamp,
            "preset": "write-section",
            "work_id": work.slug,
            "work_title": work.title,
            "target": {
                "absolute": str(target_path),
                "relative": TEST_THESIS_SECTION.as_posix(),
                "state": "existing",
            },
            "output_file": str(output_file),
            "expected_review_file": str(review_path),
            "execution_contract": contract.to_dict(),
        }
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        run_dir = self.orchestrator.store.runs_dir / "thesis-write-runtime"
        run_dir.mkdir(parents=True, exist_ok=True)
        request = {
            "run_id": "default:20260418-thesis-write",
            "lane": "thesis",
            "action": "write-section",
            "started_at": "2026-04-18T10:27:00+00:00",
            "project_id": "default",
            "project_title": self.root.name,
            "project_root": str(self.root),
            "work_id": work.slug,
            "work_title": work.title,
            "target": TEST_THESIS_SECTION.as_posix(),
        }
        result = {
            "status": "success",
            "returncode": 0,
            "started_at": request["started_at"],
            "finished_at": "2026-04-18T10:28:00+00:00",
            "log_path": str(run_dir / "launcher.log"),
        }

        self.orchestrator._finalize_runtime_run(run_dir, request, result)

        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["blockers"], [])
        self.assertIsNone(status["repair_decision"])
        self.assertIsNone(status["terminal_reason"])

        resolution = json.loads((run_dir / "resolution.json").read_text(encoding="utf-8"))
        self.assertNotIn("thesis_runtime", resolution)


class ArticleRuntimeSignalsTests(unittest.TestCase):
    def test_extract_article_artifact_signals_prefers_most_conservative_status(self) -> None:
        signals = extract_article_artifact_signals(
            {
                "output": "Post-repair verdict: `submission-ready`\n",
                "review": "- Verdict: `strong-draft`\n- Remaining blockers: none\n",
                "checklist": (
                    "* Final status : `strong-draft-with-blockers`\n"
                    "* What still blocks formal submission: Need primary-source support for the lead claim.\n"
                ),
            }
        )

        self.assertEqual(signals.readiness_status, "strong-draft-with-blockers")
        self.assertEqual(len(signals.blockers), 1)
        self.assertEqual(signals.blockers[0].category, "primary-support")
        self.assertEqual(signals.blockers[0].details["source"], "checklist")
        self.assertEqual(signals.blockers[0].details["field"], "what still blocks formal submission")

    def test_extract_article_artifact_signals_handles_aliases_and_none_markers(self) -> None:
        signals = extract_article_artifact_signals(
            {
                "review": (
                    " - FINAL STATUS : `strong-draft`\n"
                    " - Remaining blockers : none identified\n"
                    " - Formatting issues : no blockers\n"
                ),
                "checklist": "- Submission blockers: none\n",
            }
        )

        self.assertEqual(signals.readiness_status, "strong-draft")
        self.assertEqual(signals.blockers, ())

    def test_extract_article_artifact_signals_ignores_freeform_prose(self) -> None:
        signals = extract_article_artifact_signals(
            {
                "output": "This draft may become submission-ready after another source pass.\n",
                "review": "Narrative note without explicit verdict fields.\n",
                "checklist": "- What still blocks formal submission: none\n",
            }
        )

        self.assertIsNone(signals.readiness_status)
        self.assertEqual(signals.blockers, ())

    def test_extract_article_artifact_signals_reads_guarded_blocker_prose(self) -> None:
        signals = extract_article_artifact_signals(
            {
                "review": (
                    "Formal submission is blocked by missing primary-source support for the lead claim.\n"
                    "Cannot claim submission-ready until the core citation is verified against the official text.\n"
                ),
            }
        )

        self.assertIsNone(signals.readiness_status)
        self.assertEqual(len(signals.blockers), 1)
        self.assertEqual(signals.blockers[0].category, "primary-support")
        self.assertEqual(signals.blockers[0].details["source"], "review")
        self.assertEqual(signals.blockers[0].details["field"], "guarded-prose")


class ThesisRuntimeSignalsTests(unittest.TestCase):
    def test_extract_thesis_runtime_signals_parses_review_findings(self) -> None:
        signals = extract_thesis_runtime_signals(
            {
                "output": "Terminal status: ready-with-caveats\n",
                "review": (
                    "- Есть ли утверждения без опоры: да\n"
                    "- Есть ли спорные выводы: да\n"
                    "- Все ли динамичные нормы и решения перепроверены на дату написания: нет\n"
                    "- Что нужно дополнить источниками: Добавить первичную опору к ключевому тезису.\n"
                ),
            }
        )

        self.assertEqual(signals.status_hint, "ready-with-caveats")
        self.assertEqual({item.category for item in signals.blockers}, {"primary-support", "review", "dynamic-material"})
        self.assertTrue(any(item.details["source"] == "review" for item in signals.blockers))

    def test_extract_thesis_runtime_signals_ignores_neutral_review_answers(self) -> None:
        signals = extract_thesis_runtime_signals(
            {
                "output": "Result: updated\n",
                "review": (
                    "- Есть ли утверждения без опоры: нет\n"
                    "- Есть ли спорные выводы: нет\n"
                    "- Все ли динамичные нормы и решения перепроверены на дату написания: да\n"
                    "- Что нужно дополнить источниками: нет\n"
                ),
            }
        )

        self.assertEqual(signals.status_hint, "updated")
        self.assertEqual(signals.blockers, ())

    def test_extract_thesis_runtime_signals_reads_guarded_review_prose(self) -> None:
        signals = extract_thesis_runtime_signals(
            {
                "review": (
                    "Нужна первичная опора для ключевого тезиса.\n"
                    "Нужно перепроверить динамичные нормы на дату написания.\n"
                ),
            }
        )

        self.assertIsNone(signals.status_hint)
        self.assertEqual({item.category for item in signals.blockers}, {"primary-support", "dynamic-material"})
        self.assertTrue(all(item.details["field"] == "guarded-prose" for item in signals.blockers))


class GuardedProseRegistryTests(unittest.TestCase):
    def test_machine_readable_registry_loads_article_and_thesis_rules(self) -> None:
        article_rules = load_guarded_prose_rules("article")
        thesis_rules = load_guarded_prose_rules("thesis")

        self.assertTrue(any(rule.code == "guarded-prose-primary-support" for rule in article_rules))
        self.assertTrue(any(rule.code == "guarded-prose-dynamic-material" for rule in thesis_rules))
        self.assertTrue(any(rule.forbidden_markers for rule in article_rules))
        self.assertTrue(any(rule.regex_patterns for rule in thesis_rules))


class WorkspaceTargetResolutionTests(unittest.TestCase):
    def test_resolve_target_for_action_marks_thesis_legacy_root_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            workspace = load_workspace_config(root)
            selection = resolve_work_selection(workspace, target="manuscript/sections/01-introduction.md")

            resolution = resolve_target_for_action(
                workspace,
                selection.work,
                "thesis",
                "write-section",
                "manuscript/sections/01-introduction.md",
                work_source=selection.source,
            )

            self.assertEqual(selection.source, "default")
            self.assertEqual(resolution.normalized_path, TEST_THESIS_SECTION.as_posix())
            self.assertEqual(resolution.resolution_mode, "legacy-root")
            self.assertTrue(resolution.used_legacy_root_mapping)
            self.assertEqual(resolution.warning_code, "legacy-root-target")
            self.assertIn(TEST_THESIS_SECTION.as_posix(), resolution.warning_message or "")

    def test_resolve_target_for_action_marks_article_legacy_root_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            workspace = load_workspace_config(root)
            selection = resolve_work_selection(workspace, target="articles/drafts/demo.md")

            resolution = resolve_target_for_action(
                workspace,
                selection.work,
                "article",
                "review",
                "articles/drafts/demo.md",
                work_source=selection.source,
            )

            self.assertEqual(selection.source, "default")
            self.assertEqual(resolution.normalized_path, TEST_ARTICLE_DRAFT.as_posix())
            self.assertEqual(resolution.resolution_mode, "legacy-root")
            self.assertTrue(resolution.used_legacy_root_mapping)
            self.assertEqual(resolution.warning_code, "legacy-root-target")
            self.assertIn(TEST_ARTICLE_DRAFT.as_posix(), resolution.warning_message or "")

    def test_resolve_target_for_action_marks_other_legacy_aliases_without_duplicate_prefix_list(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            workspace = load_workspace_config(root)
            selection = resolve_work_selection(workspace, target="reviews/01-introduction-review.md")

            thesis_resolution = resolve_target_for_action(
                workspace,
                selection.work,
                "thesis",
                "verify",
                "reviews/01-introduction-review.md",
                work_source=selection.source,
            )
            article_resolution = resolve_target_for_action(
                workspace,
                selection.work,
                "article",
                "review",
                "articles/final/demo.md",
                work_source=selection.source,
            )

            self.assertEqual(thesis_resolution.resolution_mode, "legacy-root")
            self.assertTrue(thesis_resolution.used_legacy_root_mapping)
            self.assertEqual(thesis_resolution.normalized_path, TEST_THESIS_REVIEW.as_posix())
            self.assertEqual(article_resolution.resolution_mode, "legacy-root")
            self.assertTrue(article_resolution.used_legacy_root_mapping)
            self.assertEqual(article_resolution.normalized_path, TEST_ARTICLE_FINAL.as_posix())

    def test_legacy_target_prefixes_cover_current_legacy_world(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            workspace = load_workspace_config(root)
            work = resolve_work_selection(workspace).work

            prefixes = set(legacy_target_prefixes(work))

            self.assertTrue({"chapters/", "sources/", "manuscript/sections/", "reviews/"}.issubset(prefixes))
            self.assertTrue({"articles/briefs/", "articles/drafts/", "articles/reviews/", "articles/final/"}.issubset(prefixes))

    def test_legacy_target_entries_are_derived_from_work_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            workspace = load_workspace_config(root)
            work = resolve_work_selection(workspace).work

            entries = {entry.prefix: entry for entry in legacy_target_entries(work)}

            self.assertIn("manuscript/sections/", entries)
            self.assertIn("articles/final/", entries)
            self.assertEqual(
                entries["manuscript/sections/"].resolved_path.resolve(),
                (root / TEST_WORK_ROOT / "thesis" / "manuscript" / "sections").resolve(),
            )
            self.assertEqual(
                entries["articles/final/"].resolved_path.resolve(),
                (root / TEST_WORK_ROOT / "articles" / "final").resolve(),
            )


class RuntimeObservabilityWrapperTests(unittest.TestCase):
    def test_run_wrapper_writes_status_for_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            success_dir = root / "run-success"
            success_dir.mkdir(parents=True, exist_ok=True)
            (success_dir / "request.json").write_text(
                json.dumps(
                    {
                        "run_id": "alpha:20260418-thesis-write-section",
                        "project_id": "alpha",
                        "project_title": "Alpha",
                        "project_root": str(root),
                        "work_id": "demo-work",
                        "work_title": "Demo work",
                        "lane": "thesis",
                        "action": "write-section",
                        "started_at": "2026-04-18T10:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            code = run_wrapper_module.main(
                [
                    "--run-dir",
                    str(success_dir),
                    "--cwd",
                    str(root),
                    "--",
                    "python3",
                    "-c",
                    "print('ok')",
                ]
            )
            self.assertEqual(code, 0)
            success_status = json.loads((success_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(success_status["version"], "v2")
            self.assertEqual(success_status["entity_kind"], "workflow-run")
            self.assertEqual(success_status["status"], "succeeded")
            self.assertEqual(success_status["lane"], "thesis")
            self.assertEqual(success_status["action"], "write-section")
            self.assertEqual(success_status["project_id"], "alpha")
            self.assertIn("queued", [item["name"] for item in success_status["checkpoints"]])
            self.assertIn("command-started", [item["name"] for item in success_status["checkpoints"]])
            self.assertIn("command-finished", [item["name"] for item in success_status["checkpoints"]])
            self.assertIn("status", success_status["attachments"])
            self.assertIn("request", success_status["attachments"])
            self.assertIn("result", success_status["attachments"])
            self.assertIn("log", success_status["attachments"])

            failure_dir = root / "run-failure"
            failure_dir.mkdir(parents=True, exist_ok=True)
            (failure_dir / "request.json").write_text(
                json.dumps(
                    {
                        "run_id": "alpha:20260418-thesis-verify",
                        "project_id": "alpha",
                        "project_title": "Alpha",
                        "project_root": str(root),
                        "work_id": "demo-work",
                        "work_title": "Demo work",
                        "lane": "thesis",
                        "action": "verify",
                        "started_at": "2026-04-18T10:10:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            code = run_wrapper_module.main(
                [
                    "--run-dir",
                    str(failure_dir),
                    "--cwd",
                    str(root),
                    "--",
                    "python3",
                    "-c",
                    "import sys; sys.exit(3)",
                ]
            )
            self.assertEqual(code, 3)
            failure_status = json.loads((failure_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(failure_status["status"], "failed")
            self.assertEqual(failure_status["failure"]["category"], "process")
            self.assertEqual(failure_status["failure"]["code"], "command-exited-nonzero")

    def test_chat_wrapper_records_resume_recovery_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            fake_codex = root / "fake-codex"
            build_fake_codex(fake_codex)
            task_dir = root / "task"
            task_dir.mkdir(parents=True, exist_ok=True)
            request = {
                "task_id": "alpha:20260418-chat",
                "project_id": "alpha",
                "project_title": "Alpha",
                "project_root": str(root),
                "work_id": "demo-work",
                "work_title": "Demo work",
                "prompt": "Продолжай работу",
                "user_text": "Продолжай работу",
                "session_id": "broken-session",
                "started_at": "2026-04-18T10:20:00+00:00",
                "codex_bin": str(fake_codex),
                "codex_model": "gpt-test",
                "profile": "execute",
            }
            (task_dir / "request.json").write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            code = chat_wrapper_module.main(["--task-dir", str(task_dir)])
            self.assertEqual(code, 0)
            status = json.loads((task_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["version"], "v2")
            self.assertEqual(status["entity_kind"], "chat-turn")
            self.assertEqual(status["status"], "succeeded")
            self.assertEqual(status["profile"], "execute")
            self.assertIn("response", status["attachments"])
            self.assertIn("stdout", status["attachments"])
            self.assertIn("stderr", status["attachments"])
            checkpoint_names = [item["name"] for item in status["checkpoints"]]
            self.assertIn("resume-attempted", checkpoint_names)
            self.assertIn("resume-failed", checkpoint_names)
            self.assertIn("restart-after-resume-failure", checkpoint_names)
            resume_failed = next(item for item in status["checkpoints"] if item["name"] == "resume-failed")
            self.assertEqual(resume_failed["failure"]["category"], "codex")
            self.assertEqual(resume_failed["failure"]["code"], "resume-session-failed")


class StandardsResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def load_active_work(self):
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        return workspace, work

    def test_registry_missing_uses_legacy_profiles(self) -> None:
        workspace, work = self.load_active_work()

        registry = load_standards_registry(self.root)
        resolution = resolve_standard_profile(
            self.root,
            workspace,
            work,
            lane="article",
            requested_profile_id=None,
        )

        self.assertTrue(registry.synthetic)
        self.assertEqual(resolution.resolved_profile_id, "ru-law-article-v1")
        self.assertIsNone(resolution.fallback_profile_id)
        self.assertTrue(resolution.normalized_path.exists())

    def test_missing_requested_profile_falls_back_to_generic(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        workspace, work = self.load_active_work()

        resolution = resolve_standard_profile(
            self.root,
            workspace,
            work,
            lane="article",
            requested_profile_id="missing-profile",
        )

        self.assertEqual(resolution.requested_profile_id, "missing-profile")
        self.assertEqual(resolution.resolved_profile_id, "ru-law-article-v1")
        self.assertEqual(resolution.fallback_profile_id, "ru-law-article-v1")

    def test_missing_normalized_profile_falls_back_to_generic(self) -> None:
        write_sample_standards_registry(self.root)
        rewrite_work_profiles(self.root, thesis_profile="sogu-vkr-2025")
        workspace, work = self.load_active_work()

        resolution = resolve_standard_profile(
            self.root,
            workspace,
            work,
            lane="thesis",
            requested_profile_id=None,
        )

        self.assertEqual(resolution.requested_profile_id, "sogu-vkr-2025")
        self.assertEqual(resolution.resolved_profile_id, "thesis-v1")
        self.assertEqual(resolution.fallback_profile_id, "thesis-v1")

    def test_missing_raw_does_not_force_fallback(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        workspace, work = self.load_active_work()

        resolution = resolve_standard_profile(
            self.root,
            workspace,
            work,
            lane="article",
            requested_profile_id="journal-jrp",
        )

        self.assertEqual(resolution.resolved_profile_id, "journal-jrp")
        self.assertIsNone(resolution.fallback_profile_id)
        self.assertEqual(resolution.raw_status, "missing")


class ProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        self.bot_home = self.workspace / "bot-home"
        self.bot_home.mkdir(parents=True, exist_ok=True)
        self.repo_a = self.workspace / "alpha"
        self.repo_b = self.workspace / "beta"
        build_fake_repo(self.repo_a)
        build_fake_repo(self.repo_b)
        write_file(self.repo_b / "manuscript/sections/02-custom.md", "# Another section\n")
        write_projects_registry(
            self.bot_home,
            [
                {
                    "id": "alpha",
                    "title": "Диплом А",
                    "root_dir": str(self.repo_a),
                    "capabilities": ["thesis", "article"],
                },
                {
                    "id": "beta",
                    "title": "Диплом Б",
                    "root_dir": str(self.repo_b),
                    "capabilities": ["thesis"],
                },
                {
                    "id": "broken",
                    "title": "Сломанный проект",
                    "root_dir": str(self.workspace / "missing"),
                    "capabilities": ["thesis"],
                },
            ],
        )
        self.service = ProjectService(self.bot_home)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def wait_for_no_active_run(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.service.sync_active_run()
            if not self.service.store.get_active_run():
                return
            time.sleep(0.1)
        self.fail("active run did not complete in time")

    def test_registry_marks_unavailable_project(self) -> None:
        projects = {project.id: project for project in self.service.list_projects()}
        self.assertTrue(projects["alpha"].available)
        self.assertTrue(projects["beta"].available)
        self.assertFalse(projects["broken"].available)
        self.assertIn("Папка проекта не найдена", projects["broken"].problems[0])

    def test_multiple_projects_require_explicit_selection(self) -> None:
        self.assertIsNone(self.service.get_active_project())

    def test_register_project_generates_slug_from_russian_title(self) -> None:
        repo_c = self.workspace / "gamma"
        build_fake_repo(repo_c)
        result = self.service.register_project("Диплом по биометрии", repo_c)

        self.assertTrue(result.created)
        self.assertEqual(result.project.id, "diplom-po-biometrii")
        payload = json.loads((self.bot_home / "output" / "telegram" / "projects.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["projects"][-1]["id"], "diplom-po-biometrii")

    def test_register_project_adds_numeric_suffix_for_conflict(self) -> None:
        repo_c = self.workspace / "gamma"
        repo_d = self.workspace / "delta"
        build_fake_repo(repo_c)
        build_fake_repo(repo_d)

        first = self.service.register_project("Диплом по биометрии", repo_c)
        second = self.service.register_project("Диплом по биометрии", repo_d)

        self.assertEqual(first.project.id, "diplom-po-biometrii")
        self.assertEqual(second.project.id, "diplom-po-biometrii-2")

    def test_register_project_returns_existing_entry_for_duplicate_path(self) -> None:
        result = self.service.register_project("Любое новое имя", self.repo_a)

        self.assertFalse(result.created)
        self.assertEqual(result.project.id, "alpha")
        payload = json.loads((self.bot_home / "output" / "telegram" / "projects.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload["projects"]), 3)

    def test_register_project_rejects_invalid_root(self) -> None:
        broken_root = self.workspace / "broken-local"
        broken_root.mkdir(parents=True, exist_ok=True)

        with self.assertRaisesRegex(Exception, "Этот проект пока нельзя добавить"):
            self.service.register_project("Сломанный локальный", broken_root)

        payload = json.loads((self.bot_home / "output" / "telegram" / "projects.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload["projects"]), 3)

    def test_targets_and_recent_runs_are_project_scoped(self) -> None:
        alpha_targets = self.service.list_targets("alpha", "thesis", "write-section")
        beta_targets = self.service.list_targets("beta", "thesis", "write-section")
        self.assertEqual(alpha_targets, ["manuscript/sections/01-introduction.md"])
        self.assertIn("manuscript/sections/02-custom.md", beta_targets)

        write_thesis_manifest(self.repo_a, "20260417-101010")
        write_thesis_manifest(self.repo_b, "20260417-101010")
        alpha_records = self.service.list_recent_runs("alpha", "thesis", limit=5)
        beta_records = self.service.list_recent_runs("beta", "thesis", limit=5)
        self.assertTrue(alpha_records[0].record_id.startswith("alpha:"))
        self.assertTrue(beta_records[0].record_id.startswith("beta:"))

    def test_empty_work_scaffold_can_be_selected_and_reports_empty_runtime_views(self) -> None:
        add_empty_work_scaffold(self.repo_a)
        self.service = ProjectService(self.bot_home)

        works = {work.slug for work in self.service.list_works("alpha")}
        self.assertIn(TEST_WORK_ID, works)
        self.assertIn("empty-work", works)

        active_work = self.service.set_active_work("alpha", "empty-work")
        self.assertEqual(active_work.slug, "empty-work")
        self.assertEqual(self.service.list_targets("alpha", "thesis", "write-section"), [])
        self.assertEqual(self.service.list_thesis_sections("alpha"), [])
        self.assertEqual(self.service.list_article_slugs("alpha"), [])

        thesis_status = self.service.get_artifact_status("alpha", "thesis")
        article_status = self.service.get_artifact_status("alpha", "article")
        self.assertEqual(thesis_status["kind"], "thesis-overview")
        self.assertEqual(thesis_status["sections"], [])
        self.assertEqual(thesis_status["summary"]["kind"], "thesis-overview-summary")
        self.assertEqual(thesis_status["summary"]["section_count"], 0)
        self.assertEqual(article_status["kind"], "article-overview")
        self.assertEqual(article_status["bundles"], [])
        self.assertEqual(article_status["summary"]["kind"], "article-overview-summary")
        self.assertEqual(article_status["summary"]["bundle_count"], 0)

    def test_global_lock_mentions_busy_project(self) -> None:
        previous_sleep = os.environ.get("TEST_SLEEP_SECONDS")
        os.environ["TEST_SLEEP_SECONDS"] = "1"
        try:
            self.service.start_run("alpha", "thesis", "verify", "manuscript/sections/01-introduction.md")
            with self.assertRaises(RunBusyError) as ctx:
                self.service.start_run("beta", "thesis", "verify", "manuscript/sections/01-introduction.md")
        finally:
            if previous_sleep is None:
                os.environ.pop("TEST_SLEEP_SECONDS", None)
            else:
                os.environ["TEST_SLEEP_SECONDS"] = previous_sleep

        self.assertIn("Диплом А", str(ctx.exception))
        self.wait_for_no_active_run()

    def test_attachment_lookup_uses_project_prefixed_record_id(self) -> None:
        write_thesis_manifest(self.repo_a, "20260417-111111")
        write_thesis_manifest(self.repo_b, "20260417-111111")
        record_a = self.service.list_recent_runs("alpha", "thesis", limit=5)[0]
        record_b = self.service.list_recent_runs("beta", "thesis", limit=5)[0]

        manifest_a = self.service.get_run_attachment(record_a.record_id, "manifest")
        manifest_b = self.service.get_run_attachment(record_b.record_id, "manifest")

        self.assertIsNotNone(manifest_a)
        self.assertIsNotNone(manifest_b)
        assert manifest_a is not None
        assert manifest_b is not None
        self.assertIn(str(self.repo_a), str(manifest_a))
        self.assertIn(str(self.repo_b), str(manifest_b))

    def test_single_project_bootstrap_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            service = ProjectService(root)
            project = service.get_active_project()
            self.assertIsNotNone(project)
            assert project is not None
            self.assertEqual(project.id, "default")
            self.assertTrue((root / "output" / "telegram" / "projects.json").exists())

    def test_register_project_creates_registry_without_bootstrap_default(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            bot_home = Path(tempdir) / "bot-home"
            bot_home.mkdir(parents=True, exist_ok=True)
            repo = Path(tempdir) / "repo"
            build_fake_repo(repo)

            service = ProjectService(bot_home)
            result = service.register_project("Диплом по биометрии", repo)

            self.assertTrue(result.created)
            self.assertEqual(result.project.id, "diplom-po-biometrii")
            payload = json.loads((bot_home / "output" / "telegram" / "projects.json").read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in payload["projects"]], ["diplom-po-biometrii"])


class AgentChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)
        self.fake_codex = self.root / "bin" / "fake-codex"
        build_fake_codex(self.fake_codex)
        self.projects = ProjectService(self.root, codex_bin=str(self.fake_codex), codex_model="gpt-test")
        self.chat = AgentChatService(self.projects, codex_bin=str(self.fake_codex), codex_model="gpt-test")
        self.session_id = f"session-{self.root.name}"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def wait_for_task_completion(self, timeout: float = 5.0) -> AgentTurnNotification:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.chat.sync_active_task()
            if not self.chat.store.get_active_agent_task():
                items = self.chat.drain_notifications()
                if items:
                    return items[-1]
            time.sleep(0.1)
        self.fail("agent task did not complete in time")

    def read_request_payload(self, active: dict[str, object]) -> dict[str, object]:
        task_dir = Path(str(active["task_dir"]))
        return json.loads((task_dir / "request.json").read_text(encoding="utf-8"))

    def test_new_turn_persists_session_and_summary(self) -> None:
        self.chat.start_turn("default", "Допиши введение")
        notification = self.wait_for_task_completion()

        self.assertEqual(notification.status, "success")
        self.assertIn("Допиши введение", notification.response_text or "")
        state = self.chat.get_project_state("default")
        self.assertEqual(state.session_id, self.session_id)
        self.assertEqual(state.last_user_message, "Допиши введение")
        self.assertIn("Допиши введение", state.last_assistant_summary or "")

    def test_first_turn_uses_full_context_and_execute_profile(self) -> None:
        active = self.chat.start_turn("default", "Допиши введение")
        request = self.read_request_payload(active)

        self.assertEqual(request["context_mode"], "full")
        self.assertEqual(request["profile"], "execute")
        self.assertEqual(request["detected_intent"], "выполнение")
        self.assertIn(str(self.root / "AGENTS.md"), str(request["prompt"]))
        self.assertIn(str(self.root / "meta" / "project-canon.md"), str(request["prompt"]))
        self.assertIn(str(self.root / "meta" / "master-protocol.md"), str(request["prompt"]))
        self.wait_for_task_completion()

    def test_follow_up_uses_resume_session(self) -> None:
        self.chat.store.set_project_chat(
            "default",
            {
                "session_id": self.session_id,
                "last_activity_at": "2026-04-17T10:00:00+00:00",
                "last_user_message": "Первый запрос",
                "last_assistant_summary": "Первый ответ",
                "busy": False,
                "last_export_path": None,
                "needs_full_context": False,
            },
        )
        self.chat.store.set_last_chat_project_id("default")

        self.chat.start_turn("default", "Продолжай")
        notification = self.wait_for_task_completion()

        self.assertEqual(notification.session_id, self.session_id)
        self.assertIn(f"resume({self.session_id})", notification.response_text or "")

    def test_follow_up_uses_compact_context(self) -> None:
        self.chat.store.set_project_chat(
            "default",
            {
                "session_id": self.session_id,
                "last_activity_at": "2026-04-17T10:00:00+00:00",
                "last_user_message": "Первый запрос",
                "last_assistant_summary": "Первый ответ",
                "busy": False,
                "last_export_path": None,
                "needs_full_context": False,
            },
        )
        self.chat.store.set_last_chat_project_id("default")

        active = self.chat.start_turn("default", "Как усилить аргументацию?")
        request = self.read_request_payload(active)

        self.assertEqual(request["context_mode"], "compact")
        self.assertEqual(request["profile"], "answer")
        self.assertIn("Краткий recap проекта", str(request["prompt"]))
        self.wait_for_task_completion()

    def test_broken_session_falls_back_to_new_session(self) -> None:
        self.chat.store.set_project_chat(
            "default",
            {
                "session_id": "broken-session",
                "last_activity_at": "2026-04-17T10:00:00+00:00",
                "last_user_message": "Старый запрос",
                "last_assistant_summary": "Старый ответ",
                "busy": False,
                "last_export_path": None,
                "needs_full_context": False,
            },
        )
        self.chat.store.set_last_chat_project_id("default")

        self.chat.start_turn("default", "Продолжай после сбоя")
        notification = self.wait_for_task_completion()

        self.assertTrue(notification.reset_session)
        self.assertEqual(notification.status, "success")
        self.assertEqual(notification.session_id, self.session_id)
        self.assertIn(f"new({self.session_id})", notification.response_text or "")
        self.assertTrue(self.chat.get_project_state("default").needs_full_context)

    def test_next_turn_after_broken_session_uses_full_context(self) -> None:
        self.chat.store.set_project_chat(
            "default",
            {
                "session_id": "broken-session",
                "last_activity_at": "2026-04-17T10:00:00+00:00",
                "last_user_message": "Старый запрос",
                "last_assistant_summary": "Старый ответ",
                "busy": False,
                "last_export_path": None,
                "needs_full_context": False,
            },
        )
        self.chat.store.set_last_chat_project_id("default")

        self.chat.start_turn("default", "Продолжай после сбоя")
        self.wait_for_task_completion()

        active = self.chat.start_turn("default", "Продолжай уже в новой сессии")
        request = self.read_request_payload(active)

        self.assertEqual(request["context_mode"], "full")
        self.assertEqual(request["session_id"], self.session_id)
        self.wait_for_task_completion()

    def test_busy_lock_blocks_second_project(self) -> None:
        previous_sleep = os.environ.get("FAKE_CODEX_SLEEP_SECONDS")
        os.environ["FAKE_CODEX_SLEEP_SECONDS"] = "1"
        try:
            self.chat.start_turn("default", "Первая задача")
            with self.assertRaises(AgentBusyError):
                self.chat.start_turn("default", "Вторая задача")
        finally:
            if previous_sleep is None:
                os.environ.pop("FAKE_CODEX_SLEEP_SECONDS", None)
            else:
                os.environ["FAKE_CODEX_SLEEP_SECONDS"] = previous_sleep
        self.wait_for_task_completion()

    def test_record_export_updates_project_state(self) -> None:
        export_path = self.root / "output" / "docx" / "thesis-draft.docx"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text("docx", encoding="utf-8")

        self.chat.record_export("default", export_path)

        state = self.chat.get_project_state("default")
        self.assertEqual(state.last_export_path, str(export_path.resolve()))


class PromptBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)
        self.projects = ProjectService(self.root)
        self.builder = PromptBuilder()
        self.project = self.projects.require_project("default")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_question_is_classified_as_answer(self) -> None:
        built = self.builder.build_turn_prompt(
            self.project,
            ProjectChatState(project_id="default"),
            "Как усилить введение?",
            context_mode="full",
            current_focus="Пока без истории.",
        )

        self.assertEqual(built.profile, "answer")
        self.assertEqual(built.detected_intent, "ответ")
        self.assertFalse(any("изменены" in item for item in built.done_contract))

    def test_review_request_is_classified_as_review(self) -> None:
        built = self.builder.build_turn_prompt(
            self.project,
            ProjectChatState(project_id="default"),
            "Проверь, готово ли введение и есть ли ошибки",
            context_mode="compact",
            current_focus="Проверка готовности.",
        )

        self.assertEqual(built.profile, "review")
        self.assertEqual(built.detected_intent, "проверка")
        self.assertTrue(any("findings first" in item for item in built.done_contract))

    def test_built_prompt_contains_project_context(self) -> None:
        built = self.builder.build_turn_prompt(
            self.project,
            ProjectChatState(
                project_id="default",
                last_user_message="Предыдущий запрос",
                last_assistant_summary="Предыдущий summary",
            ),
            "Допиши выводы",
            context_mode="full",
            current_focus="Дописать выводы и выровнять структуру.",
        )

        self.assertIn(str(self.root), built.prompt_text)
        self.assertIn(str(self.root / "AGENTS.md"), built.prompt_text)
        self.assertIn("Предыдущий summary", built.prompt_text)
        self.assertIn("Дописать выводы и выровнять структуру.", built.prompt_text)
        self.assertIn("Thesis sections", built.prompt_text)


class TelegramConsoleBotUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)
        self.projects = ProjectService(self.root)
        self.api = FakeApi()
        self.chat = FakeChatService()
        self.config = TelegramConsoleConfig(
            root_dir=self.root,
            token="test-token",
            allowed_chat_id=1,
            poll_timeout=1,
        )
        self.bot = TelegramConsoleBot(self.config, self.api, self.projects, self.chat)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_dashboard_shows_minimal_menu(self) -> None:
        self.bot._show_dashboard(1)
        last_message = self.api.messages[-1]
        self.assertIn("Удаленный Codex", str(last_message["text"]))
        self.assertIn("Пиши мне обычным сообщением", str(last_message["text"]))
        self.assertIn("Действующие проекты", str(last_message["text"]))
        self.assertIn("Что дальше:", str(last_message["text"]))
        self.assertEqual(
            last_message["reply_markup"]["keyboard"],
            [[{"text": label} for label in row] for row in MAIN_MENU],
        )

    def test_free_text_is_sent_to_project_chat(self) -> None:
        self.bot._handle_message({"chat": {"id": 1}, "text": "Продолжай писать диплом"})

        self.assertEqual(len(self.chat.started), 1)
        self.assertEqual(self.chat.started[0]["project_id"], "default")
        self.assertEqual(self.chat.started[0]["prompt"], "Продолжай писать диплом")
        self.assertIn("Беру это в работу", str(self.api.messages[-1]["text"]))
        self.assertIn("Режим: выполнение", str(self.api.messages[-1]["text"]))
        self.assertIn("Ожидаю:", str(self.api.messages[-1]["text"]))

    def test_plain_greeting_is_sent_to_chat(self) -> None:
        self.bot._handle_message({"chat": {"id": 1}, "text": "привет"})

        self.assertEqual(len(self.chat.started), 1)
        self.assertEqual(self.chat.started[0]["prompt"], "привет")

    def test_slash_text_is_sent_to_chat(self) -> None:
        self.bot._handle_message({"chat": {"id": 1}, "text": "/run диплом проверить manuscript/sections/01-introduction.md"})

        self.assertEqual(len(self.chat.started), 1)
        self.assertEqual(
            self.chat.started[0]["prompt"],
            "/run диплом проверить manuscript/sections/01-introduction.md",
        )

    def test_review_request_acknowledgement_shows_detected_mode(self) -> None:
        self.bot._handle_message({"chat": {"id": 1}, "text": "Проверь диплом и найди риски"})

        self.assertEqual(len(self.chat.started), 1)
        self.assertIn("Режим: проверка", str(self.api.messages[-1]["text"]))

    def test_success_notification_sends_answer_text(self) -> None:
        self.chat.notifications.append(
            AgentTurnNotification(
                task_id="default:chat",
                project_id="default",
                project_title="Тестовый диплом",
                status="success",
                started_at="2026-04-17T10:00:00+00:00",
                finished_at="2026-04-17T10:01:00+00:00",
                response_text="Готово. Я дописал введение.",
                summary="Готово. Я дописал введение.",
                session_id="session-demo",
            )
        )

        self.bot.tick()

        self.assertEqual(len(self.api.messages), 2)
        self.assertIn("Ответ готов", str(self.api.messages[0]["text"]))
        self.assertIn("Готово. Я дописал введение.", str(self.api.messages[1]["text"]))

    def test_failed_notification_is_human_readable(self) -> None:
        self.chat.notifications.append(
            AgentTurnNotification(
                task_id="default:chat",
                project_id="default",
                project_title="Тестовый диплом",
                status="failed",
                started_at="2026-04-17T10:00:00+00:00",
                finished_at="2026-04-17T10:01:00+00:00",
                error="Codex CLI завершился с ошибкой.",
                reset_session=True,
            )
        )

        self.bot.tick()

        self.assertIn("Не получилось получить ответ Codex", str(self.api.messages[-1]["text"]))
        self.assertIn("сессия", str(self.api.messages[-1]["text"]).lower())

    def test_workflow_notification_uses_lane_summary(self) -> None:
        run_dir = self.root / "output" / "telegram" / "runtime" / "runs" / "20260418-100000-default-thesis-verify"
        resolution_path = run_dir / "resolution.json"
        write_runtime_status_fixture(
            run_dir,
            record_id="default:20260418-thesis-verify",
            entity_kind="workflow-run",
            project_id="default",
            project_title=self.root.name,
            project_root=self.root,
            work_id=TEST_WORK_ID,
            work_title="Demo work",
            lane="thesis",
            action="verify",
            attachments={"resolution": str(resolution_path)},
            summary="Workflow verification completed.",
        )
        resolution_path.write_text(
            json.dumps(
                {
                    "target_resolution": {
                        "normalized_path": TEST_THESIS_SECTION.as_posix(),
                        "resolution_mode": "legacy-root",
                        "work_source": "default",
                        "used_legacy_root_mapping": True,
                        "warning_code": "legacy-root-target",
                        "warning_message": (
                            "Legacy target path `manuscript/sections/01-introduction.md` "
                            f"resolved to `{TEST_THESIS_SECTION.as_posix()}`."
                        ),
                    },
                    "thesis_runtime": {
                        "summary_block": {
                            "kind": "thesis-section-summary",
                            "target": TEST_THESIS_SECTION.as_posix(),
                            "review_present": True,
                            "last_run_action": "verify",
                            "last_run_status": "success",
                            "blocker_count": 0,
                            "terminal_reason": None,
                            "suggested_next_action": "review-section",
                        }
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.projects.store.append_notification(
            RunRecord(
                record_id="default:20260418-thesis-verify",
                lane="thesis",
                action="verify",
                status="success",
                started_at="2026-04-18T10:00:00+00:00",
                project_id="default",
                project_title=self.root.name,
                project_root=str(self.root),
                work_id=TEST_WORK_ID,
                work_title="Demo work",
                finished_at="2026-04-18T10:01:00+00:00",
                target=TEST_THESIS_SECTION.as_posix(),
                runtime_run_dir=str(run_dir),
                summary="Workflow verification completed.",
            ).to_dict()
        )

        self.bot.tick()

        self.assertTrue(any("Workflow завершен" in str(item["text"]) for item in self.api.messages))
        self.assertTrue(any("Lane summary:" in str(item["text"]) for item in self.api.messages))
        self.assertTrue(any("next=review-section" in str(item["text"]) for item in self.api.messages))
        self.assertTrue(any("Legacy target path" in str(item["text"]) for item in self.api.messages))

    def test_run_export_without_mailer_keeps_telegram_delivery(self) -> None:
        self.bot._run_export(1, "default", "thesis")

        self.assertEqual(len(self.api.documents), 1)
        self.assertIn("thesis-draft.docx", self.api.documents[0]["file_path"])
        self.assertEqual(len(self.chat.exports), 1)
        self.assertTrue(any("Экспорт готов" in str(item["text"]) for item in self.api.messages))
        self.assertFalse(any("ушла на почту" in str(item["text"]) for item in self.api.messages))

    def test_run_export_with_mailer_sends_email_copy(self) -> None:
        mailer = FakeMailer()
        bot = TelegramConsoleBot(self.config, self.api, self.projects, self.chat, mailer=mailer)

        bot._run_export(1, "default", "article:demo")

        self.assertEqual(len(self.api.documents), 1)
        self.assertEqual(len(mailer.calls), 1)
        self.assertIn("demo.docx", mailer.calls[0]["file_path"])
        self.assertEqual(mailer.calls[0]["artifact_kind"], "статья")
        self.assertIn("ушла на почту", str(self.api.messages[-1]["text"]))

    def test_run_export_mailer_failure_does_not_block_telegram_delivery(self) -> None:
        mailer = FakeMailer(error=EmailDeliveryError("smtp timeout"))
        bot = TelegramConsoleBot(self.config, self.api, self.projects, self.chat, mailer=mailer)

        bot._run_export(1, "default", "thesis")

        self.assertEqual(len(self.api.documents), 1)
        self.assertEqual(len(mailer.calls), 1)
        self.assertIn("не отправилась", str(self.api.messages[-1]["text"]))
        self.assertIn("smtp timeout", str(self.api.messages[-1]["text"]))


class TelegramConsoleBotProjectSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        self.bot_home = self.workspace / "bot-home"
        self.bot_home.mkdir(parents=True, exist_ok=True)
        self.repo_a = self.workspace / "alpha"
        self.repo_b = self.workspace / "beta"
        build_fake_repo(self.repo_a)
        build_fake_repo(self.repo_b)
        write_projects_registry(
            self.bot_home,
            [
                {
                    "id": "alpha",
                    "title": "Диплом А",
                    "root_dir": str(self.repo_a),
                    "capabilities": ["thesis", "article"],
                },
                {
                    "id": "beta",
                    "title": "Диплом Б",
                    "root_dir": str(self.repo_b),
                    "capabilities": ["thesis"],
                },
            ],
        )
        self.projects = ProjectService(self.bot_home)
        self.api = FakeApi()
        self.chat = FakeChatService()
        self.chat.states["alpha"] = ProjectChatState(
            project_id="alpha",
            session_id="alpha-session",
            last_assistant_summary="Дописываю введение и проверяю выводы.",
        )
        self.config = TelegramConsoleConfig(
            root_dir=self.bot_home,
            token="test-token",
            allowed_chat_id=1,
            poll_timeout=1,
        )
        self.bot = TelegramConsoleBot(self.config, self.api, self.projects, self.chat)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_text_without_active_project_opens_project_picker(self) -> None:
        self.bot._handle_message({"chat": {"id": 1}, "text": "Продолжай работу"})
        text = str(self.api.messages[-1]["text"])
        self.assertIn("Сначала выбери активный проект", text)
        self.assertIn("Диплом А", text)
        buttons = self.api.messages[-1]["reply_markup"]["inline_keyboard"]
        flat_labels = [button["text"] for row in buttons for button in row]
        self.assertIn("📚 Диплом А · alpha", flat_labels)

    def test_project_selection_happens_via_callback(self) -> None:
        self.bot._handle_callback(
            {
                "id": "cb-1",
                "from": {"id": 1},
                "data": "project:use:beta",
                "message": {"chat": {"id": 1}},
            }
        )
        self.assertEqual(self.projects.store.get_active_project_id(), "beta")
        self.assertIn("Проект переключен", str(self.api.messages[-1]["text"]))
        self.assertIn("Диплом Б", str(self.api.messages[-1]["text"]))

    def test_project_menu_shows_title_id_and_focus(self) -> None:
        self.bot._show_projects_menu(1)
        text = str(self.api.messages[-1]["text"])
        self.assertIn("Диплом А", text)
        self.assertIn("`alpha`", text)
        self.assertIn("Дописываю введение", text)

    def test_running_bot_sees_project_added_later_without_restart(self) -> None:
        self.bot._show_projects_menu(1)
        self.assertNotIn("Гамма", str(self.api.messages[-1]["text"]))

        repo_c = self.workspace / "gamma"
        build_fake_repo(repo_c)
        self.projects.register_project("Гамма", repo_c)

        self.bot._show_projects_menu(1)
        text = str(self.api.messages[-1]["text"])
        self.assertIn("Гамма", text)
        self.assertIn("gamma", text)

    def test_busy_chat_message_is_returned_to_user(self) -> None:
        self.projects.set_active_project("alpha")
        self.chat.raise_busy = True

        self.bot._handle_message({"chat": {"id": 1}, "text": "Продолжай писать"})

        self.assertIn("Я уже отвечаю", str(self.api.messages[-1]["text"]))

    def test_export_button_uses_active_project(self) -> None:
        self.projects.set_active_project("beta")
        self.bot._handle_message({"chat": {"id": 1}, "text": "📦 Экспорт"})

        self.assertEqual(len(self.api.documents), 1)
        self.assertIn("thesis-draft.docx", self.api.documents[0]["file_path"])

    def test_project_command_text_is_not_intercepted(self) -> None:
        self.projects.set_active_project("alpha")
        self.bot._handle_message({"chat": {"id": 1}, "text": "/project current"})

        self.assertEqual(len(self.chat.started), 1)
        self.assertEqual(self.chat.started[0]["prompt"], "/project current")

    def test_reset_chat_text_is_not_intercepted(self) -> None:
        self.projects.set_active_project("alpha")

        self.bot._handle_message({"chat": {"id": 1}, "text": "/resetchat"})

        self.assertEqual(len(self.chat.started), 1)
        self.assertEqual(self.chat.started[0]["prompt"], "/resetchat")


class ActionSpecRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def load_active_work(self):
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        return workspace, work

    def test_registry_covers_public_launch_actions(self) -> None:
        thesis_actions = {spec.action for spec in list_action_specs("thesis")}
        article_actions = {spec.action for spec in list_action_specs("article")}

        self.assertEqual(thesis_actions, set(work_cli_module.THESIS_PRESETS))
        self.assertEqual(article_actions, set(work_cli_module.ARTICLE_COMMANDS))

    def test_thesis_contract_resolution_exposes_allowed_writes_and_repair_policy(self) -> None:
        workspace, work = self.load_active_work()
        profile = resolve_standard_profile(self.root, workspace, work, lane="thesis", requested_profile_id=None)
        target_path = self.root / TEST_THESIS_SECTION
        contract = build_thesis_execution_contract(
            work=work,
            profile=profile,
            action="verify",
            target_path=target_path,
            target_rel=TEST_THESIS_SECTION.as_posix(),
            related_context=[
                self.root / "AGENTS.md",
                self.root / "workspace.toml",
                work.work_canon_path,
                target_path,
            ],
            review_path=self.root / TEST_THESIS_REVIEW,
            sync_hint_path=work.thesis.sync_dir / "{date}-verify-01-introduction.md",
        )

        self.assertEqual(contract.lane, "thesis")
        self.assertEqual(contract.action, "verify")
        self.assertIn("blocked-primary-support", contract.terminal_statuses)
        self.assertTrue(contract.repair_policy.eligible)
        self.assertEqual(contract.repair_policy.max_iterations, 1)
        self.assertTrue(contract.repair_policy.safe_only)
        allowed_paths = {item.path for item in contract.allowed_write_scopes}
        self.assertIn(str(target_path), allowed_paths)
        self.assertIn(str(work.thesis.sync_dir), allowed_paths)
        self.assertTrue(any(item.name == "target-file" for item in contract.required_outputs))
        self.assertTrue(any(gate.gate_id == "dynamic-material-refresh" for gate in contract.quality_gates))

    def test_article_contract_resolution_exposes_terminal_statuses_and_bundle_outputs(self) -> None:
        workspace, work = self.load_active_work()
        profile = resolve_standard_profile(self.root, workspace, work, lane="article", requested_profile_id=None)
        bundle = {
            "brief": self.root / TEST_ARTICLE_BRIEF,
            "evidence_pack": self.root / TEST_WORK_ROOT / "articles" / "evidence" / "demo.md",
            "claim_map": self.root / TEST_WORK_ROOT / "articles" / "claim-maps" / "demo.md",
            "draft": self.root / TEST_ARTICLE_DRAFT,
            "review": self.root / TEST_WORK_ROOT / "articles" / "reviews" / "demo.md",
            "final_markdown": self.root / TEST_ARTICLE_FINAL,
            "checklist": self.root / TEST_ARTICLE_CHECKLIST,
            "docx": self.root / "output" / "docx" / TEST_WORK_ID / "articles" / "demo.docx",
        }
        contract = build_article_execution_contract(
            work=work,
            profile=profile,
            action="review",
            related_context=[
                self.root / "AGENTS.md",
                self.root / "workspace.toml",
                work.work_canon_path,
                self.root / TEST_ARTICLE_DRAFT,
            ],
            bundle=bundle,
            topic=None,
            input_brief_path=None,
            target_path=self.root / TEST_ARTICLE_DRAFT,
            target_rel=TEST_ARTICLE_DRAFT.as_posix(),
        )

        self.assertEqual(contract.lane, "article")
        self.assertEqual(contract.action, "review")
        self.assertEqual(
            contract.terminal_statuses,
            ("submission-ready", "strong-draft", "strong-draft-with-blockers"),
        )
        self.assertTrue(contract.repair_policy.eligible)
        self.assertEqual(contract.repair_policy.max_iterations, 2)
        self.assertTrue(any(item.name == "review-sheet" for item in contract.required_outputs))
        self.assertTrue(any(gate.gate_id == "standards-consistency" for gate in contract.quality_gates))

    def test_article_finalize_contract_is_public_and_scoped(self) -> None:
        workspace, work = self.load_active_work()
        profile = resolve_standard_profile(self.root, workspace, work, lane="article", requested_profile_id=None)
        bundle = article_bundle_paths(work, "demo")

        contract = build_article_execution_contract(
            work=work,
            profile=profile,
            action="finalize",
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path],
            bundle=bundle,
            topic=None,
            input_brief_path=None,
            target_path=self.root / TEST_ARTICLE_FINAL,
            target_rel=TEST_ARTICLE_FINAL.as_posix(),
        )

        self.assertEqual(contract.action, "finalize")
        self.assertIn("submission-ready", contract.terminal_statuses)
        self.assertFalse(contract.repair_policy.eligible)
        self.assertTrue(any(item.name == "checklist" for item in contract.required_outputs))
        self.assertTrue(any(item.name == "docx" for item in contract.required_outputs))


class ContractGateEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        self.workspace = load_workspace_config(self.root)
        self.work = load_work_config(self.workspace, TEST_WORK_ID)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def build_article_contract(self, slug: str, action: str = "article"):
        profile = resolve_standard_profile(self.root, self.workspace, self.work, lane="article", requested_profile_id=None)
        bundle = article_bundle_paths(self.work, slug)
        return (
            profile,
            bundle,
            build_article_execution_contract(
                work=self.work,
                profile=profile,
                action=action,
                related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", self.work.work_canon_path],
                bundle=bundle,
                topic=None,
                input_brief_path=bundle["brief"],
                target_path=bundle["draft"] if action != "article" else None,
                target_rel=relative_to_workspace(self.workspace, bundle["draft"]) if action != "article" else None,
            ),
        )

    def test_missing_required_output_blocks_export(self) -> None:
        profile, bundle, contract = self.build_article_contract("gate-missing-output")
        write_file(bundle["brief"], "# Brief\n")

        gates = [item.to_dict() for item in evaluate_contract_gates(contract=contract, profile=profile)]

        evidence_gate = next(item for item in gates if item["gate_id"] == "required-output:evidence-pack")
        self.assertEqual(evidence_gate["status"], "block")
        self.assertTrue(evidence_gate["blocks_export"])
        self.assertTrue(evidence_gate["blocks_submission_ready"])

    def test_raw_standards_missing_blocks_formal_readiness(self) -> None:
        profile, _, contract = self.build_article_contract("gate-raw-missing", action="review")

        gates = [item.to_dict() for item in evaluate_contract_gates(contract=contract, profile=profile)]

        raw_gate = next(item for item in gates if item["gate_id"] == "standards-raw")
        self.assertEqual(raw_gate["status"], "block")
        self.assertTrue(raw_gate["blocks_submission_ready"])
        self.assertTrue(raw_gate["blocks_export"])

    def test_conflict_flag_blocks_formal_readiness(self) -> None:
        rewrite_work_profiles(self.root, article_profile="journal-jrp")
        write_raw_manifest(self.root, "journal-jrp")
        work = load_work_config(self.workspace, TEST_WORK_ID)
        profile = resolve_standard_profile(self.root, self.workspace, work, lane="article", requested_profile_id=None)
        bundle = article_bundle_paths(work, "gate-conflict")
        contract = build_article_execution_contract(
            work=work,
            profile=profile,
            action="review",
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path],
            bundle=bundle,
            topic=None,
            input_brief_path=None,
            target_path=bundle["draft"],
            target_rel=relative_to_workspace(self.workspace, bundle["draft"]),
        )

        gates = [item.to_dict() for item in evaluate_contract_gates(contract=contract, profile=profile)]

        conflict_gate = next(item for item in gates if item["gate_id"] == "standards-conflict")
        self.assertEqual(conflict_gate["status"], "block")
        self.assertTrue(conflict_gate["blocks_submission_ready"])
        self.assertTrue(conflict_gate["blocks_export"])

    def test_clean_gates_do_not_block_export(self) -> None:
        write_raw_manifest(self.root, "ru-law-article-v1")
        profile, bundle, contract = self.build_article_contract("gate-clean")
        write_file(bundle["brief"], "# Brief\n")
        write_file(bundle["evidence_pack"], "# Evidence\n")
        write_file(bundle["claim_map"], "# Claim map\n")
        write_file(bundle["draft"], "# Draft\n")

        gates = [item.to_dict() for item in evaluate_contract_gates(contract=contract, profile=profile)]

        self.assertTrue(gates)
        self.assertFalse(any(item["blocks_export"] for item in gates))
        self.assertTrue(all(item["status"] in {"pass", "not-applicable"} for item in gates))


class AutonomousControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        self.orchestrator = WorkflowOrchestrator(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def work_state(self) -> dict[str, object]:
        return self.orchestrator.get_artifact_status("work")

    def test_policy_blocks_active_run(self) -> None:
        state = self.work_state()
        state["runtime"]["active_run"] = {
            "run_id": "default:active",
            "lane": "article",
            "action": "review",
        }

        decision = evaluate_autonomous_policy(
            work_state=state,
            action={"command": "launch-academic review works/demo-work/articles/drafts/demo.md", "intent": "review", "lane": "article"},
            mode="autonomous-safe",
        ).to_dict()

        self.assertEqual(decision["decision"], "blocked")
        self.assertIn("active-run", decision["blocking_categories"])
        self.assertEqual(decision["readiness_claim"], "none")

    def test_policy_blocks_export_when_standards_blocker_exists(self) -> None:
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        rewrite_work_profiles(self.root, article_profile="journal-jrp")
        write_file(self.root / TEST_WORK_ROOT / "articles" / "reviews" / "demo.md", "# Review\n")
        state = self.work_state()

        decision = evaluate_autonomous_policy(
            work_state=state,
            action={"command": "export-article-docx works/demo-work/articles/final/demo.md", "intent": "export", "lane": "article"},
            mode="autonomous-full",
        ).to_dict()

        self.assertEqual(decision["decision"], "blocked")
        self.assertIn("standards-consistency", decision["blocking_categories"])
        self.assertIsNone(decision["safe_command"])

    def test_policy_blocks_export_when_contract_gate_blocks_export(self) -> None:
        state = self.work_state()
        state["known_blockers"] = [
            {
                "category": "contract-gate",
                "code": "article-required-output-checklist",
                "message": "Checklist missing.",
                "lane": "article",
                "details": {
                    "gate_id": "required-output:checklist",
                    "blocks_export": True,
                    "blocks_submission_ready": True,
                },
            }
        ]

        decision = evaluate_autonomous_policy(
            work_state=state,
            action={"command": "export-article-docx works/demo-work/articles/final/demo.md", "intent": "export", "lane": "article"},
            mode="autonomous-full",
        ).to_dict()

        self.assertEqual(decision["decision"], "blocked")
        self.assertIn("required-output:checklist", decision["blocking_gate_ids"])

    def test_policy_allows_safe_review_in_autonomous_safe_mode(self) -> None:
        write_raw_manifest(self.root, "thesis-v1")
        write_raw_manifest(self.root, "ru-law-article-v1")
        state = self.work_state()
        action = state["suggested_next_action"]

        decision = evaluate_autonomous_policy(work_state=state, action=action, mode="autonomous-safe").to_dict()

        self.assertEqual(decision["decision"], "allowed")
        self.assertEqual(decision["intent"], "review")
        self.assertIn("launch-academic review", decision["safe_command"])

    def test_policy_requires_confirmation_for_drafting_export_and_finalize(self) -> None:
        state = self.work_state()
        for action in (
            {"command": "launch-thesis write-section works/demo-work/thesis/manuscript/sections/01-introduction.md", "intent": "draft", "lane": "thesis"},
            {"command": "export-thesis-docx", "intent": "export", "lane": "thesis"},
            {"command": "launch-academic finalize works/demo-work/articles/final/demo.md", "intent": "finalize-checklist", "lane": "article"},
        ):
            decision = evaluate_autonomous_policy(work_state=state, action=action, mode="autonomous-safe").to_dict()
            self.assertEqual(decision["decision"], "requires-confirmation")
            self.assertEqual(decision["readiness_claim"], "none")

    def test_policy_blocks_legacy_root_target_for_autonomous_run(self) -> None:
        state = self.work_state()

        decision = evaluate_autonomous_policy(
            work_state=state,
            action={"command": "launch-thesis verify manuscript/sections/01-introduction.md", "intent": "verify", "lane": "thesis"},
            mode="autonomous-safe",
            target_resolution={"warning_code": "legacy-root-target"},
        ).to_dict()

        self.assertEqual(decision["decision"], "blocked")
        self.assertIn("noncanonical-target", decision["blocking_categories"])

    def test_planner_uses_unblocked_continuation_when_standards_block_export(self) -> None:
        rewrite_work_profiles(self.root, article_profile="journal-jrp")
        state = self.work_state()

        plan = build_autonomous_plan(work_state=state, mode="autonomous-safe", max_steps=3).to_dict()

        self.assertEqual(plan["mode"], "autonomous-safe")
        self.assertEqual(plan["readiness_claim"], "none")
        self.assertTrue(plan["steps"])
        self.assertEqual(plan["steps"][0]["policy"]["decision"], "allowed")
        self.assertIn("launch-academic review", plan["steps"][0]["command"])

    def test_autonomous_full_allows_export_after_deterministic_finalization_check(self) -> None:
        write_raw_manifest(self.root, "thesis-v1")
        write_raw_manifest(self.root, "ru-law-article-v1")
        write_file(self.root / TEST_WORK_ROOT / "articles" / "reviews" / "demo.md", "# Review\n")
        state = self.work_state()

        plan = build_autonomous_plan(work_state=state, mode="autonomous-full", max_steps=1).to_dict()

        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["steps"])
        first_step = plan["steps"][0]
        self.assertEqual(first_step["action_id"], "export-article-docx")
        self.assertEqual(first_step["policy"]["decision"], "allowed")
        self.assertEqual(first_step["finalization_check"]["status"], "pass")
        self.assertEqual(first_step["policy"]["readiness_claim"], "none")


class FinalizationEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        write_raw_manifest(self.root, "ru-law-article-v1")
        self.workspace = load_workspace_config(self.root)
        self.work = load_work_config(self.workspace, TEST_WORK_ID)
        self.bundle = article_bundle_paths(self.work, "final-check")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_finalization_blocks_missing_final_markdown(self) -> None:
        result = evaluate_article_finalization(bundle=self.bundle, readiness_status="strong-draft", blockers=[], contract_gates=[]).to_dict()

        self.assertEqual(result["status"], "block")
        self.assertIn("final-markdown-missing", result["blocked_reasons"])
        self.assertEqual(result["readiness_claim"], "none")

    def test_finalization_blocks_submission_ready_when_gates_or_primary_blockers_exist(self) -> None:
        write_file(self.bundle["final_markdown"], "# Final\n")
        write_file(self.bundle["checklist"], "# Checklist\n")
        write_file(self.bundle["review"], "# Review\n")

        result = evaluate_article_finalization(
            bundle=self.bundle,
            readiness_status="submission-ready",
            blockers=[
                {
                    "category": "primary-support",
                    "code": "primary-gap",
                    "message": "Need primary source.",
                }
            ],
            contract_gates=[
                {
                    "gate_id": "standards-raw",
                    "status": "block",
                    "reason": "Raw standards missing.",
                    "blocks_submission_ready": True,
                }
            ],
        ).to_dict()

        self.assertEqual(result["status"], "block")
        self.assertIn("primary-support-blockers", result["blocked_reasons"])
        self.assertIn("gate:standards-raw", result["blocked_reasons"])
        self.assertEqual(result["effective_readiness_status"], "strong-draft-with-blockers")

    def test_clean_finalization_is_export_ready_without_overclaim(self) -> None:
        write_file(self.bundle["final_markdown"], "# Final\n")
        write_file(self.bundle["checklist"], "# Checklist\n")
        write_file(self.bundle["review"], "# Review\n")

        result = evaluate_article_finalization(
            bundle=self.bundle,
            readiness_status="strong-draft",
            blockers=[],
            contract_gates=[
                {
                    "gate_id": "standards-raw",
                    "status": "pass",
                    "reason": "Raw standards available.",
                }
            ],
        ).to_dict()

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["finalization_status"], "export-ready")
        self.assertIn("docx", result["allowed_exports"])
        self.assertEqual(result["readiness_claim"], "none")


class RepairKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def load_active_work(self):
        workspace = load_workspace_config(self.root)
        work = load_work_config(workspace, TEST_WORK_ID)
        return workspace, work

    def test_bounded_article_repair_loop_recovers_after_one_iteration(self) -> None:
        workspace, work = self.load_active_work()
        profile = resolve_standard_profile(self.root, workspace, work, lane="article", requested_profile_id=None)
        bundle = {
            "brief": self.root / TEST_ARTICLE_BRIEF,
            "evidence_pack": self.root / TEST_WORK_ROOT / "articles" / "evidence" / "demo.md",
            "claim_map": self.root / TEST_WORK_ROOT / "articles" / "claim-maps" / "demo.md",
            "draft": self.root / TEST_ARTICLE_DRAFT,
            "review": self.root / TEST_WORK_ROOT / "articles" / "reviews" / "demo.md",
            "final_markdown": self.root / TEST_ARTICLE_FINAL,
            "checklist": self.root / TEST_ARTICLE_CHECKLIST,
            "docx": self.root / "output" / "docx" / TEST_WORK_ID / "articles" / "demo.docx",
        }
        contract = build_article_execution_contract(
            work=work,
            profile=profile,
            action="repair",
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path],
            bundle=bundle,
            topic=None,
            input_brief_path=None,
            target_path=self.root / TEST_ARTICLE_DRAFT,
            target_rel=TEST_ARTICLE_DRAFT.as_posix(),
        )
        initial_blockers = [
            Blocker(
                category="primary-support",
                code="unsupported-claim",
                message="Strong claim is missing primary support.",
                repairable=True,
                blocks_statuses=("submission-ready",),
            )
        ]

        repair_calls: list[int] = []

        def repair_fn(plan):
            repair_calls.append(plan.repair_iteration)
            return {"patched": True}

        def evaluate_fn(plan, repair_result):
            self.assertTrue(repair_result["patched"])
            return []

        outcome = run_bounded_repair_loop(
            contract=contract,
            initial_blockers=initial_blockers,
            repair_fn=repair_fn,
            evaluate_fn=evaluate_fn,
        )

        self.assertEqual(repair_calls, [1])
        self.assertEqual(outcome.terminal_reason, "ready")
        self.assertEqual(outcome.repair_iteration, 1)
        self.assertEqual(outcome.remaining_blockers, ())
        self.assertEqual(len(outcome.decisions), 2)
        self.assertEqual(outcome.decisions[0].action, "repair")
        self.assertEqual(outcome.decisions[-1].action, "stop")

    def test_bounded_article_repair_loop_stops_at_max_iterations(self) -> None:
        workspace, work = self.load_active_work()
        profile = resolve_standard_profile(self.root, workspace, work, lane="article", requested_profile_id=None)
        bundle = {
            "brief": self.root / TEST_ARTICLE_BRIEF,
            "evidence_pack": self.root / TEST_WORK_ROOT / "articles" / "evidence" / "demo.md",
            "claim_map": self.root / TEST_WORK_ROOT / "articles" / "claim-maps" / "demo.md",
            "draft": self.root / TEST_ARTICLE_DRAFT,
            "review": self.root / TEST_WORK_ROOT / "articles" / "reviews" / "demo.md",
            "final_markdown": self.root / TEST_ARTICLE_FINAL,
            "checklist": self.root / TEST_ARTICLE_CHECKLIST,
            "docx": self.root / "output" / "docx" / TEST_WORK_ID / "articles" / "demo.docx",
        }
        contract = build_article_execution_contract(
            work=work,
            profile=profile,
            action="repair",
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path],
            bundle=bundle,
            topic=None,
            input_brief_path=None,
            target_path=self.root / TEST_ARTICLE_DRAFT,
            target_rel=TEST_ARTICLE_DRAFT.as_posix(),
        )
        blocker = Blocker(
            category="primary-support",
            code="persistent-gap",
            message="Primary support gap still remains.",
            repairable=True,
            blocks_statuses=("submission-ready",),
        )

        outcome = run_bounded_repair_loop(
            contract=contract,
            initial_blockers=[blocker],
            repair_fn=lambda plan: {"iteration": plan.repair_iteration},
            evaluate_fn=lambda plan, result: [blocker],
        )

        self.assertEqual(outcome.terminal_reason, "max-repair-iterations")
        self.assertEqual(outcome.repair_iteration, 2)
        self.assertEqual(len(outcome.plans), 2)
        self.assertEqual(outcome.remaining_blockers, (blocker,))

    def test_thesis_safe_repair_plan_filters_broad_style_blockers(self) -> None:
        workspace, work = self.load_active_work()
        profile = resolve_standard_profile(self.root, workspace, work, lane="thesis", requested_profile_id=None)
        contract = build_thesis_execution_contract(
            work=work,
            profile=profile,
            action="verify",
            target_path=self.root / TEST_THESIS_SECTION,
            target_rel=TEST_THESIS_SECTION.as_posix(),
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", work.work_canon_path],
            review_path=self.root / TEST_THESIS_REVIEW,
            sync_hint_path=work.thesis.sync_dir / "{date}-verify-01-introduction.md",
        )
        plan = build_repair_plan(
            contract=contract,
            blockers=[
                Blocker(category="style", code="generic-voice", message="Text sounds too generic.", repairable=True),
                Blocker(category="citation", code="missing-footnote", message="A strong claim lacks a footnote.", repairable=True),
                Blocker(category="review", code="overclaim", message="Conclusion is overstated.", repairable=True),
            ],
            repair_iteration=1,
        )

        self.assertTrue(plan.safe_only)
        self.assertEqual({item.category for item in plan.blockers}, {"citation", "review"})
        self.assertIn("citation", plan.focus_areas)
        self.assertIn("review", plan.focus_areas)
        self.assertNotIn("style", plan.focus_areas)

    def test_runtime_status_round_trip_preserves_repair_fields(self) -> None:
        payload = build_runtime_status(
            record_id="alpha:repair-run",
            entity_kind="workflow-run",
            status="failed",
            stage="repairing",
            project_id="alpha",
            work_id=TEST_WORK_ID,
            lane="article",
            action="repair",
            summary="Repair loop stopped on unresolved blocker.",
            repair_iteration=2,
            terminal_reason="max-repair-iterations",
            blockers=[
                {
                    "category": "primary-support",
                    "code": "persistent-gap",
                    "message": "Primary support gap still remains.",
                }
            ],
            repair_decision={
                "action": "stop",
                "reason": "repair-limit-reached",
                "terminal_reason": "max-repair-iterations",
            },
            contract_gates=[
                {
                    "gate_id": "required-output:checklist",
                    "status": "block",
                    "reason": "Checklist is missing.",
                    "blocks_export": True,
                    "blocks_submission_ready": True,
                }
            ],
        )

        record = record_from_payload(payload, runtime_dir=None, status_path=None, source="status")

        assert record is not None
        self.assertIn("blockers", payload)
        self.assertIn("repair_decision", payload)
        self.assertIn("contract_gates", payload)
        self.assertEqual(record.repair_iteration, 2)
        self.assertEqual(record.terminal_reason, "max-repair-iterations")
        self.assertEqual(record.blockers[0]["code"], "persistent-gap")
        self.assertEqual(record.repair_decision["action"], "stop")
        self.assertEqual(record.contract_gates[0]["gate_id"], "required-output:checklist")


class ThesisRepairPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)
        workspace = load_workspace_config(self.root)
        self.work = load_work_config(workspace, TEST_WORK_ID)
        self.profile = resolve_standard_profile(self.root, workspace, self.work, lane="thesis", requested_profile_id=None)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def build_contract(self, action: str = "verify"):
        return build_thesis_execution_contract(
            work=self.work,
            profile=self.profile,
            action=action,
            target_path=self.root / TEST_THESIS_SECTION,
            target_rel=TEST_THESIS_SECTION.as_posix(),
            related_context=[self.root / "AGENTS.md", self.root / "workspace.toml", self.work.work_canon_path],
            review_path=self.root / TEST_THESIS_REVIEW,
            sync_hint_path=self.work.thesis.sync_dir / "{date}-verify-01-introduction.md",
        )

    def test_planner_routes_primary_support_blocker_to_verify(self) -> None:
        plan = build_thesis_repair_plan(
            section_summary={"kind": "thesis-section-summary", "target": TEST_THESIS_SECTION.as_posix()},
            blockers=[
                Blocker(
                    category="primary-support",
                    code="primary-support-gap",
                    message="Need primary support for the central claim.",
                    repairable=True,
                )
            ],
            contract=self.build_contract(),
            target=TEST_THESIS_SECTION.as_posix(),
            repair_iteration=0,
        )
        payload = plan.to_dict()

        self.assertTrue(payload["eligible"])
        self.assertEqual(payload["suggested_action"], "verify")
        self.assertEqual(payload["target"], TEST_THESIS_SECTION.as_posix())
        self.assertIn("launch-thesis verify", payload["suggested_command"])
        self.assertEqual(payload["terminal_reason"], None)
        self.assertEqual(payload["readiness_claim"], "none")

    def test_planner_rejects_broad_style_only_blocker(self) -> None:
        plan = build_thesis_repair_plan(
            section_summary={"kind": "thesis-section-summary", "target": TEST_THESIS_SECTION.as_posix()},
            blockers=[
                Blocker(
                    category="style",
                    code="generic-voice",
                    message="The whole section sounds too generic.",
                    repairable=True,
                )
            ],
            contract=self.build_contract(action="full-cycle"),
            target=TEST_THESIS_SECTION.as_posix(),
            repair_iteration=0,
        )
        payload = plan.to_dict()

        self.assertFalse(payload["eligible"])
        self.assertEqual(payload["safe_repair_actions"], [])
        self.assertIn("no-safe-thesis-repair-actions", payload["blocked_reasons"])
        self.assertEqual(payload["terminal_reason"], "ready-with-caveats")
        self.assertIsNone(payload["suggested_command"])

    def test_planner_stops_at_max_iteration(self) -> None:
        plan = build_thesis_repair_plan(
            section_summary={"kind": "thesis-section-summary", "target": TEST_THESIS_SECTION.as_posix()},
            blockers=[
                Blocker(
                    category="citation",
                    code="missing-footnote",
                    message="A strong claim still lacks a footnote.",
                    repairable=True,
                )
            ],
            contract=self.build_contract(),
            target=TEST_THESIS_SECTION.as_posix(),
            repair_iteration=1,
        )
        payload = plan.to_dict()

        self.assertFalse(payload["eligible"])
        self.assertEqual(payload["safe_repair_actions"], [])
        self.assertIn("repair-limit-reached", payload["blocked_reasons"])
        self.assertEqual(payload["terminal_reason"], "max-repair-iterations")

    def test_planner_routes_dynamic_legal_material_to_verify(self) -> None:
        plan = build_thesis_repair_plan(
            section_summary={"kind": "thesis-section-summary", "target": TEST_THESIS_SECTION.as_posix()},
            blockers=[
                Blocker(
                    category="dynamic-material",
                    code="dynamic-material-not-refreshed",
                    message="Dynamic legal material still needs a fresh check.",
                    repairable=True,
                )
            ],
            contract=self.build_contract(action="review-section"),
            target=TEST_THESIS_SECTION.as_posix(),
            repair_iteration=0,
        )
        payload = plan.to_dict()

        self.assertTrue(payload["eligible"])
        self.assertEqual(payload["suggested_action"], "verify")
        self.assertIn("requires verification before drafting", payload["safe_repair_actions"][0]["reason"])


class ArticleBundleLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_fake_repo(self.root)
        write_sample_standards_registry(self.root)
        write_sample_normalized_profiles(self.root)
        self.fake_codex = self.root / "bin" / "fake-codex"
        build_fake_codex(self.fake_codex)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_launch_academic_run_writes_article_bundle_manifest(self) -> None:
        brief_path = self.root / TEST_WORK_ROOT / "articles" / "briefs" / "demo-brief.md"
        write_file(brief_path, "# Fresh brief\n")
        bundle_manifest = self.root / TEST_WORK_ROOT / "articles" / "runs" / "demo-brief.bundle.json"

        stdout = StringIO()
        stderr = StringIO()
        with patch.dict(os.environ, {"CODEX_BIN": str(self.fake_codex)}, clear=False):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "launch-academic",
                        "article",
                        "--brief",
                        brief_path.relative_to(self.root).as_posix(),
                        "--no-search",
                    ],
                    root_dir=self.root,
                )

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertTrue(bundle_manifest.exists())
        payload = json.loads(bundle_manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["work_id"], TEST_WORK_ID)
        self.assertEqual(payload["article_slug"], "demo-brief")
        self.assertEqual(payload["current_phase"], "briefed")
        self.assertEqual(payload["active_phase"], "drafted")
        self.assertEqual(payload["current_status"], "in-progress")
        self.assertEqual(payload["last_run_status"], "succeeded")
        self.assertEqual(payload["profile_id"], "ru-law-article-v1")
        self.assertEqual(payload["evidence_state"], "missing")
        self.assertTrue(payload["bundle_files"]["brief"]["exists"])
        self.assertFalse(payload["bundle_files"]["draft"]["exists"])
        self.assertIn("Saved article bundle state", stdout.getvalue())

    def test_article_bundle_status_reads_manifest_and_lists_manifest_only_slug(self) -> None:
        runs_dir = self.root / TEST_WORK_ROOT / "articles" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "v1",
            "work_id": TEST_WORK_ID,
            "article_slug": "pending-topic",
            "current_phase": "not-started",
            "current_status": "in-progress",
            "readiness_status": None,
            "active_phase": "drafted",
            "profile_id": "ru-law-article-v1",
            "evidence_state": "missing",
            "checklist_state": "not-started",
            "finalizer_gate_state": "not-ready",
            "last_action": "article",
            "last_run_status": "started",
            "latest_run_manifest": None,
            "latest_output_file": None,
            "latest_runtime_record_ids": [],
            "bundle_files": {},
            "execution_contract": None,
            "inputs": {"topic": "Pending topic", "input_brief": None, "target_path": None},
            "updated_at": "2026-04-18T10:00:00+00:00",
        }
        (runs_dir / "pending-topic.bundle.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        orchestrator = WorkflowOrchestrator(self.root)
        self.assertIn("pending-topic", orchestrator.list_article_slugs())
        status = orchestrator.get_artifact_status("article:pending-topic")

        self.assertEqual(status["kind"], "article-bundle")
        self.assertTrue(status["bundle_state_manifest_exists"])
        self.assertEqual(status["state"]["article_slug"], "pending-topic")
        self.assertEqual(status["state"]["current_status"], "in-progress")
        self.assertEqual(status["state"]["active_phase"], "drafted")


class TelegramConsoleCliTests(unittest.TestCase):
    def test_standards_intake_creates_manifest_and_normalized_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_sample_standards_registry(root)

            fetch_payloads = {
                "https://example.test/jrp-home.html": (b"<html>home</html>", "https://example.test/jrp-home.html", "text/html"),
                "https://example.test/jrp-rules.html": (b"<html>rules</html>", "https://example.test/jrp-rules.html", "text/html"),
            }

            stdout = StringIO()
            stderr = StringIO()
            with patch(
                "telegram_console.standards.fetch_url_bytes",
                side_effect=lambda url: fetch_payloads[url],
            ):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(["standards-intake", "journal-jrp"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            manifest_path = root / "meta/standards/raw/journal-jrp/manifest.json"
            normalized_path = root / "meta/standards/normalized/journal-jrp.md"
            self.assertTrue(manifest_path.exists())
            self.assertTrue(normalized_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["sources"]), 2)
            self.assertIn("Resolved profile: journal-jrp", stdout.getvalue())

    def test_standards_refresh_rewrites_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_sample_standards_registry(root)

            stdout = StringIO()
            stderr = StringIO()
            with patch(
                "telegram_console.standards.fetch_url_bytes",
                side_effect=[
                    (b"first-home", "https://example.test/jrp-home.html", "text/html"),
                    (b"first-rules", "https://example.test/jrp-rules.html", "text/html"),
                    (b"second-home", "https://example.test/jrp-home.html", "text/html"),
                    (b"second-rules", "https://example.test/jrp-rules.html", "text/html"),
                ],
            ):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(["standards-intake", "journal-jrp"], root_dir=root)
                    self.assertEqual(code, 0)
                    manifest_before = json.loads(
                        (root / "meta/standards/raw/journal-jrp/manifest.json").read_text(encoding="utf-8")
                    )
                    code = work_cli_module.main(["standards-refresh", "journal-jrp"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            manifest_after = json.loads((root / "meta/standards/raw/journal-jrp/manifest.json").read_text(encoding="utf-8"))
            self.assertNotEqual(
                manifest_before["sources"][0]["checksum_sha256"],
                manifest_after["sources"][0]["checksum_sha256"],
            )

    def test_standards_status_reports_conflict_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_sample_standards_registry(root)
            write_sample_normalized_profiles(root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["standards-status", "missing-profile"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Requested profile: missing-profile", stdout.getvalue())
            self.assertIn("Resolved profile: ru-law-article-v1", stdout.getvalue())
            self.assertIn("Fallback profile: ru-law-article-v1", stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["standards-status", "sogu-vkr-2025"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Conflict flag: yes", stdout.getvalue())

    def test_launch_thesis_dry_run_uses_bound_profile_context(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_sample_standards_registry(root)
            write_sample_normalized_profiles(root)
            rewrite_work_profiles(root, thesis_profile="sogu-vkr-2025")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["launch-thesis", "write-section", "manuscript/sections/01-introduction.md", "--dry-run"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Requested profile: sogu-vkr-2025", stdout.getvalue())
            self.assertIn("Resolved profile: sogu-vkr-2025", stdout.getvalue())
            self.assertIn("meta/standards/raw/sogu-vkr-2025", stdout.getvalue())
            self.assertIn("Execution contract:", stdout.getvalue())
            self.assertIn("Target validation:", stdout.getvalue())
            self.assertIn("Repair policy:", stdout.getvalue())
            self.assertIn("Target resolution mode: legacy-root", stdout.getvalue())
            self.assertIn("Legacy target warning:", stdout.getvalue())
            self.assertIn(TEST_THESIS_SECTION.as_posix(), stdout.getvalue())

    def test_launch_thesis_manifest_includes_target_resolution_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_sample_standards_registry(root)
            write_sample_normalized_profiles(root)

            def fake_run_codex(_: Path, __: str, out_file: Path, ___: bool, ____: str | None) -> None:
                write_file(out_file, "thesis output\n")

            with patch.object(work_cli_module, "_run_codex", side_effect=fake_run_codex):
                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(
                        ["launch-thesis", "write-section", "manuscript/sections/01-introduction.md"],
                        root_dir=root,
                    )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            manifests = sorted((root / "output" / "runs" / TEST_WORK_ID / "thesis").glob("*-write-section.meta.json"))
            self.assertTrue(manifests)
            payload = json.loads(manifests[-1].read_text(encoding="utf-8"))
            self.assertEqual(payload["target"]["relative"], TEST_THESIS_SECTION.as_posix())
            self.assertEqual(payload["target_resolution"]["warning_code"], "legacy-root-target")
            self.assertEqual(payload["target_resolution"]["resolution_mode"], "legacy-root")

    def test_launch_academic_dry_run_uses_requested_journal_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_sample_standards_registry(root)
            write_sample_normalized_profiles(root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "launch-academic",
                        "article",
                        "--topic",
                        "Demo topic",
                        "--profile",
                        "journal-jrp",
                        "--dry-run",
                    ],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Requested profile: journal-jrp", stdout.getvalue())
            self.assertIn("Resolved profile: journal-jrp", stdout.getvalue())
            self.assertIn("Execution contract:", stdout.getvalue())
            self.assertIn("Terminal statuses:", stdout.getvalue())

    def test_launch_academic_review_dry_run_shows_legacy_target_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_sample_standards_registry(root)
            write_sample_normalized_profiles(root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "launch-academic",
                        "review",
                        "articles/drafts/demo.md",
                        "--dry-run",
                    ],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Target resolution mode: legacy-root", stdout.getvalue())
            self.assertIn("Legacy target warning:", stdout.getvalue())
            self.assertIn(TEST_ARTICLE_DRAFT.as_posix(), stdout.getvalue())

    def test_launch_academic_finalize_dry_run_uses_public_finalizer_action(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_sample_standards_registry(root)
            write_sample_normalized_profiles(root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "launch-academic",
                        "finalize",
                        "articles/final/demo.md",
                        "--dry-run",
                    ],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Command: finalize", stdout.getvalue())
            self.assertIn("Article finalization", stdout.getvalue())
            self.assertIn("$academic-finalizer", stdout.getvalue())
            self.assertIn("Target validation:", stdout.getvalue())

    def test_launch_academic_review_manifest_includes_target_resolution_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_sample_standards_registry(root)
            write_sample_normalized_profiles(root)

            def fake_run_codex(_: Path, __: str, out_file: Path, ___: bool, ____: str | None) -> None:
                write_file(out_file, "article output\n")

            with patch.object(work_cli_module, "_run_codex", side_effect=fake_run_codex):
                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(
                        [
                            "launch-academic",
                            "review",
                            "articles/drafts/demo.md",
                        ],
                        root_dir=root,
                    )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            manifests = sorted((root / "output" / "runs" / TEST_WORK_ID / "article").glob("*-review-*.meta.json"))
            self.assertTrue(manifests)
            payload = json.loads(manifests[-1].read_text(encoding="utf-8"))
            self.assertEqual(payload["target_path"], TEST_ARTICLE_DRAFT.as_posix())
            self.assertEqual(payload["target_resolution"]["warning_code"], "legacy-root-target")
            self.assertEqual(payload["target_resolution"]["resolution_mode"], "legacy-root")

    def test_launch_academic_dry_run_falls_back_to_generic_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_sample_standards_registry(root)
            write_sample_normalized_profiles(root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "launch-academic",
                        "article",
                        "--topic",
                        "Demo topic",
                        "--profile",
                        "missing-profile",
                        "--dry-run",
                    ],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Requested profile: missing-profile", stdout.getvalue())
            self.assertIn("Resolved profile: ru-law-article-v1", stdout.getvalue())
            self.assertIn("Fallback profile: ru-law-article-v1", stdout.getvalue())

    def test_assemble_and_export_commands_remain_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)

            def fake_run_pandoc(input_md: Path, output_docx: Path) -> None:
                write_file(output_docx, f"docx from {input_md}\n")

            with patch.object(work_cli_module, "_run_pandoc", side_effect=fake_run_pandoc):
                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(["assemble-thesis"], root_dir=root)
                self.assertEqual(code, 0)
                self.assertEqual(stderr.getvalue(), "")
                self.assertTrue((root / TEST_WORK_ROOT / "thesis" / "manuscript" / "full-draft.md").exists())

                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(["export-thesis-docx"], root_dir=root)
                self.assertEqual(code, 0)
                self.assertEqual(stderr.getvalue(), "")
                self.assertTrue((root / "output" / "docx" / TEST_WORK_ID / "thesis-draft.docx").exists())

                stdout = StringIO()
                stderr = StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = work_cli_module.main(
                        ["export-article-docx", "articles/final/demo.md"],
                        root_dir=root,
                    )
                self.assertEqual(code, 0)
                self.assertEqual(stderr.getvalue(), "")
                self.assertTrue((root / "output" / "docx" / TEST_WORK_ID / "articles" / "demo.docx").exists())

    def test_work_status_cli_prints_compact_next_safe_action(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Work status:", stdout.getvalue())
            self.assertIn("Scope: signals-only", stdout.getvalue())
            self.assertIn("Next safe action:", stdout.getvalue())
            self.assertIn("launch-academic review", stdout.getvalue())
            self.assertNotIn("{", stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status", "--json"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "work-state")
            self.assertEqual(payload["suggested_next_action"]["action_id"], "article-review")

    def test_work_status_cli_exposes_thesis_repair_plan_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            write_runtime_status_fixture(
                root / "output" / "telegram" / "runtime" / "runs" / "thesis-plan-runtime",
                record_id="default:20260418-thesis-verify",
                entity_kind="workflow-run",
                project_id="default",
                project_title=root.name,
                project_root=root,
                work_id=TEST_WORK_ID,
                work_title="Demo work",
                lane="thesis",
                action="verify",
                blockers=[
                    {
                        "category": "primary-support",
                        "code": "primary-support-gap",
                        "message": "Thesis section needs primary support.",
                        "repairable": True,
                    }
                ],
                repair_decision={
                    "action": "repair",
                    "reason": "repairable-blockers-available",
                    "repair_iteration": 1,
                    "blocker_count": 1,
                },
                repair_iteration=0,
                terminal_reason="blocked-primary-support",
                thesis_repair_plan={
                    "kind": "thesis-repair-plan",
                    "eligible": True,
                    "target": TEST_THESIS_SECTION.as_posix(),
                    "suggested_action": "verify",
                    "suggested_command": f"launch-thesis verify {TEST_THESIS_SECTION.as_posix()}",
                    "safe_repair_actions": [],
                    "blocked_reasons": [],
                    "terminal_reason": None,
                    "readiness_claim": "none",
                },
            )

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Thesis repair plan:", stdout.getvalue())
            self.assertIn("launch-thesis verify", stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status", "--json"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            latest = payload["runtime"]["recent"][0]
            self.assertEqual(latest["repair_decision"]["action"], "repair")
            self.assertEqual(latest["thesis_repair_plan"]["suggested_action"], "verify")

    def test_work_status_cli_exposes_contract_gate_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            write_runtime_status_fixture(
                root / "output" / "telegram" / "runtime" / "runs" / "article-contract-gate-runtime",
                record_id="default:20260418-article-finalize",
                entity_kind="workflow-run",
                project_id="default",
                project_title=root.name,
                project_root=root,
                work_id=TEST_WORK_ID,
                work_title="Demo work",
                lane="article",
                action="finalize",
                contract_gates=[
                    {
                        "gate_id": "required-output:checklist",
                        "status": "block",
                        "reason": "Required artifact `checklist` is missing.",
                        "blocks_export": True,
                        "blocks_submission_ready": True,
                        "lane": "article",
                        "action": "finalize",
                        "artifact": str(root / TEST_ARTICLE_CHECKLIST),
                    }
                ],
            )

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Contract gates: blocks=1 warnings=0", stdout.getvalue())
            self.assertNotIn("[{", stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["work-status", "--json"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            latest = payload["runtime"]["recent"][0]
            self.assertEqual(latest["contract_gate_summary"]["block_count"], 1)
            self.assertTrue(any(item["category"] == "contract-gate" for item in payload["known_blockers"]))
            self.assertEqual(payload["suggested_next_action"]["action_id"], "article-repair")

    def test_autonomous_plan_cli_prints_policy_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["autonomous", "plan", "--mode", "autonomous-safe"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Autonomous plan:", stdout.getvalue())
            self.assertIn("launch-academic review", stdout.getvalue())
            self.assertIn("decision=allowed", stdout.getvalue())
            self.assertNotIn("submission-ready", stdout.getvalue())

    def test_autonomous_run_dry_run_writes_state_without_launching(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "run", "--mode", "autonomous-safe", "--max-steps", "2", "--dry-run"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Autonomous run: dry-run", stdout.getvalue())
            state_path = root / "output" / "telegram" / "runtime" / "autonomous" / "demo-work.json"
            self.assertTrue(state_path.exists())
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "dry-run")
            self.assertEqual(payload["mode"], "autonomous-safe")
            self.assertEqual(payload["readiness_claim"], "none")

    def test_autonomous_run_execute_stops_when_plan_has_no_allowed_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            orchestrator = WorkflowOrchestrator(root)
            orchestrator.store.set_active_run(
                {
                    "run_id": "default:active",
                    "run_dir": str(root / "output" / "telegram" / "runs" / "active"),
                    "pid": os.getpid(),
                    "lane": "article",
                    "action": "review",
                    "started_at": "2026-04-18T10:22:00+00:00",
                    "project_root": str(root),
                    "work_id": TEST_WORK_ID,
                    "target": TEST_ARTICLE_DRAFT.as_posix(),
                }
            )

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "run", "--mode", "autonomous-safe", "--execute"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Autonomous run: stopped", stdout.getvalue())
            state_path = root / "output" / "telegram" / "runtime" / "autonomous" / "demo-work.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "stopped")
            self.assertEqual(payload["stop_reason"], "A workflow run is already active for this work.")
            self.assertEqual(payload["readiness_claim"], "none")

    def test_autonomous_full_run_executes_export_after_finalization_check(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            write_file(root / TEST_WORK_ROOT / "articles" / "reviews" / "demo.md", "# Review\n")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["autonomous", "run", "--mode", "autonomous-full", "--max-steps", "1", "--execute"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Autonomous run: completed", stdout.getvalue())
            docx_path = root / "output" / "docx" / "demo-work" / "articles" / "demo.docx"
            self.assertTrue(docx_path.exists())
            state_path = root / "output" / "telegram" / "runtime" / "autonomous" / "demo-work.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["readiness_claim"], "none")
            self.assertEqual(payload["executed_steps"][0]["status"], "completed")
            self.assertIn("export-article-docx", payload["executed_steps"][0]["command"])

    def test_project_add_command_creates_registry_and_prints_result(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            bot_home = workspace / "bot-home"
            bot_home.mkdir(parents=True, exist_ok=True)
            repo = workspace / "repo"
            build_fake_repo(repo)
            stdout = StringIO()
            stderr = StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "--root",
                        str(bot_home),
                        "project",
                        "add",
                        "--title",
                        "Диплом по биометрии",
                        "--root",
                        str(repo),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Проект добавлен", stdout.getvalue())
            self.assertIn("ID: diplom-po-biometrii", stdout.getvalue())
            payload = json.loads((bot_home / "output" / "telegram" / "projects.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["projects"][0]["id"], "diplom-po-biometrii")

    def test_project_add_command_reports_existing_root(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            bot_home = workspace / "bot-home"
            bot_home.mkdir(parents=True, exist_ok=True)
            repo = workspace / "repo"
            build_fake_repo(repo)
            write_projects_registry(
                bot_home,
                [
                    {
                        "id": "alpha",
                        "title": "Диплом А",
                        "root_dir": str(repo),
                        "capabilities": ["thesis", "article"],
                    }
                ],
            )
            stdout = StringIO()
            stderr = StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "--root",
                        str(bot_home),
                        "project",
                        "add",
                        "--title",
                        "Новое имя",
                        "--root",
                        str(repo),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Проект уже есть в реестре", stdout.getvalue())
            self.assertIn("ID: alpha", stdout.getvalue())

    def test_project_add_command_fails_for_invalid_root(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            bot_home = workspace / "bot-home"
            bot_home.mkdir(parents=True, exist_ok=True)
            repo = workspace / "broken"
            repo.mkdir(parents=True, exist_ok=True)
            stdout = StringIO()
            stderr = StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "--root",
                        str(bot_home),
                        "project",
                        "add",
                        "--title",
                        "Сломанный проект",
                        "--root",
                        str(repo),
                    ]
                )

            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("Этот проект пока нельзя добавить", stderr.getvalue())

    def test_service_install_creates_env_template_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            bot_home = Path(tempdir)
            build_fake_launchd_files(bot_home)
            manager = LaunchdServiceManager(
                bot_home,
                home_dir=bot_home / "home",
                command_runner=FakeLaunchctl(),
            )
            stdout = StringIO()
            stderr = StringIO()

            with patch("telegram_console.bot.LaunchdServiceManager", return_value=manager):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(["--root", str(bot_home), "service", "install"])

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue((bot_home / "output" / "telegram" / ".env.launchd").exists())
            self.assertIn("Шаблон env-файла создан", stdout.getvalue())

    def test_service_status_uses_launchd_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            bot_home = Path(tempdir)
            build_fake_launchd_files(bot_home)
            env_file = bot_home / "output" / "telegram" / ".env.launchd"
            write_file(
                env_file,
                "TELEGRAM_BOT_TOKEN=test\nTELEGRAM_ALLOWED_CHAT_ID=1\n",
            )
            fake_launchctl = FakeLaunchctl()
            manager = LaunchdServiceManager(bot_home, home_dir=bot_home / "home", command_runner=fake_launchctl)
            manager.install()
            stdout = StringIO()
            stderr = StringIO()

            with patch("telegram_console.bot.LaunchdServiceManager", return_value=manager):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(["--root", str(bot_home), "service", "status"])

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Загружен в launchd: да", stdout.getvalue())
            self.assertIn("Env готов: да", stdout.getvalue())

    def test_runtime_commands_are_project_aware_and_show_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            bot_home = workspace / "bot-home"
            bot_home.mkdir(parents=True, exist_ok=True)
            repo_a = workspace / "alpha"
            repo_b = workspace / "beta"
            build_fake_repo(repo_a)
            build_fake_repo(repo_b)
            write_projects_registry(
                bot_home,
                [
                    {
                        "id": "alpha",
                        "title": "Диплом А",
                        "root_dir": str(repo_a),
                        "capabilities": ["thesis", "article"],
                    },
                    {
                        "id": "beta",
                        "title": "Диплом Б",
                        "root_dir": str(repo_b),
                        "capabilities": ["thesis"],
                    },
                ],
            )

            workflow_dir = bot_home / "output" / "telegram" / "runtime" / "runs" / "20260418-100000-alpha-thesis-verify"
            workflow_request = workflow_dir / "request.json"
            workflow_result = workflow_dir / "result.json"
            workflow_log = workflow_dir / "launcher.log"
            workflow_manifest = repo_a / "output" / "runs" / TEST_WORK_ID / "thesis" / "20260418-verify.meta.json"
            workflow_trace = repo_a / "output" / "runs" / TEST_WORK_ID / "thesis" / "20260418-verify.md"
            workflow_resolution = workflow_dir / "resolution.json"
            write_runtime_status_fixture(
                workflow_dir,
                record_id="alpha:20260418-thesis-verify",
                entity_kind="workflow-run",
                project_id="alpha",
                project_title="Диплом А",
                project_root=repo_a,
                work_id=TEST_WORK_ID,
                work_title="Demo work",
                lane="thesis",
                action="verify",
                attachments={
                    "request": str(workflow_request),
                    "result": str(workflow_result),
                    "log": str(workflow_log),
                    "manifest": str(workflow_manifest),
                    "trace": str(workflow_trace),
                    "resolution": str(workflow_resolution),
                },
                summary="Workflow verification completed.",
                contract_gates=[
                    {
                        "gate_id": "required-output:target-file",
                        "status": "block",
                        "reason": "Target file is missing.",
                        "blocks_export": True,
                        "blocks_submission_ready": True,
                        "lane": "thesis",
                        "action": "verify",
                    }
                ],
            )
            workflow_resolution.write_text(
                json.dumps(
                    {
                        "target_resolution": {
                            "normalized_path": TEST_THESIS_SECTION.as_posix(),
                            "resolution_mode": "legacy-root",
                            "work_source": "default",
                            "used_legacy_root_mapping": True,
                            "warning_code": "legacy-root-target",
                            "warning_message": (
                                "Legacy target path `manuscript/sections/01-introduction.md` "
                                f"resolved to `{TEST_THESIS_SECTION.as_posix()}`."
                            ),
                        },
                        "thesis_runtime": {
                            "summary_block": {
                                "kind": "thesis-section-summary",
                                "target": TEST_THESIS_SECTION.as_posix(),
                                "review_present": True,
                                "last_run_action": "verify",
                                "last_run_status": "success",
                                "blocker_count": 0,
                                "terminal_reason": None,
                                "suggested_next_action": "review-section",
                            }
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            chat_dir = bot_home / "output" / "telegram" / "runtime" / "agent_tasks" / "20260418-101500-beta-chat"
            chat_request = chat_dir / "request.json"
            chat_result = chat_dir / "result.json"
            chat_response = chat_dir / "assistant.txt"
            chat_stdout = chat_dir / "codex.stdout.jsonl"
            chat_stderr = chat_dir / "codex.stderr.log"
            write_runtime_status_fixture(
                chat_dir,
                record_id="beta:20260418-chat",
                entity_kind="chat-turn",
                project_id="beta",
                project_title="Диплом Б",
                project_root=repo_b,
                work_id=TEST_WORK_ID,
                work_title="Demo work",
                profile="execute",
                action="chat",
                attachments={
                    "request": str(chat_request),
                    "result": str(chat_result),
                    "response": str(chat_response),
                    "stdout": str(chat_stdout),
                    "stderr": str(chat_stderr),
                },
                summary="Chat turn completed.",
            )

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--root", str(bot_home), "runtime", "status", "--project", "alpha"])
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("alpha:20260418-thesis-verify", stdout.getvalue())
            self.assertIn("Lane summary:", stdout.getvalue())
            self.assertIn("gates=1/0", stdout.getvalue())
            self.assertIn("Resolution warning:", stdout.getvalue())
            self.assertNotIn("beta:20260418-chat", stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--root", str(bot_home), "runtime", "status", "--kind", "chat", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(len(payload["records"]), 1)
            self.assertEqual(payload["records"][0]["record_id"], "beta:20260418-chat")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--root", str(bot_home), "runtime", "show", "alpha:20260418-thesis-verify"])
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("workflow-run", stdout.getvalue())
            self.assertIn("next=review-section", stdout.getvalue())
            self.assertIn("Contract gates: blocks=1 warnings=0", stdout.getvalue())
            self.assertIn("Resolution warning:", stdout.getvalue())
            self.assertIn(TEST_THESIS_SECTION.as_posix(), stdout.getvalue())
            self.assertIn(str(workflow_manifest), stdout.getvalue())
            self.assertIn(str(workflow_trace), stdout.getvalue())

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "--root",
                        str(bot_home),
                        "runtime",
                        "path",
                        "beta:20260418-chat",
                        "response",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(stdout.getvalue().strip(), str(chat_response))


class LaunchdServiceManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.bot_home = Path(self.tempdir.name) / "bot-home"
        self.bot_home.mkdir(parents=True, exist_ok=True)
        build_fake_launchd_files(self.bot_home)
        self.home_dir = Path(self.tempdir.name) / "home"
        self.fake_launchctl = FakeLaunchctl()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def build_manager(self) -> LaunchdServiceManager:
        return LaunchdServiceManager(
            self.bot_home,
            home_dir=self.home_dir,
            command_runner=self.fake_launchctl,
        )

    def write_env(self) -> Path:
        env_file = self.bot_home / "output" / "telegram" / ".env.launchd"
        write_file(
            env_file,
            "TELEGRAM_BOT_TOKEN=test-token\nTELEGRAM_ALLOWED_CHAT_ID=1\nCODEX_MODEL=gpt-test\n",
        )
        return env_file

    def test_install_creates_env_template_without_bootstrapping(self) -> None:
        manager = self.build_manager()

        result = manager.install()

        self.assertTrue(result.env_template_created)
        self.assertFalse(result.installed)
        self.assertTrue(manager.paths.env_file.exists())
        self.assertEqual(self.fake_launchctl.commands, [])
        self.assertIn("TELEGRAM_BOT_TOKEN=", manager.paths.env_file.read_text(encoding="utf-8"))

    def test_install_with_env_renders_plist_and_bootstraps_agent(self) -> None:
        manager = self.build_manager()
        self.write_env()

        result = manager.install()

        self.assertTrue(result.installed)
        self.assertTrue(manager.paths.installed_plist.exists())
        plist_text = manager.paths.installed_plist.read_text(encoding="utf-8")
        self.assertIn(DEFAULT_SERVICE_LABEL, plist_text)
        self.assertIn(str(manager.paths.wrapper_script), plist_text)
        joined = [" ".join(command) for command in self.fake_launchctl.commands]
        self.assertTrue(any("launchctl bootstrap" in item for item in joined))
        self.assertTrue(any("launchctl kickstart -k" in item for item in joined))

    def test_status_reports_loaded_agent_and_pid(self) -> None:
        manager = self.build_manager()
        self.write_env()
        manager.install()

        status = manager.status()

        self.assertTrue(status.installed)
        self.assertTrue(status.loaded)
        self.assertEqual(status.pid, self.fake_launchctl.pid)
        self.assertTrue(status.env_configured)

    def test_restart_uses_kickstart_when_agent_is_loaded(self) -> None:
        manager = self.build_manager()
        self.write_env()
        manager.install()
        self.fake_launchctl.commands.clear()

        status = manager.restart()

        self.assertTrue(status.loaded)
        self.assertTrue(any(command[:3] == ["launchctl", "kickstart", "-k"] for command in self.fake_launchctl.commands))

    def test_uninstall_removes_plist_but_keeps_env(self) -> None:
        manager = self.build_manager()
        env_file = self.write_env()
        manager.install()

        status = manager.uninstall()

        self.assertFalse(manager.paths.installed_plist.exists())
        self.assertTrue(env_file.exists())
        self.assertFalse(status.installed)


class TelegramApiTests(unittest.TestCase):
    def test_timeout_is_wrapped_into_telegram_api_error(self) -> None:
        api = TelegramBotApi("test-token")
        with patch("telegram_console.telegram_api.request.urlopen", side_effect=TimeoutError("boom")):
            with self.assertRaisesRegex(TelegramApiError, "timeout"):
                api.get_updates(timeout=1)


if __name__ == "__main__":
    unittest.main()
