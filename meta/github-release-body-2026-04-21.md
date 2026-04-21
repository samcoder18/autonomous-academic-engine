# GitHub Release Body (2026-04-21)

`autonomous-academic-engine` is an autonomous workflow engine for legal-academic writing with deterministic quality gates and evidence-first finalization.

## Highlights

- Hardened academic quality gates across thesis and article workflows.
- Standardized all 19 repo-mapped thesis/article skill contracts.
- Synced external Codex `SKILL.md` files from repo-side `agents/*.md`.
- Added stricter machine enforcement for claim passports, review artifacts, and managed finalization.

## What Changed

- Article and thesis runtime parsers now read additional machine-readable blockers from review/checklist artifacts.
- `quality_advisories` now flag missing pinpoint locator, missing support excerpt, missing caveat, unsafe draft use, and review-derived citation/logic risks.
- `finalization_engine` no longer allows `export-ready` when citation, logic, or review blockers remain open.
- `one-shot-thesis` now enforces `thesis-quality-contract` for managed thesis bundles.
- Evidence and verification templates were upgraded to a stricter claim-passport contract.

## Verification

- `python3 -m unittest discover -s tests -q` — `395 tests OK` twice in a row
- `ruff check telegram_console/ tests/` — OK
- `ruff format --check telegram_console/ tests/` — OK
- `python3 -m telegram_console.work_cli skill-source-map audit --json` — OK
- `python3 -m telegram_console.work_cli skill-source-map audit --skills-root /Users/albina/.codex/skills --json` — OK
- `python3 -m telegram_console.work_cli work-status --json` — OK

## Notes

- This release closes the repo/platform layer.
- Content-level acceptance for a specific `works/<slug>/` manuscript bundle remains a separate final phase.
