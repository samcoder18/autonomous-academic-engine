# Changelog

Этот changelog фиксирует GitHub-facing релизные изменения `autonomous-academic-engine`.
Подробные инженерные аудиты и closeout-документы остаются в `meta/`.

## 2026-04-21 — Academic Quality Hardening

Коротко:

- professional skill hardening для всех 19 repo-mapped thesis/article skills;
- strict academic quality enforcement для claim passports, review artifacts и managed finalization;
- synchronized external `SKILL.md` contracts от repo-side `agents/*.md`;
- clean verification wave с повторным полным прогоном тестов и audit surfaces.

Что изменилось:

- стандартизированы agent role docs: единый operating contract, role boundaries, handoff и verdict discipline;
- article/thesis runtime parsers читают больше machine-readable blockers из review/checklist artifacts;
- `quality_advisories` теперь поднимают flags по missing locator/excerpt, missing caveat, unsafe draft use и review-derived citation/logic issues;
- `finalization_engine` больше не считает bundle `export-ready`, если открыты citation / logic / review blockers;
- `one-shot-thesis` для managed thesis bundle проверяет `thesis-quality-contract`;
- templates `evidence-ledger`, `evidence-pack`, `claim-map`, `verification-log`, `source-package-passport` усилены под strict claim-passport contract.

Verification:

- `python3 -m unittest discover -s tests -q` — `395 tests OK` два раза подряд;
- `ruff check telegram_console/ tests/` — OK;
- `ruff format --check telegram_console/ tests/` — OK;
- `python3 -m telegram_console.work_cli skill-source-map audit --json` — OK;
- `python3 -m telegram_console.work_cli skill-source-map audit --skills-root /Users/albina/.codex/skills --json` — OK;
- `python3 -m telegram_console.work_cli work-status --json` — OK.

Release boundary:

- этот релиз закрывает repo/platform layer;
- content-level acceptance конкретного `works/<slug>/` bundle остается отдельной финальной фазой.
