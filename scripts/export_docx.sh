#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INPUT_MD="$ROOT_DIR/manuscript/full-draft.md"
OUTPUT_DIR="$ROOT_DIR/output/docx"
OUTPUT_DOCX="$OUTPUT_DIR/thesis-draft.docx"

mkdir -p "$OUTPUT_DIR"

"$ROOT_DIR/scripts/assemble_thesis.sh" >/dev/null

pandoc "$INPUT_MD" \
  --from markdown+footnotes \
  --to docx \
  --output "$OUTPUT_DOCX"

printf 'Exported %s\n' "$OUTPUT_DOCX"
