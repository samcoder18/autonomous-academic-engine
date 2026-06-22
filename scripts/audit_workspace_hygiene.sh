#!/usr/bin/env bash
set -euo pipefail

echo "== git status =="
git status --short

echo "== tracked output/docx =="
git ls-files output/docx

echo "== runtime ignore rule =="
git check-ignore -v output/runtime/local.sqlite3

echo "== standards =="
python3 -m academic_engine.work_cli standards-status

echo "== skill source map =="
python3 -m academic_engine.work_cli skill-source-map audit --json
