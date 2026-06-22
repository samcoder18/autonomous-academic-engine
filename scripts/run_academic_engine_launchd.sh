#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/output/telegram/.env.launchd"

prepend_path_if_dir() {
  local candidate="$1"
  if [[ -d "$candidate" ]]; then
    PATH="$candidate${PATH:+:$PATH}"
  fi
}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

prepend_path_if_dir "/Library/Frameworks/Python.framework/Versions/Current/bin"
prepend_path_if_dir "/opt/homebrew/bin"
prepend_path_if_dir "/usr/local/bin"
prepend_path_if_dir "/Applications/Codex.app/Contents/Resources"
export PATH
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

python_is_supported() {
  local candidate="$1"
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  declare -a candidates=(
    "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"
    "/opt/homebrew/bin/python3"
    "/usr/local/bin/python3"
  )
  if command -v python3 >/dev/null 2>&1; then
    candidates+=("$(command -v python3)")
  fi
  candidates+=("/usr/bin/python3")
  for candidate in "${candidates[@]}"; do
    if [[ -n "$candidate" && -x "$candidate" ]] && python_is_supported "$candidate"; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Compatible python3 (>=3.11) not found for LaunchAgent runner" >&2
  exit 1
fi

if ! python_is_supported "$PYTHON_BIN"; then
  echo "Configured PYTHON_BIN must point to python3 >=3.11" >&2
  exit 1
fi

if [[ -z "${CODEX_BIN:-}" && -x "/Applications/Codex.app/Contents/Resources/codex" ]]; then
  export CODEX_BIN="/Applications/Codex.app/Contents/Resources/codex"
fi

export PYTHON_BIN
cd "$ROOT_DIR"
exec "$PYTHON_BIN" -m academic_engine --root "$ROOT_DIR"
