#!/usr/bin/env bash
set -euo pipefail

echo "== git status =="
git status --short

echo "== tracked output/docx =="
git ls-files output/docx

echo "== ignore rules =="
git check-ignore -v frontend/node_modules/.package-lock.json
git check-ignore -v frontend/.next/trace
git check-ignore -v output/runtime/web-control-plane.sqlite3

echo "== standards =="
python3 -m telegram_console.work_cli standards-status

echo "== skill source map =="
python3 -m telegram_console.work_cli skill-source-map audit --json
