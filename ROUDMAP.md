# Refactoring Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean the repository, remove generated junk and stale artifacts, consolidate duplicate workflow utilities, and make the pipeline easier to operate without weakening safety gates.

**Architecture:** Keep canonical source text and configuration under `works/<slug>/`, `telegram_console/`, `agents/`, `templates/`, `meta/`, and launcher scripts. Treat `output/`, local caches, runtime databases, `node_modules`, `.next`, and one-shot JSON traces as generated state unless a file is explicitly documented as a versioned evidence snapshot. Make every cleanup step reversible through git review and verify with the existing offline CI matrix.

**Tech Stack:** Python 3.11+ stdlib, unittest, ruff, shell launchers, TOML/Markdown workspace contracts, git.

---

## Progress Log

- [x] **2026-06-22: Baseline audit completed.** Evidence gathered with `git status --short`, `python3 -m unittest discover -s tests -q`, `ruff check telegram_console tests`, `ruff format --check telegram_console tests`, `work-status`, `standards-status`, and targeted file inventory commands.
- [x] **2026-06-22: Roadmap file created.** This plan is saved as `ROUDMAP.md`, matching the requested filename.
- [x] **2026-06-22: Execution branch prepared.** Work moved from `main` to `cleanup-roadmap-20260622`; baseline verification passed with 429 unittest tests, `ruff check`, and `ruff format --check`.
- [x] **2026-06-22: Task 1 completed.** `.gitignore` now excludes local frontend build artifacts and runtime SQLite files; `git check-ignore` verified all targeted paths.
- [x] **2026-06-22: Task 2 completed.** Ignored generated directories `frontend/`, `output/runtime/`, and `academic_engine/` were removed after dry-run confirmation; git status stayed clean afterward.
- [x] **2026-06-22: Task 5 completed.** `WorkflowError` from blocked exports now returns a clean CLI error instead of a traceback; regression test added and full verification passed with 430 unittest tests, `ruff check`, and `ruff format --check`.
- [x] **2026-06-22: Task 4 completed.** One-shot reports now emit `one-shot-report/v2`, thesis export rejects legacy reports, stale Markdown reports were archived with legacy warnings, ignored one-shot JSON traces were removed locally, and verification passed with 433 unittest tests plus ruff gates.
- [x] **2026-06-22: Task 3 completed.** `output/docx/` policy is now strict generated-output-only; 135 constitutional render/PDF files were removed from git index, local copies are ignored, and verification passed with 433 unittest tests plus ruff gates.
- [x] **2026-06-22: Task 6 completed.** Duplicate work-local DOCX formatting scripts were replaced by shared `telegram_console.docx_preview` plus `scripts/render_docx_preview.py`; work-specific settings now live in `work.toml`, and verification passed with 436 unittest tests plus ruff gates.
- [x] **2026-06-22: Task 7 completed.** Thesis works now bind to official `sogu-vkr-2025` with raw bundle available; the old `thesis-standards-raw-missing` blocker is gone, while the profile's honest conflict/applicability flag remains visible.

## Initial Audit Baseline

- Test suite: `python3 -m unittest discover -s tests -q` passed with 429 tests.
- Lint: `ruff check telegram_console tests` passed.
- Format: `ruff format --check telegram_console tests` passed.
- Skill map: `python3 -m telegram_console.work_cli skill-source-map audit --json` reported `ok: true`.
- Dirty tree before cleanup: untracked `frontend/` and `output/runtime/`.
- Large local junk: `frontend/` is about 629 MB and contains only `.next/` plus `node_modules/`.
- Runtime junk: `output/runtime/` contains SQLite files for `web-control-plane`.
- Ignored local duplicate package cache: `academic_engine/` is ignored and contains `__pycache__` files.
- Versioned generated output exists under `output/docx/`, including PDF and rendered PNG snapshots.
- Work-local DOCX helper scripts exist at:
  - `works/martial-law-coursework/thesis/format_docx.py`
  - `works/constitutional-amendments-implementation-coursework/thesis/format_docx.py`
- Thesis works currently have a standards blocker for `ru-vkr-university-default` because its raw bundle is missing.
- Legacy one-shot reports still say `submission-ready` while noting that originality was skipped. Current code correctly requires an originality corpus and emits `machine-gates-passed` or `blocked`.

