#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 <file> [target_pages] [chars_per_page]"
  exit 1
fi

FILE="$1"
TARGET_PAGES="${2:-0}"
CHARS_PER_PAGE="${3:-1800}"

if [[ ! -f "$FILE" ]]; then
  echo "File not found: $FILE"
  exit 1
fi

CHAR_COUNT="$(wc -m < "$FILE" | tr -d ' ')"

python3 - "$FILE" "$CHAR_COUNT" "$TARGET_PAGES" "$CHARS_PER_PAGE" <<'PY'
import math
import sys

file_path = sys.argv[1]
char_count = int(sys.argv[2])
target_pages = int(sys.argv[3])
chars_per_page = int(sys.argv[4])

estimated_pages = char_count / chars_per_page if chars_per_page else 0

print(f"File: {file_path}")
print(f"Characters (with spaces): {char_count}")
print(f"Estimated pages: {estimated_pages:.2f} (at {chars_per_page} chars/page)")

if target_pages > 0:
    target_chars = target_pages * chars_per_page
    diff = target_chars - char_count
    print(f"Target pages: {target_pages}")
    print(f"Target characters: {target_chars}")
    if diff > 0:
        print(f"Status: short by about {diff} characters ({diff / chars_per_page:.2f} pages)")
    else:
        print(f"Status: target reached, above target by about {abs(diff)} characters ({abs(diff) / chars_per_page:.2f} pages)")
PY
