#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

if ! command -v pandoc >/dev/null 2>&1; then
  echo "Ошибка: pandoc не найден в PATH. Установите Pandoc: https://pandoc.org" >&2
  exit 1
fi

python3 -m telegram_console.work_cli export-thesis-docx "$@"