## Non-Negotiable Guardrails

- Do not delete canonical text under `works/<slug>/work-canon.md` or `works/<slug>/thesis/manuscript/sections/`.
- Do not delete source packages, ledgers, verification logs, or review Markdown unless the file is explicitly superseded and archived.
- Do not manually edit generated DOCX as source of truth.
- Do not remove safety gates to make export easier.
- Do not use external anti-plagiarism or AI-detector services.
- Use `git status --short` before and after each task.
- Run the verification matrix after each task that changes code or tests:

```bash
python3 -m unittest discover -s tests -q
ruff check telegram_console tests
ruff format --check telegram_console tests
```

---

## Task 1: Align `.gitignore` With Runtime Reality

**Files:**
- Modify: `.gitignore`
- Verify: `git check-ignore`

- [x] **Step 1: Add ignored generated paths**

Add these entries to `.gitignore` below the existing output/runtime rules:

```gitignore
# Local web/frontend build artifacts
frontend/node_modules/
frontend/.next/

# Local runtime databases
output/runtime/
*.sqlite3
*.sqlite3-shm
*.sqlite3-wal
```

- [x] **Step 2: Verify ignore behavior**

Run:

```bash
git check-ignore -v frontend/node_modules/.package-lock.json
git check-ignore -v frontend/.next/trace
git check-ignore -v output/runtime/web-control-plane.sqlite3
```

Expected: each command prints the matching `.gitignore` rule.

- [x] **Step 3: Verify no source files were hidden accidentally**

Run:

```bash
git status --short
```

Expected: `frontend/` and `output/runtime/` no longer appear as untracked directories; only intentional source edits remain.

- [x] **Step 4: Commit**

```bash
git add .gitignore ROUDMAP.md
git commit -m "chore: ignore local runtime and frontend artifacts"
```

---

## Task 2: Remove Local Generated Junk From the Working Tree

**Files:**
- Remove locally only: `frontend/.next/`, `frontend/node_modules/`, `output/runtime/`, `academic_engine/`
- Do not remove tracked source files.

- [x] **Step 1: Preview ignored cleanup**

Run:

```bash
git clean -ndX frontend output/runtime academic_engine
```

Expected: the preview lists only ignored generated files and cache directories.

- [x] **Step 2: Remove ignored generated files after preview is clean**

Run:

```bash
git clean -fdX frontend output/runtime academic_engine
```

Expected: the same ignored generated files are removed.

- [x] **Step 3: Verify size reduction**

Run:

```bash
du -sh frontend output/runtime academic_engine
```

Expected: removed paths are absent or much smaller; no source directory under `telegram_console/`, `works/`, `meta/`, `agents/`, `templates/`, or `tests/` is affected.

- [x] **Step 4: Verify git tree**

Run:

```bash
git status --short
```

Expected: cleanup does not create source deletions outside the intended `.gitignore` and roadmap changes.

---

## Task 3: Decide and Enforce the `output/docx` Versioning Policy

**Files:**
- Modify: `output/README.md`
- Modify: `.gitignore`
- Possibly remove from git index: generated files under `output/docx/constitutional-amendments-implementation-coursework/`
- Possibly keep: explicitly documented evidence snapshots

- [x] **Step 1: Inventory tracked output files**

Run:

```bash
git ls-files output/docx
```

Expected: list all currently versioned PDF, PNG, and render-tool files under `output/docx/`.

Actual: 136 tracked files were found before cleanup: 135 generated PDF/PNG render files under `output/docx/constitutional-amendments-implementation-coursework/` plus legacy `output/docx/state-essence-role-coursework/render-tools/render_docx.py`.

- [x] **Step 2: Choose one policy**

Use one of these two policies and write it into `output/README.md`:

```markdown
## Versioning Policy

Default: `output/docx/` is generated output and is not a source of truth.

Allowed exceptions: versioned PDF/PNG evidence snapshots are allowed only when a nearby Markdown note explains the audit reason, source manuscript, generation date, and verification purpose.
```

or:

