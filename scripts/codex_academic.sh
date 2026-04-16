#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_BIN="${CODEX_BIN:-codex}"
CODEX_MODEL="${CODEX_MODEL:-}"
THESIS_LAUNCHER="$ROOT_DIR/scripts/codex_thesis.sh"
ARTICLE_RUNS_DIR="$ROOT_DIR/articles/runs"
PROFILES_DIR="$ROOT_DIR/meta/standards/normalized"
RAW_STANDARDS_DIR="$ROOT_DIR/meta/standards/raw"
ARTICLE_DOCX_DIR="$ROOT_DIR/output/docx/articles"

mkdir -p "$ARTICLE_RUNS_DIR" "$ARTICLE_DOCX_DIR"

print_usage() {
  cat <<'EOF'
Usage:
  bash scripts/codex_academic.sh <command> [options]

Commands:
  article         Run the full legal-academic article workflow.
  review          Run article evaluator review on a draft or final article.
  repair          Repair an article bundle from a review, draft, or final file.
  thesis          Proxy to scripts/codex_thesis.sh for thesis-specific workflows.
  help            Show this help.

Article examples:
  bash scripts/codex_academic.sh article --topic "Конституционные пределы биометрической идентификации"
  bash scripts/codex_academic.sh article --brief articles/briefs/biometrics.md
  bash scripts/codex_academic.sh article --topic "..." --profile ru-law-article-v1 --dry-run

Review and repair examples:
  bash scripts/codex_academic.sh review articles/drafts/biometrics.md
  bash scripts/codex_academic.sh repair articles/reviews/biometrics.md

Defaults:
  article, review, and repair enable web search by default.
  profile defaults to ru-law-article-v1.
  article outputs are expected in articles/* plus output/docx/articles/.

Common options:
  --notes <file-or-text>
  --profile <profile-id>
  --dry-run
  --search | --no-search
  --model <model>
EOF
}

resolve_path() {
  local raw="$1"
  python3 - "$ROOT_DIR" "$raw" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
raw = Path(sys.argv[2]).expanduser()
path = raw if raw.is_absolute() else (root / raw)
print(path.resolve())
PY
}

path_relative_to_root() {
  local raw="$1"
  python3 - "$ROOT_DIR" "$raw" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
path = Path(sys.argv[2]).resolve()

try:
    rel = path.relative_to(root)
except ValueError:
    raise SystemExit(1)

print(rel.as_posix())
PY
}

read_notes() {
  local raw="${1:-}"
  local root_relative=""

  if [[ -z "$raw" ]]; then
    printf 'None provided.\n'
    return 0
  fi

  if [[ -f "$raw" ]]; then
    cat "$raw"
    return 0
  fi

  root_relative="$ROOT_DIR/$raw"
  if [[ -f "$root_relative" ]]; then
    cat "$root_relative"
    return 0
  fi

  printf '%s\n' "$raw"
}

default_search_for_command() {
  case "$1" in
    article|review|repair)
      printf 'yes\n'
      ;;
    *)
      printf 'Unknown command: %s\n' "$1" >&2
      return 1
      ;;
  esac
}

slugify_text() {
  local raw="$1"
  python3 - "$raw" <<'PY'
import re
import sys

raw = sys.argv[1].strip().lower()
slug = re.sub(r"[^\w]+", "-", raw, flags=re.UNICODE).strip("-_")
slug = re.sub(r"-{2,}", "-", slug)
print((slug[:80] or "article-topic"))
PY
}

ensure_profile_file() {
  local profile_id="$1"
  local profile_path="$PROFILES_DIR/${profile_id}.md"

  if [[ ! -f "$profile_path" ]]; then
    printf 'Unknown academic profile: %s\nExpected file: %s\n' "$profile_id" "$profile_path" >&2
    return 1
  fi

  printf '%s\n' "$profile_path"
}

assert_review_target() {
  local target_rel="$1"

  case "$target_rel" in
    articles/drafts/*.md|articles/final/*.md)
      ;;
    *)
      printf 'review expects a target in articles/drafts/ or articles/final/.\n' >&2
      return 1
      ;;
  esac
}

assert_repair_target() {
  local target_rel="$1"

  case "$target_rel" in
    articles/drafts/*.md|articles/final/*.md|articles/reviews/*.md)
      ;;
    *)
      printf 'repair expects a target in articles/drafts/, articles/final/, or articles/reviews/.\n' >&2
      return 1
      ;;
  esac
}

derive_slug_from_path() {
  local target_rel="$1"
  local base_name=""

  base_name="$(basename "$target_rel" .md)"
  base_name="${base_name%-checklist}"
  slugify_text "$base_name"
}

set_bundle_paths() {
  local slug="$1"

  ARTICLE_SLUG="$slug"
  ARTICLE_BRIEF_PATH="$ROOT_DIR/articles/briefs/${slug}.md"
  ARTICLE_EVIDENCE_PATH="$ROOT_DIR/articles/evidence/${slug}.md"
  ARTICLE_CLAIM_MAP_PATH="$ROOT_DIR/articles/claim-maps/${slug}.md"
  ARTICLE_DRAFT_PATH="$ROOT_DIR/articles/drafts/${slug}.md"
  ARTICLE_REVIEW_PATH="$ROOT_DIR/articles/reviews/${slug}.md"
  ARTICLE_FINAL_PATH="$ROOT_DIR/articles/final/${slug}.md"
  ARTICLE_CHECKLIST_PATH="$ROOT_DIR/articles/final/${slug}-checklist.md"
  ARTICLE_DOCX_PATH="$ARTICLE_DOCX_DIR/${slug}.docx"
}

bundle_paths_block() {
  cat <<EOF
- Brief: $ARTICLE_BRIEF_PATH
- Evidence pack: $ARTICLE_EVIDENCE_PATH
- Claim map: $ARTICLE_CLAIM_MAP_PATH
- Draft: $ARTICLE_DRAFT_PATH
- Review: $ARTICLE_REVIEW_PATH
- Final markdown: $ARTICLE_FINAL_PATH
- Final checklist: $ARTICLE_CHECKLIST_PATH
- Expected DOCX: $ARTICLE_DOCX_PATH
EOF
}

add_context_path() {
  local path="$1"

  if [[ -z "$path" || ! -e "$path" ]]; then
    return 0
  fi

  local existing
  for existing in "${RELATED_CONTEXT[@]:-}"; do
    if [[ "$existing" == "$path" ]]; then
      return 0
    fi
  done

  RELATED_CONTEXT+=("$path")
}

collect_article_context() {
  local command="$1"
  local profile_path="$2"
  local input_brief_path="${3:-}"
  local target_path="${4:-}"

  RELATED_CONTEXT=()

  add_context_path "$ROOT_DIR/README.md"
  add_context_path "$ROOT_DIR/AGENTS.md"
  add_context_path "$ROOT_DIR/meta/master-protocol.md"
  add_context_path "$ROOT_DIR/meta/standards/README.md"
  add_context_path "$ROOT_DIR/meta/standards/raw/README.md"
  add_context_path "$profile_path"
  add_context_path "$ROOT_DIR/templates/article-brief.md"
  add_context_path "$ROOT_DIR/templates/evidence-pack.md"
  add_context_path "$ROOT_DIR/templates/claim-map.md"
  add_context_path "$ROOT_DIR/templates/article-review-sheet.md"
  add_context_path "$ROOT_DIR/templates/submission-checklist.md"
  add_context_path "$ROOT_DIR/articles/README.md"
  add_context_path "$ROOT_DIR/sources/00-working-materials-map.md"

  if [[ -n "$input_brief_path" ]]; then
    add_context_path "$input_brief_path"
  fi

  if [[ -n "$target_path" ]]; then
    add_context_path "$target_path"
  fi

  add_context_path "$ARTICLE_BRIEF_PATH"
  add_context_path "$ARTICLE_EVIDENCE_PATH"
  add_context_path "$ARTICLE_CLAIM_MAP_PATH"
  add_context_path "$ARTICLE_DRAFT_PATH"
  add_context_path "$ARTICLE_REVIEW_PATH"
  add_context_path "$ARTICLE_FINAL_PATH"
  add_context_path "$ARTICLE_CHECKLIST_PATH"

  if [[ "$command" == "article" ]]; then
    add_context_path "$ROOT_DIR/meta/project-canon.md"
  fi
}

format_paths_block() {
  if [[ $# -eq 0 ]]; then
    printf '%s\n' '- none detected'
    return 0
  fi

  local item
  for item in "$@"; do
    printf -- '- %s\n' "$item"
  done
}

write_manifest() {
  local manifest_path="$1"
  local timestamp="$2"
  local command="$3"
  local profile_id="$4"
  local use_search="$5"
  local topic="$6"
  local input_brief="$7"
  local target_path="$8"
  local output_file="$9"
  shift 9

  python3 - "$manifest_path" "$timestamp" "$command" "$profile_id" "$use_search" "$topic" "$input_brief" "$target_path" "$output_file" "$ROOT_DIR" "$ARTICLE_SLUG" "$ARTICLE_BRIEF_PATH" "$ARTICLE_EVIDENCE_PATH" "$ARTICLE_CLAIM_MAP_PATH" "$ARTICLE_DRAFT_PATH" "$ARTICLE_REVIEW_PATH" "$ARTICLE_FINAL_PATH" "$ARTICLE_CHECKLIST_PATH" "$ARTICLE_DOCX_PATH" "$@" <<'PY'
from pathlib import Path
import json
import sys

manifest_path = Path(sys.argv[1])
timestamp = sys.argv[2]
command = sys.argv[3]
profile_id = sys.argv[4]
use_search = sys.argv[5] == "yes"
topic = sys.argv[6] or None
input_brief = sys.argv[7] or None
target_path = sys.argv[8] or None
output_file = sys.argv[9]
root_dir = sys.argv[10]
slug = sys.argv[11]
brief_path = sys.argv[12]
evidence_path = sys.argv[13]
claim_map_path = sys.argv[14]
draft_path = sys.argv[15]
review_path = sys.argv[16]
final_path = sys.argv[17]
checklist_path = sys.argv[18]
docx_path = sys.argv[19]
related_context = sys.argv[20:]

manifest = {
    "timestamp": timestamp,
    "command": command,
    "profile_id": profile_id,
    "search_enabled": use_search,
    "topic": topic,
    "input_brief": input_brief,
    "target_path": target_path,
    "root_dir": root_dir,
    "output_file": output_file,
    "bundle": {
        "slug": slug,
        "brief": brief_path,
        "evidence_pack": evidence_path,
        "claim_map": claim_map_path,
        "draft": draft_path,
        "review": review_path,
        "final_markdown": final_path,
        "checklist": checklist_path,
        "docx": docx_path,
    },
    "related_context": related_context,
}

manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

run_codex() {
  local command="$1"
  local profile_id="$2"
  local use_search="$3"
  local topic="$4"
  local input_brief="$5"
  local target_path="$6"
  local prompt="$7"
  shift 7
  local timestamp out_file manifest_file
  local -a cmd

  timestamp="$(date '+%Y%m%d-%H%M%S')"
  out_file="$ARTICLE_RUNS_DIR/${timestamp}-${command}-${ARTICLE_SLUG}.md"
  manifest_file="$ARTICLE_RUNS_DIR/${timestamp}-${command}-${ARTICLE_SLUG}.meta.json"

  cmd=("$CODEX_BIN")
  if [[ "$use_search" == "yes" ]]; then
    cmd+=(--search)
  fi
  cmd+=(exec -C "$ROOT_DIR" --skip-git-repo-check --full-auto -o "$out_file")
  if [[ -n "$CODEX_MODEL" ]]; then
    cmd+=(-m "$CODEX_MODEL")
  fi

  printf '%s\n' "$prompt" | "${cmd[@]}" -

  write_manifest \
    "$manifest_file" \
    "$timestamp" \
    "$command" \
    "$profile_id" \
    "$use_search" \
    "$topic" \
    "$input_brief" \
    "$target_path" \
    "$out_file" \
    "${RELATED_CONTEXT[@]}"

  printf '\nSaved final message to %s\n' "$out_file"
  printf 'Saved run manifest to %s\n' "$manifest_file"
}

build_article_prompt() {
  local profile_id="$1"
  local profile_path="$2"
  local use_search="$3"
  local topic="$4"
  local input_brief_path="$5"
  local notes="$6"
  local nearby_context="$7"
  local search_state input_block

  if [[ "$use_search" == "yes" ]]; then
    search_state="enabled by launcher"
  else
    search_state="disabled by launcher"
  fi

  if [[ -n "$input_brief_path" ]]; then
    input_block="Input brief source: $input_brief_path"
  else
    input_block="Input topic: $topic"
  fi

  cat <<EOF
Use \$academic-workflow-orchestrator to run a full legal-academic article workflow in /Users/albina/дипломная.

Article lane:
- Work only inside articles/ and article-related outputs such as output/docx/articles/.
- Never write article artifacts into manuscript/sections/.

Execution context:
$input_block
Publication profile: $profile_id
Profile file: $profile_path
Web search: $search_state
Relevant raw standards directory: $RAW_STANDARDS_DIR

Managed article bundle paths:
$(bundle_paths_block)

Nearby context candidates:
$nearby_context

Workflow requirements:
- Open README.md, AGENTS.md, meta/master-protocol.md, the active profile, and the article templates before editing.
- Consult meta/project-canon.md only if the article clearly aligns with the thesis topic or reuses thesis-specific claims.
- Start with \$academic-intake and normalize the request into the managed brief path.
- Then use \$academic-source-acquirer, \$academic-source-verifier, and \$academic-evidence-cartographer before serious drafting.
- For law, case law, regulator guidance, and statistics, final authority must be official or primary.
- Secondary literature is interpretive support, not a substitute for primary verification.
- Proprietary legal databases and aggregators may be used only as navigational support.
- Build or update the evidence pack and claim map so each significant claim has an evidence trace or an explicit analytical status.
- Draft with \$academic-draft-writer only from verified support or clearly marked analytical conclusions.
- Run \$academic-citation-checker, \$academic-counterargument-critic, and \$academic-submission-evaluator before finalization.
- If blockers remain, use \$academic-repair-orchestrator and do not overstate readiness.
- Repair logic must stay finite. If strong primary gaps remain, downgrade to \`strong-draft-with-blockers\`.
- Finish with \$academic-finalizer: produce final Markdown, checklist, and DOCX via scripts/export_academic_docx.sh.
- If relevant official raw formatting standards are missing or conflicting, reflect that as a blocker in the checklist and do not overstate formal submission readiness.

Additional notes:
$notes

Deliverable:
- Update the managed article bundle directly.
- End with the explicit status \`submission-ready\`, \`strong-draft\`, or \`strong-draft-with-blockers\`.
- Summarize changed files, verification performed, exported outputs, and remaining blockers.
EOF
}

build_review_prompt() {
  local profile_id="$1"
  local profile_path="$2"
  local use_search="$3"
  local target_path="$4"
  local target_rel="$5"
  local notes="$6"
  local nearby_context="$7"
  local search_state

  if [[ "$use_search" == "yes" ]]; then
    search_state="enabled by launcher"
  else
    search_state="disabled by launcher"
  fi

  cat <<EOF
Use \$academic-submission-evaluator, \$academic-counterargument-critic, and \$academic-citation-checker to review this legal-academic article bundle in /Users/albina/дипломная.

Target file: $target_path
Target path (relative): $target_rel
Publication profile: $profile_id
Profile file: $profile_path
Web search: $search_state

Managed article bundle paths:
$(bundle_paths_block)

Nearby context candidates:
$nearby_context

Execution rules:
- Treat this as an article-lane review, not a thesis-section review.
- Review source integrity, primary support, dynamic materials, counterarguments, composition, citations, and checklist blockers.
- Use templates/article-review-sheet.md and update the review file exactly here: $ARTICLE_REVIEW_PATH
- Verify dynamic legal material against current official or primary sources when needed.
- Output a findings-first review with the verdict \`submission-ready\`, \`strong-draft\`, or \`strong-draft-with-blockers\`.
- Do not broadly rewrite the target file; only make tiny safe citation or factual fixes if they are obvious and necessary.

Additional notes:
$notes

Deliverable:
- Update or create $ARTICLE_REVIEW_PATH
- End with the key findings first, then the explicit verdict and next repair priorities.
EOF
}

build_repair_prompt() {
  local profile_id="$1"
  local profile_path="$2"
  local use_search="$3"
  local target_path="$4"
  local target_rel="$5"
  local notes="$6"
  local nearby_context="$7"
  local search_state

  if [[ "$use_search" == "yes" ]]; then
    search_state="enabled by launcher"
  else
    search_state="disabled by launcher"
  fi

  cat <<EOF
Use \$academic-repair-orchestrator, \$academic-source-verifier, \$academic-citation-checker, \$academic-submission-evaluator, and \$academic-finalizer to repair this legal-academic article bundle in /Users/albina/дипломная.

Repair input: $target_path
Repair input (relative): $target_rel
Publication profile: $profile_id
Profile file: $profile_path
Web search: $search_state

Managed article bundle paths:
$(bundle_paths_block)

Nearby context candidates:
$nearby_context

Execution rules:
- Prioritize primary-source blockers, unsupported claims, and missing caveats before style or polish.
- Use the companion review file if it exists: $ARTICLE_REVIEW_PATH
- Keep the repair inside article-lane artifacts only.
- Do not hide unresolved blockers behind nicer prose.
- Re-run evaluator logic before finalization.
- If relevant raw formatting standards are still missing or conflicting, preserve that blocker in the checklist.
- Finish by updating the active draft or final markdown, the checklist, and DOCX export when justified.
- If blockers remain after reasonable repair, keep or downgrade the status to \`strong-draft-with-blockers\`.

Additional notes:
$notes

Deliverable:
- Update the relevant article bundle files directly.
- End with the explicit post-repair status, changed files, and remaining blockers.
EOF
}

COMMAND="${1:-help}"
shift || true

case "$COMMAND" in
  help)
    print_usage
    exit 0
    ;;
  thesis)
    if [[ $# -eq 0 ]]; then
      bash "$THESIS_LAUNCHER" help
      exit 0
    fi
    bash "$THESIS_LAUNCHER" "$@"
    exit $?
    ;;
  article|review|repair)
    ;;
  *)
    printf 'Unknown command: %s\n' "$COMMAND" >&2
    print_usage >&2
    exit 1
    ;;
esac

PROFILE_ID="ru-law-article-v1"
USE_SEARCH="auto"
DRY_RUN="no"
NOTES_ARG=""
TOPIC=""
BRIEF_ARG=""
TARGET_ARG=""

case "$COMMAND" in
  article)
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --topic)
          if [[ $# -lt 2 ]]; then
            printf 'Option --topic requires a value.\n' >&2
            exit 1
          fi
          TOPIC="$2"
          shift 2
          ;;
        --brief)
          if [[ $# -lt 2 ]]; then
            printf 'Option --brief requires a value.\n' >&2
            exit 1
          fi
          BRIEF_ARG="$2"
          shift 2
          ;;
        --notes)
          if [[ $# -lt 2 ]]; then
            printf 'Option --notes requires a value.\n' >&2
            exit 1
          fi
          NOTES_ARG="$2"
          shift 2
          ;;
        --profile)
          if [[ $# -lt 2 ]]; then
            printf 'Option --profile requires a value.\n' >&2
            exit 1
          fi
          PROFILE_ID="$2"
          shift 2
          ;;
        --dry-run)
          DRY_RUN="yes"
          shift
          ;;
        --search)
          USE_SEARCH="yes"
          shift
          ;;
        --no-search)
          USE_SEARCH="no"
          shift
          ;;
        --model)
          if [[ $# -lt 2 ]]; then
            printf 'Option --model requires a value.\n' >&2
            exit 1
          fi
          CODEX_MODEL="$2"
          shift 2
          ;;
        *)
          printf 'Unknown option for article: %s\n' "$1" >&2
          exit 1
          ;;
      esac
    done

    if [[ -n "$TOPIC" && -n "$BRIEF_ARG" ]]; then
      printf 'Use either --topic or --brief, not both.\n' >&2
      exit 1
    fi

    if [[ -z "$TOPIC" && -z "$BRIEF_ARG" ]]; then
      printf 'article requires either --topic or --brief.\n' >&2
      exit 1
    fi
    ;;
  review|repair)
    TARGET_ARG="${1:-}"
    if [[ -z "$TARGET_ARG" ]]; then
      printf '%s requires a target file.\n' "$COMMAND" >&2
      exit 1
    fi
    shift || true

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --notes)
          if [[ $# -lt 2 ]]; then
            printf 'Option --notes requires a value.\n' >&2
            exit 1
          fi
          NOTES_ARG="$2"
          shift 2
          ;;
        --profile)
          if [[ $# -lt 2 ]]; then
            printf 'Option --profile requires a value.\n' >&2
            exit 1
          fi
          PROFILE_ID="$2"
          shift 2
          ;;
        --dry-run)
          DRY_RUN="yes"
          shift
          ;;
        --search)
          USE_SEARCH="yes"
          shift
          ;;
        --no-search)
          USE_SEARCH="no"
          shift
          ;;
        --model)
          if [[ $# -lt 2 ]]; then
            printf 'Option --model requires a value.\n' >&2
            exit 1
          fi
          CODEX_MODEL="$2"
          shift 2
          ;;
        *)
          printf 'Unknown option for %s: %s\n' "$COMMAND" "$1" >&2
          exit 1
          ;;
      esac
    done
    ;;
esac

PROFILE_PATH="$(ensure_profile_file "$PROFILE_ID")"
if [[ "$USE_SEARCH" == "auto" ]]; then
  USE_SEARCH="$(default_search_for_command "$COMMAND")"
fi

INPUT_BRIEF_PATH=""
TARGET_PATH=""
TARGET_REL=""

case "$COMMAND" in
  article)
    if [[ -n "$BRIEF_ARG" ]]; then
      INPUT_BRIEF_PATH="$(resolve_path "$BRIEF_ARG")"
      if [[ ! -f "$INPUT_BRIEF_PATH" ]]; then
        printf 'Article brief not found: %s\n' "$INPUT_BRIEF_PATH" >&2
        exit 1
      fi
      ARTICLE_SLUG="$(derive_slug_from_path "$(basename "$INPUT_BRIEF_PATH")")"
    else
      ARTICLE_SLUG="$(slugify_text "$TOPIC")"
    fi
    ;;
  review|repair)
    TARGET_PATH="$(resolve_path "$TARGET_ARG")"
    if [[ ! -f "$TARGET_PATH" ]]; then
      printf 'Target not found: %s\n' "$TARGET_PATH" >&2
      exit 1
    fi
    if ! TARGET_REL="$(path_relative_to_root "$TARGET_PATH")"; then
      printf 'Target must be inside %s\n' "$ROOT_DIR" >&2
      exit 1
    fi

    if [[ "$COMMAND" == "review" ]]; then
      assert_review_target "$TARGET_REL"
    else
      assert_repair_target "$TARGET_REL"
    fi

    ARTICLE_SLUG="$(derive_slug_from_path "$TARGET_REL")"
    ;;
esac

set_bundle_paths "$ARTICLE_SLUG"
collect_article_context "$COMMAND" "$PROFILE_PATH" "$INPUT_BRIEF_PATH" "$TARGET_PATH"
NEARBY_CONTEXT_BLOCK="$(format_paths_block "${RELATED_CONTEXT[@]}")"
NOTES_CONTENT="$(read_notes "$NOTES_ARG")"

case "$COMMAND" in
  article)
    PROMPT="$(build_article_prompt "$PROFILE_ID" "$PROFILE_PATH" "$USE_SEARCH" "$TOPIC" "$INPUT_BRIEF_PATH" "$NOTES_CONTENT" "$NEARBY_CONTEXT_BLOCK")"
    ;;
  review)
    PROMPT="$(build_review_prompt "$PROFILE_ID" "$PROFILE_PATH" "$USE_SEARCH" "$TARGET_PATH" "$TARGET_REL" "$NOTES_CONTENT" "$NEARBY_CONTEXT_BLOCK")"
    ;;
  repair)
    PROMPT="$(build_repair_prompt "$PROFILE_ID" "$PROFILE_PATH" "$USE_SEARCH" "$TARGET_PATH" "$TARGET_REL" "$NOTES_CONTENT" "$NEARBY_CONTEXT_BLOCK")"
    ;;
esac

if [[ "$DRY_RUN" == "yes" ]]; then
  printf 'Command: %s\n' "$COMMAND"
  printf 'Profile: %s\n' "$PROFILE_ID"
  printf 'Search enabled: %s\n' "$USE_SEARCH"
  if [[ -n "$TOPIC" ]]; then
    printf 'Topic: %s\n' "$TOPIC"
  fi
  if [[ -n "$INPUT_BRIEF_PATH" ]]; then
    printf 'Input brief: %s\n' "$INPUT_BRIEF_PATH"
  fi
  if [[ -n "$TARGET_PATH" ]]; then
    printf 'Target: %s\n' "$TARGET_PATH"
    printf 'Target (relative): %s\n' "$TARGET_REL"
  fi
  printf 'Article slug: %s\n' "$ARTICLE_SLUG"
  if [[ -n "$CODEX_MODEL" ]]; then
    printf 'Model: %s\n' "$CODEX_MODEL"
  fi
  printf 'Managed bundle paths:\n%s\n' "$(bundle_paths_block)"
  printf 'Related context:\n%s\n' "$NEARBY_CONTEXT_BLOCK"
  printf '\n%s\n' "$PROMPT"
  exit 0
fi

run_codex \
  "$COMMAND" \
  "$PROFILE_ID" \
  "$USE_SEARCH" \
  "$TOPIC" \
  "$INPUT_BRIEF_PATH" \
  "$TARGET_PATH" \
  "$PROMPT" \
  "${RELATED_CONTEXT[@]}"
