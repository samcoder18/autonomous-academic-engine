#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
prepend_path_if_dir() {
  local candidate="$1"
  if [[ -d "$candidate" ]]; then
    PATH="$candidate${PATH:+:$PATH}"
  fi
}

prepend_path_if_dir "/Library/Frameworks/Python.framework/Versions/Current/bin"
prepend_path_if_dir "/opt/homebrew/bin"
prepend_path_if_dir "/usr/local/bin"
export PATH
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

if ! command -v pandoc >/dev/null 2>&1; then
  echo "Ошибка: pandoc не найден в PATH. Установите Pandoc: https://pandoc.org" >&2
  exit 1
fi

PYTHON_CMD="${PYTHON_BIN:-python3}"
"$PYTHON_CMD" -m telegram_console.work_cli export-article-docx "$@"
