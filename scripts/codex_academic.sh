#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

if [[ "${1:-}" == "thesis" ]]; then
  shift
  exec bash "$ROOT_DIR/scripts/codex_thesis.sh" "$@"
fi

python3 -m academic_engine.work_cli launch-academic "$@"