```markdown
## Versioning Policy

`output/docx/` is generated output and must not be committed. Canonical text lives under `works/<slug>/`. Visual render checks should be regenerated locally and excluded from git.
```

Chosen: strict generated-output policy. `output/docx/` is ignored as generated output; reusable renderer code must move to `scripts/` or `telegram_console/` in Task 6.

- [x] **Step 3: If generated snapshots should not be versioned, remove them from git index**

Run:

```bash
git rm -r output/docx/constitutional-amendments-implementation-coursework
```

Expected: generated snapshots are staged for removal, while `output/README.md` remains tracked.

Actual: removed the constitutional render bundle with `git rm -r --cached output/docx/constitutional-amendments-implementation-coursework`, so local files remain available but are no longer tracked.

- [x] **Step 4: If evidence snapshots should stay versioned, add an evidence note**

Create:

```text
output/docx/constitutional-amendments-implementation-coursework/README.md
```

Content:

```markdown
# Versioned Render Evidence

These files are retained as visual evidence snapshots for the constitutional-amendments coursework revision. They are not canonical source text. Canonical text remains in `works/constitutional-amendments-implementation-coursework/thesis/manuscript/sections/`.

Regenerate only from the canonical Markdown and record the command used in the relevant review artifact.
```

Actual: not applicable because snapshots should not stay versioned under the chosen strict policy.

- [x] **Step 5: Verify**

Run:

```bash
python3 -m unittest discover -s tests -q
ruff check telegram_console tests
ruff format --check telegram_console tests
```

Expected: all commands pass.

---

## Task 4: Normalize Legacy One-Shot Reports

**Files:**
- Modify: `telegram_console/one_shot.py`
- Modify: `telegram_console/orchestrator_exports.py`
- Modify: `tests/test_one_shot.py`
- Modify or archive: `works/*/thesis/reviews/*one-shot-report.md`
- Remove from git index if generated: `works/*/thesis/reviews/*one-shot-report.json`

- [x] **Step 1: Add report version to new one-shot JSON**

Modify `OneShotReport.to_dict()` in `telegram_console/one_shot.py` so it emits:

```python
"version": "one-shot-report/v2",
```

Expected: new reports are machine-identifiable and old reports can be rejected explicitly.

- [x] **Step 2: Require v2 machine gate reports for export**

Modify `require_machine_gates_passed()` in `telegram_console/orchestrator_exports.py` to accept only:

```python
payload.get("version") == "one-shot-report/v2"
payload.get("status") == "machine-gates-passed"
```

Expected: legacy reports with `submission-ready` or skipped originality cannot unlock export.

- [x] **Step 3: Extend tests**

Add tests in `tests/test_one_shot.py` covering:

```python
def test_report_dict_contains_v2_version(self) -> None:
    ...

def test_legacy_submission_ready_report_does_not_unlock_export(self) -> None:
    ...
```

Expected: old `submission-ready` one-shot JSON is rejected even if all legacy gates say PASS.

- [x] **Step 4: Archive stale Markdown reports**

Move old Markdown reports into:

```text
works/<slug>/thesis/reviews/archive/
```

Add a short header to each archived report:

```markdown
> Legacy report. This predates mandatory originality corpus enforcement and must not be used as a submission-ready signal.
```

- [x] **Step 5: Remove generated one-shot JSON from git index**

Run:

```bash
git rm works/*/thesis/reviews/*one-shot-report.json
```

Expected: JSON traces stop being versioned; `.gitignore` already excludes future one-shot JSON.

Actual: current JSON traces were already ignored/untracked, so they were removed from the local working tree and verified absent with `find works -path '*/thesis/reviews/*one-shot-report.json' -print`.

- [x] **Step 6: Verify**

Run:

```bash
python3 -m unittest tests.test_one_shot -v
python3 -m unittest discover -s tests -q
```

Expected: all tests pass.

---

## Task 5: Fix CLI Error Handling for Export Blocks

**Files:**
- Modify: `telegram_console/work_cli.py`
- Test: add coverage to `tests/test_work_cli_runtime.py` or `tests/test_work_cli_autonomous.py`

- [x] **Step 1: Catch `WorkflowError` in CLI main**

Import `WorkflowError` from `telegram_console.orchestrator_support` in `telegram_console/work_cli.py` and extend the existing exception handler:

