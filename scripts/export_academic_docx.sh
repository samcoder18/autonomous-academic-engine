#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_OUTPUT_DIR="$ROOT_DIR/output/docx/articles"

if [[ $# -lt 1 || $# -gt 2 ]]; then
  printf 'Usage: bash scripts/export_academic_docx.sh <input-md> [output-docx]\n' >&2
  exit 1
fi

INPUT_RAW="$1"
INPUT_MD="$(python3 - "$ROOT_DIR" "$INPUT_RAW" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
raw = Path(sys.argv[2]).expanduser()
path = raw if raw.is_absolute() else (root / raw)
print(path.resolve())
PY
)"

if [[ ! -f "$INPUT_MD" ]]; then
  printf 'Input markdown not found: %s\n' "$INPUT_MD" >&2
  exit 1
fi

if [[ $# -eq 2 ]]; then
  OUTPUT_RAW="$2"
  OUTPUT_DOCX="$(python3 - "$ROOT_DIR" "$OUTPUT_RAW" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
raw = Path(sys.argv[2]).expanduser()
path = raw if raw.is_absolute() else (root / raw)
print(path.resolve())
PY
)"
else
  mkdir -p "$DEFAULT_OUTPUT_DIR"
  OUTPUT_DOCX="$DEFAULT_OUTPUT_DIR/$(basename "${INPUT_MD%.md}").docx"
fi

mkdir -p "$(dirname "$OUTPUT_DOCX")"

pandoc "$INPUT_MD" \
  --from markdown+footnotes \
  --to docx \
  --output "$OUTPUT_DOCX"

printf 'Exported %s\n' "$OUTPUT_DOCX"
