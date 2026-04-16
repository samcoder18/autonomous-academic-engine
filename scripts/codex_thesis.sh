#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/output/codex"
CODEX_BIN="${CODEX_BIN:-codex}"
CODEX_MODEL="${CODEX_MODEL:-}"

mkdir -p "$OUTPUT_DIR"

print_usage() {
  cat <<'EOF'
Usage:
  bash scripts/codex_thesis.sh <preset> <target> [--notes <file-or-text>] [--dry-run] [--search|--no-search] [--model <model>]

Presets:
  full-cycle      Run the full thesis workflow on the target artifact.
  source-pack     Build or refresh a source package in sources/.
  verify          Verify claims, dates, and source support for the target file.
  write-section   Draft or expand a manuscript section from the approved workflow.
  review-section  Produce or refresh a review artifact in reviews/ for a section.
  style-pass      Refine checked prose for natural academic style.
  help            Show this help.

Examples:
  bash scripts/codex_thesis.sh full-cycle manuscript/sections/03-chapter-2.md
  bash scripts/codex_thesis.sh source-pack sources/02-chapter-2-regulation.md --notes "Собери пакет по ЕБС и практике 2025-2026"
  bash scripts/codex_thesis.sh verify manuscript/sections/03-chapter-2.md --notes "Особенно проверь 152-ФЗ, 572-ФЗ и материалы Банка России"
  bash scripts/codex_thesis.sh write-section manuscript/sections/04-chapter-3.md --notes chapters/03-chapter-3-brief.md
  bash scripts/codex_thesis.sh review-section manuscript/sections/02-chapter-1.md
  bash scripts/codex_thesis.sh style-pass manuscript/sections/02-chapter-1.md --notes "Не менять смысл выводов, только ритм и естественность"

Search defaults:
  full-cycle, source-pack, verify, and write-section enable search by default.
  review-section and style-pass keep search off unless --search is passed explicitly.

Environment overrides:
  CODEX_BIN       Codex executable to use (default: codex)
  CODEX_MODEL     Model passed to codex exec
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

default_search_for_preset() {
  case "$1" in
    full-cycle|source-pack|verify|write-section)
      printf 'yes\n'
      ;;
    review-section|style-pass)
      printf 'no\n'
      ;;
    *)
      printf 'Unknown preset: %s\n' "$1" >&2
      return 1
      ;;
  esac
}

