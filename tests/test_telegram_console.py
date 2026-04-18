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
from telegram_console.bot import MAIN_MENU, TelegramConsoleBot, main
from telegram_console.config import TelegramConsoleConfig
from telegram_console.email_delivery import EmailDeliveryError, SmtpDocxSender, SmtpSettings
from telegram_console.launchd_service import DEFAULT_SERVICE_LABEL, LaunchdServiceManager
from telegram_console.orchestrator import RunBusyError, WorkflowOrchestrator
from telegram_console.prompting import PROFILE_EXPECTATIONS, PROFILE_LABELS, PromptBuilder
from telegram_console.projects import ProjectService
from telegram_console.standards import load_standards_registry, resolve_standard_profile
from telegram_console.telegram_api import TelegramApiError, TelegramBotApi
from telegram_console import work_cli as work_cli_module
from telegram_console.workspace import load_work_config, load_workspace_config


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
        self.assertEqual(article_status["kind"], "article-overview")
        self.assertEqual(article_status["bundles"], [])

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