```python
from .orchestrator_support import WorkflowError
```

```python
    except (WorkspaceConfigError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
```

Expected: blocked export prints a clean one-line error instead of a traceback.

- [x] **Step 2: Add regression test**

Add a test that calls:

```bash
python3 -m telegram_console.work_cli export-thesis-docx --work martial-law-coursework
```

Expected: exit code `1`, stderr contains `DOCX export blocked`, stderr does not contain `Traceback`.

- [x] **Step 3: Verify**

Run:

```bash
python3 -m unittest tests.test_work_cli_runtime -v
python3 -m unittest discover -s tests -q
```

Expected: all tests pass.

---

## Task 6: Consolidate Work-Local DOCX Formatting Helpers

**Files:**
- Inspect: `works/martial-law-coursework/thesis/format_docx.py`
- Inspect: `works/constitutional-amendments-implementation-coursework/thesis/format_docx.py`
- Create: `scripts/render_docx_preview.py` or `telegram_console/docx_preview.py`
- Remove after replacement: work-local `thesis/format_docx.py` scripts
- Test: `tests/test_docx_conformance.py` or a new focused test file

- [x] **Step 1: Diff existing helpers**

Run:

```bash
diff -u works/martial-law-coursework/thesis/format_docx.py works/constitutional-amendments-implementation-coursework/thesis/format_docx.py
```

Expected: identify which behavior is shared and which behavior is work-specific.

Actual: shared font/page/numbering/metadata behavior was extracted; work-specific title spacing, major titles, contents-table handling, and metadata were moved to config.

- [x] **Step 2: Extract shared behavior**

Create a single helper with a work argument:

```bash
python3 scripts/render_docx_preview.py --work martial-law-coursework
python3 scripts/render_docx_preview.py --work constitutional-amendments-implementation-coursework
```

Expected: no work-specific script is needed under `works/<slug>/thesis/`.

Actual: created `telegram_console/docx_preview.py` and `scripts/render_docx_preview.py`. CLI smoke checks load real work config and return a clean input/dependency error instead of a traceback when rendering prerequisites are absent.

- [x] **Step 3: Preserve work-specific settings as config**

If settings differ, put them in `work.toml` under a narrow section:

```toml
[thesis.docx_preview]
enabled = true
```

Expected: behavior is data-driven, not copied script-driven.

Actual: added `[thesis.docx_preview]` settings to the two affected work bundles.

- [x] **Step 4: Remove duplicate helpers**

Run:

```bash
git rm works/martial-law-coursework/thesis/format_docx.py works/constitutional-amendments-implementation-coursework/thesis/format_docx.py
```

Expected: duplicate scripts are gone after the shared helper passes equivalent checks.

Actual: removed both work-local `thesis/format_docx.py` scripts.

- [x] **Step 5: Verify**

Run:

```bash
python3 -m unittest tests.test_docx_conformance -v
python3 -m unittest discover -s tests -q
```

Expected: DOCX conformance tests and full suite pass.

Actual: `tests.test_docx_preview`, `tests.test_docx_conformance`, full unittest discovery, `ruff check`, and `ruff format --check` passed. `python-docx` is optional and not installed in the current environment, so runtime formatting itself was not executed.

---

## Task 7: Resolve Standards Profile Drift for Thesis Works

**Files:**
- Modify: `works/*/work.toml`
- Modify or add raw bundle: `meta/standards/raw/ru-vkr-university-default/manifest.json`
- Possibly modify: `meta/standards/registry.toml`

- [x] **Step 1: Decide profile strategy**

For each thesis work, choose one:

```toml
thesis_profile = "sogu-vkr-2025"
```

or keep:

```toml
thesis_profile = "ru-vkr-university-default"
```

Expected: every thesis work uses a profile whose raw/normalized authority status is intentional.

Actual: chose `sogu-vkr-2025` rather than creating a synthetic raw manifest for provisional `ru-vkr-university-default`.

- [x] **Step 2: If using `sogu-vkr-2025`, update work configs**

Modify:

```text
works/martial-law-coursework/work.toml
works/constitutional-amendments-implementation-coursework/work.toml
works/state-essence-role-coursework/work.toml
```

