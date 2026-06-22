# Final Quality Audit (2026-04-20)

## Scope

- Repo-wide final audit across `academic_engine`, tests, CLI/runtime surfaces, dissertation contour, and docs-truth.
- Conservative repair pass only: no broad refactor wave, no manuscript prose edits, no changes to public CLI names or payload contracts.

## Baseline Before Fixes

- `python3 -m unittest discover -s tests -q` — `384 tests OK`.
- `ruff check academic_engine/ tests/` — passed.
- `ruff format --check academic_engine/ tests/` — failed on 7 Python files.
- Manual temp-workspace CLI smoke found a public-command regression:
  - `python3 -m academic_engine.work_cli one-shot-dissertation --work <candidate> --skip-docx`
  - actual behavior before fix: crashed with `TypeError: 'ArgumentParser' object is not callable`
  - expected behavior: return a normal dissertation one-shot result with blocker status and a written report.

## Fixed Now

1. Restored the `one-shot-dissertation` CLI contract in `academic_engine/work_cli.py`.
   - Root cause: the local parser variable `one_shot_dissertation` shadowed the function of the same name inside `main()`.
   - Fix: renamed the parser binding to `one_shot_dissertation_parser`, preserving the public command and arguments.

2. Added a narrow CLI regression test in `tests/test_work_bootstrap.py`.
   - The test bootstraps a dissertation work in a temp workspace, runs `one-shot-dissertation --skip-docx`, and asserts that the command returns a blocker status and writes a report instead of crashing.

3. Applied the deferred Python formatting pass.
   - Formatted the previously drifting files so `ruff format --check` is clean again.

4. Synced discoverability docs.
   - Updated live docs to point to this audit report and refreshed the README test count after the new regression coverage.

## Verification After Fixes

- `python3 -m unittest discover -s tests -q` — `385 tests OK`.
- `ruff check academic_engine/ tests/` — passed.
- `ruff format --check academic_engine/ tests/` — passed.
- Manual command-surface smoke in a temp workspace:
  - `work init` for `vkr-bachelor`, `dissertation-candidate`, `dissertation-doctor` — OK.
  - `work-status --json` — OK, payload shape preserved.
  - `build-dissertation-artifacts` — returns deterministic metadata blockers, no crash.
  - `one-shot-thesis --skip-docx` — returns blocker status with report, no crash.
  - `one-shot-dissertation --skip-docx` — now returns blocker status with report, no crash.
- Read-only status check on active work:
  - `python3 -m academic_engine.work_cli work-status --json` — OK, no mutation required.

## Residual Risks / Safe Defer

- Further split of `work_cli.py` beyond the already extracted autonomous surfaces remains useful, but is not required for correctness after this pass.
- Real macOS LaunchAgent smoke on a launchd-capable runner is still valuable for ops confidence and remains outside deterministic local coverage.
- Explicit schema validation for autonomous runtime JSON files is still a nice hardening layer, but current normalize/fallback behavior is operationally acceptable.
- Candidate contour would still benefit from one pilot dry-run on a live work bundle in addition to synthetic tests.

## Notes

- No repo-tracked thesis/article manuscript prose was edited in this pass.
- Historical audit documents from earlier waves were left intact; this file is the canonical final-audit summary for the current polish pass.