assert_target_matches_preset() {
  local preset="$1"
  local target_rel="$2"

  case "$preset" in
    full-cycle)
      case "$target_rel" in
        chapters/*.md|sources/*.md|manuscript/sections/*.md|reviews/*.md)
          ;;
        *)
          printf 'Preset %s expects a target in chapters/, sources/, manuscript/sections/, or reviews/.\n' "$preset" >&2
          return 1
          ;;
      esac
      ;;
    source-pack)
      case "$target_rel" in
        sources/*.md)
          ;;
        *)
          printf 'Preset %s expects a Markdown file in sources/.\n' "$preset" >&2
          return 1
          ;;
      esac
      ;;
    verify)
      case "$target_rel" in
        chapters/*.md|sources/*.md|manuscript/sections/*.md|reviews/*.md)
          ;;
        *)
          printf 'Preset %s expects a Markdown file in chapters/, sources/, manuscript/sections/, or reviews/.\n' "$preset" >&2
          return 1
          ;;
      esac
      ;;
    write-section|review-section|style-pass)
      case "$target_rel" in
        manuscript/sections/*.md)
          ;;
        *)
          printf 'Preset %s expects a manuscript section inside manuscript/sections/.\n' "$preset" >&2
          return 1
          ;;
      esac
      ;;
    *)
      printf 'Unknown preset: %s\n' "$preset" >&2
      return 1
      ;;
  esac

  if [[ "$target_rel" == manuscript/full-draft.md ]]; then
    printf 'Use manuscript/sections/ as the editable target, not manuscript/full-draft.md.\n' >&2
    return 1
  fi
}

derive_review_path() {
  local target_rel="$1"
  local base_name=""

  case "$target_rel" in
    manuscript/sections/*.md)
      base_name="$(basename "$target_rel" .md)"
      printf '%s/reviews/%s-review.md\n' "$ROOT_DIR" "$base_name"
      ;;
  esac
}

derive_sync_hint_path() {
  local preset="$1"
  local target_rel="$2"
  local base_name=""

  base_name="$(basename "$target_rel" .md)"
  printf '%s/sync/%s-%s-%s.md\n' "$ROOT_DIR" "$(date '+%Y%m%d')" "$preset" "$base_name"
}

collect_related_paths() {
  local target_path="$1"
  python3 - "$ROOT_DIR" "$target_path" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
search_dirs = [
    root / "chapters",
    root / "sources",
    root / "manuscript" / "sections",
    root / "reviews",
]

candidates = []
seen = set()
target_stem = target.stem.lower()
keywords = set()

match = re.search(r"(chapter-\d+)", target_stem)
if match:
    keywords.add(match.group(1))

for token in ("introduction", "conclusion", "bibliography", "title"):
    if token in target_stem:
        keywords.add(token)

for token in re.split(r"[^a-z0-9]+", target_stem):
    if token and token not in {
        "0", "1", "2", "3", "4", "5", "6",
        "00", "01", "02", "03", "04", "05", "06",
        "chapter", "section", "sections", "brief", "review",
    }:
        keywords.add(token)

def add(path: Path) -> None:
    resolved = path.resolve()
    if resolved == target or not resolved.exists() or resolved.suffix.lower() != ".md":
        return
    key = str(resolved)
    if key in seen:
        return
    seen.add(key)
    candidates.append(key)

for directory in search_dirs:
    if not directory.exists():
        continue
    for path in sorted(directory.glob("*.md")):
        stem = path.stem.lower()
        if stem == target_stem or target_stem in stem or stem in target_stem:
            add(path)
            continue
        if any(keyword in stem for keyword in keywords):
            add(path)

target_parts = set(target.parts)
if "sections" in target_parts or "chapters" in target_parts:
    for extra in (
        root / "chapters" / "00-thesis-architecture.md",
        root / "sources" / "00-working-materials-map.md",
    ):
        if extra.exists():
            add(extra)

for path in candidates:
    print(path)
PY
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
  local preset="$3"
  local target_path="$4"
  local target_rel="$5"
  local target_state="$6"
  local use_search="$7"
  local output_file="$8"
  local expected_review_path="$9"
  local sync_hint_path="${10}"
  shift 10

  python3 - "$manifest_path" "$timestamp" "$preset" "$target_path" "$target_rel" "$target_state" "$use_search" "$CODEX_MODEL" "$output_file" "$expected_review_path" "$sync_hint_path" "$ROOT_DIR" "$@" <<'PY'
from pathlib import Path
import json
import sys

manifest_path = Path(sys.argv[1])
timestamp = sys.argv[2]
preset = sys.argv[3]
target_path = sys.argv[4]
target_rel = sys.argv[5]
target_state = sys.argv[6]
use_search = sys.argv[7] == "yes"
model = sys.argv[8] or None
output_file = sys.argv[9]
expected_review_path = sys.argv[10] or None
sync_hint_path = sys.argv[11] or None
root_dir = sys.argv[12]
related_paths = sys.argv[13:]

manifest = {
    "timestamp": timestamp,
    "preset": preset,
    "target": {
        "absolute": target_path,
        "relative": target_rel,
        "state": target_state,
    },
    "search_enabled": use_search,
    "model": model,
    "root_dir": root_dir,
    "output_file": output_file,
    "expected_review_file": expected_review_path,
    "sync_hint_file": sync_hint_path,
    "related_context": related_paths,
}

manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

run_codex() {
  local preset="$1"
  local use_search="$2"
  local prompt="$3"
  local target_path="$4"
  local target_rel="$5"
  local target_state="$6"
  local expected_review_path="$7"
  local sync_hint_path="$8"
  shift 8
  local timestamp out_file manifest_file
  local -a cmd related_context

  related_context=("$@")
  timestamp="$(date '+%Y%m%d-%H%M%S')"
  out_file="$OUTPUT_DIR/${timestamp}-${preset}.md"
  manifest_file="$OUTPUT_DIR/${timestamp}-${preset}.meta.json"

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
    "$preset" \
    "$target_path" \
    "$target_rel" \
    "$target_state" \
    "$use_search" \
    "$out_file" \
    "$expected_review_path" \
    "$sync_hint_path" \
    "${related_context[@]}"

  printf '\nSaved final message to %s\n' "$out_file"
  printf 'Saved run manifest to %s\n' "$manifest_file"
}

build_prompt() {
  local preset="$1"
  local target_path="$2"
  local target_rel="$3"
  local notes="$4"
  local target_state="$5"
  local use_search="$6"
  local nearby_context="$7"
  local expected_review_path="$8"
  local sync_hint_path="$9"
  local search_state review_trace sync_trace

  if [[ "$use_search" == "yes" ]]; then
    search_state="enabled by launcher"
  else
    search_state="disabled by launcher"
  fi

  if [[ -n "$expected_review_path" ]]; then
    review_trace="- Preferred review artifact path: $expected_review_path"
  else
    review_trace="- No dedicated review artifact path was precomputed for this run."
  fi

  sync_trace="- Preferred sync checkpoint path: $sync_hint_path"

  case "$preset" in
    full-cycle)
      cat <<EOF
Use \$thesis-workflow-orchestrator to handle this thesis task end-to-end in /Users/albina/дипломная.

Target artifact: $target_path
Target path (relative): $target_rel
Target state: $target_state
Web search: $search_state

Nearby context candidates:
$nearby_context

Execution rules:
- Open AGENTS.md, meta/project-canon.md, meta/master-protocol.md, and the most relevant nearby files before editing.
- Use the appropriate internal chain across structure, research, verification, drafting, citations, criticism, and style.
- For dynamic legal material, verify against up-to-date official or primary sources and use web search when needed.
- Write canonical text only into the correct thesis artifact.
- If you safely skip a workflow step, record the reason in a sync artifact.
- If you update a manuscript section, rebuild with scripts/assemble_thesis.sh.
- If the task explicitly asks for Word output or reaches a polished section checkpoint, export DOCX with scripts/export_docx.sh.
- Do not optimize for detector bypass. Optimize for independent analysis, reliable sourcing, and natural academic prose.

Operational trace:
$sync_trace
$review_trace

Additional notes:
$notes

Deliverable:
- Make the changes directly in files.
- Update sync/ if the run produces a meaningful checkpoint.
- End with a concise summary of changed files, verification performed, and remaining risks.
EOF
      ;;
    source-pack)
      cat <<EOF
Use \$thesis-research-synthesizer and \$thesis-source-verifier for this thesis source-package task.

Target source package: $target_path
Target path (relative): $target_rel
Target state: $target_state
Web search: $search_state

Nearby context candidates:
$nearby_context

Execution rules:
- Build or update the package using templates/source-package-passport.md.
- Prefer primary and official sources for law, case law, regulator guidance, and statistics.
- Record verification dates for dynamic materials.
- Mark what is verified, what still needs re-checking, and what remains analytical rather than factual.
- Keep the package compact and thesis-oriented rather than encyclopedic.

Operational trace:
$sync_trace

Additional notes:
$notes

Deliverable:
- Update the target package directly.
- Update sync/ if the package meaningfully changes the working baseline.
- End with a concise summary of sources added, sources verified, and gaps that still remain.
EOF
      ;;
    verify)
      cat <<EOF
Use \$thesis-source-verifier and \$thesis-citation-checker for this verification pass.

Target file: $target_path
Target path (relative): $target_rel
Target state: $target_state
Web search: $search_state

Nearby context candidates:
$nearby_context

Execution rules:
- Check significant legal, factual, and statistical claims for source support.
- For dynamic materials, verify against current official or primary sources and use web search when needed.
- Narrow or mark unsafe claims instead of leaving them overstated.
- Strengthen citations or footnote hygiene where appropriate.
- Do not do a broad stylistic rewrite unless a wording change is necessary to restore accuracy.

Operational trace:
$sync_trace
$review_trace

Additional notes:
$notes

Deliverable:
- Update the target file if factual or citation fixes are needed.
- If verification materially changes project assumptions, update sync/.
- End with a concise summary of what was verified, what was corrected, and what still needs follow-up.
EOF
      ;;
    write-section)
      cat <<EOF
Use \$thesis-draft-writer, \$thesis-source-verifier, and \$thesis-citation-checker to draft or expand this thesis section.

Target section: $target_path
Target path (relative): $target_rel
Target state: $target_state
Web search: $search_state

Nearby context candidates:
$nearby_context

Execution rules:
- Open the relevant brief, source packages, and canon/protocol files before writing.
- Draft only from verified sources or clearly marked analytical conclusions.
- Keep the voice academic, specific, and legally grounded.
- Add or maintain Markdown footnotes where source support is already pinned.
- If you safely skip a workflow step, record the reason in sync/.
- If the target is inside manuscript/sections, rebuild the manuscript after changes.

Operational trace:
$sync_trace
$review_trace

Additional notes:
$notes

Deliverable:
- Update the section directly.
- Update sync/ if the section reaches a meaningful checkpoint.
- End with a concise summary of what was written, which sources were relied on, and what remains unverified or incomplete.
EOF
      ;;
    review-section)
      cat <<EOF
Use \$thesis-argument-critic and \$thesis-citation-checker to review this thesis section.

Target section: $target_path
Target path (relative): $target_rel
Target state: $target_state
Web search: $search_state

Nearby context candidates:
$nearby_context

Execution rules:
- Review the section for logic gaps, overclaims, repetition, weak transitions, and citation issues.
- Create or update the review artifact exactly here: $expected_review_path
- Use templates/chapter-review-sheet.md as the review structure.
- Keep the primary output findings-first.
- Do not rewrite the manuscript broadly; only make trivial citation-hygiene fixes if they are obvious and safe.

Operational trace:
$sync_trace

Additional notes:
$notes

Deliverable:
- Update or create $expected_review_path
- Update sync/ if the review changes priorities or safely skips any expected check.
- End with the key findings first, then a brief note on any small fixes made.
EOF
      ;;
    style-pass)
      cat <<EOF
Use \$thesis-style-editor for a final style refinement pass on this checked thesis text.

Target file: $target_path
Target path (relative): $target_rel
Target state: $target_state
Web search: $search_state

Nearby context candidates:
$nearby_context

Execution rules:
- Improve natural academic Russian, paragraph rhythm, specificity, and authorial voice.
- Do not change the substantive meaning of claims unless a tiny narrowing is needed for credibility.
- Do not optimize for detector bypass or mechanical uniqueness.
- Remove stock transitions and machine-flat phrasing where possible.
- If the target is inside manuscript/sections, rebuild the manuscript after changes.

Operational trace:
$sync_trace
$review_trace

Additional notes:
$notes

Deliverable:
- Update the target file directly.
- End with a concise summary of stylistic improvements and any residual sections that still sound too generic.
EOF
      ;;
    *)
      printf 'Unknown preset: %s\n' "$preset" >&2
      return 1
      ;;
  esac
}

PRESET="${1:-help}"
shift || true

if [[ "$PRESET" == "help" ]]; then
  print_usage
  exit 0
fi

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  print_usage >&2
  exit 1
fi
shift || true

NOTES_ARG=""
USE_SEARCH="auto"
DRY_RUN="no"

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
      printf 'Unknown option: %s\n' "$1" >&2
      exit 1
      ;;
  esac
done

TARGET_PATH="$(resolve_path "$TARGET")"
if ! TARGET_REL="$(path_relative_to_root "$TARGET_PATH")"; then
  printf 'Target must be inside %s\n' "$ROOT_DIR" >&2
  exit 1
fi

assert_target_matches_preset "$PRESET" "$TARGET_REL"

if [[ "$USE_SEARCH" == "auto" ]]; then
  USE_SEARCH="$(default_search_for_preset "$PRESET")"
fi

TARGET_STATE="missing"
if [[ -e "$TARGET_PATH" ]]; then
  TARGET_STATE="existing"
fi

EXPECTED_REVIEW_PATH="$(derive_review_path "$TARGET_REL" || true)"
SYNC_HINT_PATH="$(derive_sync_hint_path "$PRESET" "$TARGET_REL")"

RELATED_CONTEXT=()
while IFS= read -r related_path; do
  if [[ -n "$related_path" ]]; then
    RELATED_CONTEXT+=("$related_path")
  fi
done < <(collect_related_paths "$TARGET_PATH")

NEARBY_CONTEXT_BLOCK="$(format_paths_block "${RELATED_CONTEXT[@]}")"
NOTES_CONTENT="$(read_notes "$NOTES_ARG")"
PROMPT="$(build_prompt "$PRESET" "$TARGET_PATH" "$TARGET_REL" "$NOTES_CONTENT" "$TARGET_STATE" "$USE_SEARCH" "$NEARBY_CONTEXT_BLOCK" "$EXPECTED_REVIEW_PATH" "$SYNC_HINT_PATH")"

if [[ "$DRY_RUN" == "yes" ]]; then
  printf 'Preset: %s\n' "$PRESET"
  printf 'Target: %s\n' "$TARGET_PATH"
  printf 'Target (relative): %s\n' "$TARGET_REL"
  printf 'Target state: %s\n' "$TARGET_STATE"
  printf 'Search enabled: %s\n' "$USE_SEARCH"
  if [[ -n "$EXPECTED_REVIEW_PATH" ]]; then
    printf 'Expected review file: %s\n' "$EXPECTED_REVIEW_PATH"
  fi
  printf 'Sync hint file: %s\n' "$SYNC_HINT_PATH"
  if [[ -n "$CODEX_MODEL" ]]; then
    printf 'Model: %s\n' "$CODEX_MODEL"
  fi
  printf 'Related context:\n%s\n' "$NEARBY_CONTEXT_BLOCK"
  printf '\n%s\n' "$PROMPT"
  exit 0
fi

run_codex \
  "$PRESET" \
  "$USE_SEARCH" \
  "$PROMPT" \
  "$TARGET_PATH" \
  "$TARGET_REL" \
  "$TARGET_STATE" \
  "$EXPECTED_REVIEW_PATH" \
  "$SYNC_HINT_PATH" \
  "${RELATED_CONTEXT[@]}"