Expected: `standards.thesis_profile` points to the selected official profile.

Actual: updated all three thesis work configs and the constitutional amendments work canon note that referenced the old profile.

- [x] **Step 3: If keeping `ru-vkr-university-default`, add raw manifest**

Create:

```text
meta/standards/raw/ru-vkr-university-default/manifest.json
```

Expected: `standards-status ru-vkr-university-default` no longer reports `raw=missing`.

Actual: not applicable because the selected strategy is `sogu-vkr-2025`.

- [x] **Step 4: Verify work status**

Run:

```bash
python3 -m telegram_console.work_cli work-status --work martial-law-coursework --json
python3 -m telegram_console.work_cli work-status --work constitutional-amendments-implementation-coursework --json
python3 -m telegram_console.work_cli work-status --work state-essence-role-coursework --json
```

Expected: no `thesis-standards-raw-missing` blocker remains unless the profile is intentionally provisional.

Actual: all three work-status checks resolve thesis profile `sogu-vkr-2025` with `raw_status=available`. The remaining standards blocker is `thesis-standards-conflict`, preserving the official profile's conflict/applicability warning.

---

## Task 8: Add a Repeatable Workspace Hygiene Audit

**Files:**
- Create: `scripts/audit_workspace_hygiene.sh`
- Test: add a smoke assertion in `tests/test_regression_harness.py` if the shell script stays simple and deterministic

- [ ] **Step 1: Create script**

Create `scripts/audit_workspace_hygiene.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

git status --short
git ls-files output/docx
git check-ignore -v frontend/node_modules/.package-lock.json
git check-ignore -v frontend/.next/trace
git check-ignore -v output/runtime/web-control-plane.sqlite3
python3 -m telegram_console.work_cli standards-status
python3 -m telegram_console.work_cli skill-source-map audit --json
```

- [ ] **Step 2: Make executable**

Run:

```bash
chmod +x scripts/audit_workspace_hygiene.sh
```

- [ ] **Step 3: Run script**

Run:

```bash
scripts/audit_workspace_hygiene.sh
```

Expected: command completes and prints hygiene signals without modifying files.

---

## Task 9: Final Verification and Closeout

**Files:**
- Modify: `ROUDMAP.md`
- Possibly modify: `README.md`
- Possibly modify: `CHANGELOG.md`

- [ ] **Step 1: Run full verification matrix**

Run:

```bash
python3 -m unittest discover -s tests -q
ruff check telegram_console tests
ruff format --check telegram_console tests
python3 -m telegram_console.work_cli skill-source-map audit --json
```

Expected: all checks pass and skill audit reports `ok: true`.

- [ ] **Step 2: Confirm clean git state shape**

Run:

```bash
git status --short
```

Expected: only intended source/doc changes are present; no `frontend/`, `output/runtime/`, `academic_engine/`, `.next/`, `node_modules/`, or one-shot JSON traces appear.

- [ ] **Step 3: Update this roadmap**

Mark completed tasks with `[x]`, add final verification output summary to `Progress Log`, and leave any intentionally deferred items unchecked with a reason.

- [ ] **Step 4: Commit closeout**

```bash
git add ROUDMAP.md .gitignore output/README.md telegram_console tests scripts works meta README.md CHANGELOG.md
git commit -m "chore: clean workspace artifacts and refactor duplicate helpers"
```

---

## Recommended Execution Order

1. Task 1: `.gitignore` alignment.
2. Task 2: local generated junk cleanup.
3. Task 5: CLI traceback fix.
4. Task 4: one-shot report versioning and legacy cleanup.
5. Task 7: standards profile drift.
6. Task 3: output/docx policy.
7. Task 6: duplicate DOCX helper consolidation.
8. Task 8: hygiene audit script.
9. Task 9: final verification and closeout.

## Deferred Decisions

- Whether versioned PDF/PNG snapshots under `output/docx/` are legitimate evidence snapshots or should be removed from git.
- Whether thesis works should use `sogu-vkr-2025` or keep a repo-defined `ru-vkr-university-default` profile with a real raw bundle.
- Whether `frontend/` is a future app root or only accidental local build output. Current evidence shows no tracked source files under `frontend/`.
