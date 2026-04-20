# System + Project Master Audit (2026-04-20)

> Historical note (2026-04-21): references to `works/biometrics-vkr/` and its
> status surfaces in this document are frozen audit evidence. The bundle was
> later removed from the active workspace during migration to `starter-work`.

Объект аудита: `legal-academic-workspace` в текущем состоянии рабочего дерева на
`2026-04-20`.

Supporting evidence sources:

- [engineering-audit-autonomous-workspace-2026-04-19.md](engineering-audit-autonomous-workspace-2026-04-19.md)
- [runtime-reliability-audit-2026-04-20.md](runtime-reliability-audit-2026-04-20.md)
- [runtime-reliability-backlog-2026-04-20.md](runtime-reliability-backlog-2026-04-20.md)
- [candidate-polish-audit-2026-04-20.md](candidate-polish-audit-2026-04-20.md)
- [candidate-polish-backlog-2026-04-20.md](candidate-polish-backlog-2026-04-20.md)
- [final-quality-audit-2026-04-20.md](final-quality-audit-2026-04-20.md)
- local verification and smoke commands from this audit pass

## Executive Verdict

Итоговый verdict по engineering/repo слою: **operationally strong, but not yet
ready for an unconditional release-quality claim**.

Что подтверждено:

- deterministic offline verification matrix зелёный:
  - `python3 -m unittest discover -s tests -q` -> `385 tests OK`;
  - `ruff check telegram_console/ tests/` -> OK;
  - `ruff format --check telegram_console/ tests/` -> OK;
  - `python3 -m telegram_console.work_cli skill-source-map audit --json` -> `ok=true`.
- public CLI smoke для `work init`, `work-status --json`,
  `build-dissertation-artifacts`, `one-shot-thesis`, `one-shot-dissertation`
  проходит без runtime crash и удерживает ожидаемый contract.
- negative path `one-shot-dissertation --work biometrics-vkr --skip-docx`
  завершается ожидаемой human-readable ошибкой, а не traceback.

Что не позволяет считать систему и проект окончательно “final”:

- остаются подтверждённые docs-truth defects;
- audit выполняется по **грязному рабочему дереву**, а не по clean release
  snapshot;
- active work `biometrics-vkr` уже сейчас показывает standards blockers и
  missing advisory surfaces, поэтому claim о project readiness был бы завышен.

## Scope And Exclusions

Включено:

- `telegram_console/`, `scripts/`, `tests/`, `meta/`, `README.md`, `AGENTS.md`,
  `.github/workflows/ci.yml`, `workspace.toml`, `works/biometrics-vkr/work.toml`;
- runtime, daemon, launchd, CLI/public contracts, dissertation contour,
  work-state/work-type logic, standards registry, docs-truth, CI/test reality;
- read-only project-state snapshot активной работы `biometrics-vkr`.

Не включено:

- line-by-line doctrinal review manuscript prose;
- citation adequacy внутри глав;
- legal/research quality verdict по содержанию `works/biometrics-vkr/thesis/`;
- live network source acquisition;
- production Telegram/Codex runs с реальными секретами.

## Baseline Snapshot

### Working tree state

- `git status --short` показывает **65 changed entries**.
- Разрез по верхним зонам:
  - `telegram_console`: 24
  - `meta`: 12
  - `tests`: 12
  - `templates`: 7
  - `scripts`: 3
  - `agents`: 4
  - `README.md`, `AGENTS.md`, `works`: по 1
- `git diff --stat` показывает **33 files changed**, `2524 insertions`,
  `2184 deletions`.

Вывод: аудитируется не замороженный release candidate, а крупная in-flight wave.
Это не делает результаты бесполезными, но напрямую ограничивает силу
release-quality claim.

### Current verification reality

Подтверждённые локальные проверки этой волны:

- `python3 -m unittest discover -s tests -q` -> `385 tests OK`
- `ruff check telegram_console/ tests/` -> OK
- `ruff format --check telegram_console/ tests/` -> OK
- `python3 -m telegram_console.work_cli skill-source-map audit --json` ->
  `declared_skill_count=19`, `manifest_skill_count=19`, `issues=[]`

CI currently runs:

- `ruff check telegram_console tests`
- `python3 -m unittest discover -s tests -q`
- `python3 -m telegram_console.work_cli skill-source-map audit --json`
- `tests.test_regression_harness`
- fake verdict parser smoke

CI currently does **not** run `ruff format --check`.

### Active work snapshot

Active work from [workspace.toml](../workspace.toml): `biometrics-vkr`.

Work config from [works/biometrics-vkr/work.toml](../works/biometrics-vkr/work.toml):

- active lanes: `thesis`, `article`
- artifact type: `vkr`
- thesis profile: `sogu-vkr-2025`
- article profile: `ru-law-article-v1`

Read-only status snapshot via `python3 -m telegram_console.work_cli work-status --json`:

- no active run;
- next safe action: `launch-thesis review-section works/biometrics-vkr/thesis/manuscript/sections/00-title.md`
- visible blockers:
  - raw standards bundle missing for article profile `ru-law-article-v1`
  - conflict flag on thesis profile `sogu-vkr-2025`
- thesis/article quality advisories: `coverage=missing`
- thesis ledger advisory: `available=false`

## Evidence Matrix By Subsystem

| Subsystem | Evidence | Current result | Audit implication |
| --- | --- | --- | --- |
| Runtime / daemon / launchd | full unittest suite; [runtime-reliability-audit-2026-04-20.md](runtime-reliability-audit-2026-04-20.md) | no reproducible crash in audited public surfaces; reliability wave claims remain consistent | runtime layer is materially stronger than 2026-04-19 baseline |
| CLI / public contracts | temp-workspace smoke for `work init`, `work-status --json`, `build-dissertation-artifacts`, `one-shot-thesis`, `one-shot-dissertation`; negative path on `biometrics-vkr` | contracts hold; `one-shot-dissertation` now fails gracefully on non-dissertation work | public command surface is stable enough for operator use |
| Dissertation contour | candidate smoke plus [candidate-polish-audit-2026-04-20.md](candidate-polish-audit-2026-04-20.md) | candidate contour behaves as scaffold + blocker engine, not fake-success path | dissertation layer is framework-ready, not proven on a live dissertation work |
| Work-state / work-type | `work-status --json` on active work; one-shot smoke outputs | machine-readable payload shape and next-action logic are present and readable | status surfaces are useful, but active work is not “clean” |
| Standards / registry | `work.toml`, `workspace.toml`, `work-status --json`, registry files | active work profiles resolve; article raw bundle still missing; thesis profile keeps visible conflict flag | standards transparency is working, but current project has unresolved formal blockers |
| Docs truth | [README.md](../README.md), [AGENTS.md](../AGENTS.md), [tests/README.md](../tests/README.md), CI workflow, smoke outputs | repo docs are mostly synchronized, but not fully | confirmed docs defects remain |
| Test / CI architecture | full suite, split test modules, [.github/workflows/ci.yml](../.github/workflows/ci.yml) | deterministic suite is broad; CI coverage is decent but not exhaustive | CI is credible, yet leaves a formatting blind spot |

## Findings

### Proved defects

#### DOC-001 — `tests/README.md` still describes the pre-split test architecture

Severity: `medium`  
Confidence: `confirmed`

Evidence:

- [tests/README.md](../tests/README.md) still says the “main integration module” is
  `test_telegram_console.py` and frames test splitting as a future option.
- The current tree already contains dedicated modules such as
  `tests/test_work_cli_autonomous.py`, `tests/test_work_cli_launchd.py`,
  `tests/test_work_cli_runtime.py`, `tests/test_daemon_smoke.py`,
  `tests/test_work_state.py`.
- [runtime-reliability-backlog-2026-04-20.md](runtime-reliability-backlog-2026-04-20.md)
  explicitly marks the split of overloaded CLI/runtime tests out of
  `tests/test_telegram_console.py` as **closed**.

Why this matters:

- onboarding and audit consumers read a stale picture of the current regression
  surface;
- docs-truth is one of the declared sources of truth for repo operation.

#### DOC-002 — README documents the wrong one-shot report path for dissertation runs

Severity: `medium`  
Confidence: `confirmed`

Evidence:

- [README.md](../README.md) states that one-shot reports are written to
  `works/<slug>/thesis/reviews/<date>-one-shot-report.(md|json)`.
- Live smoke for
  `python3 -m telegram_console.work_cli one-shot-dissertation --work demo-candidate --skip-docx`
  writes:
  `works/demo-candidate/thesis/reviews/2026-04-20-one-shot-dissertation-report.md`.

Why this matters:

- dissertation operators following README will look for the wrong report stem;
- this is a deterministic docs-truth mismatch on a public CLI artifact path.

#### STATE-001 — Active work `biometrics-vkr` is not formally clean at the status layer

Severity: `high`  
Confidence: `confirmed`

Evidence from `python3 -m telegram_console.work_cli work-status --json`:

- article standards raw bundle is missing for `ru-law-article-v1`;
- thesis profile `sogu-vkr-2025` retains a visible conflict flag;
- thesis/article quality advisories are `missing`;
- thesis evidence ledger advisory is unavailable.

Why this matters:

- this does **not** prove manuscript weakness, but it does block any honest claim
  that the current project state is formally clean or submission-facing ready;
- the system is correctly surfacing those blockers instead of hiding them.

#### BASELINE-001 — The audit target is a dirty in-flight snapshot, not a clean release candidate

Severity: `high`  
Confidence: `confirmed`

Evidence:

- `git status --short` -> `65` changed entries;
- diff concentration across `telegram_console`, `tests`, `meta`, `templates`,
  `scripts`, and `works`.

Why this matters:

- a final release-quality claim should be pinned to a clean commit or tagged
  snapshot;
- otherwise the audit becomes a statement about a moving branch, not a stable
  system state.

### Probable risks / debt

#### CI-001 — CI still has a formatting blind spot

Severity: `medium`  
Confidence: `strong-suspect`

Evidence:

- [.github/workflows/ci.yml](../.github/workflows/ci.yml) runs `ruff check` but
  not `ruff format --check`.
- [.pre-commit-config.yaml](../.pre-commit-config.yaml) does include
  `ruff-format`, so formatting is enforced locally but not in CI.
- The supporting [final-quality-audit-2026-04-20.md](final-quality-audit-2026-04-20.md)
  recorded a recent formatting drift before it was cleaned.

Risk:

- style/format drift can return without failing CI.

#### CLI-001 — `work_cli.py` remains a large parser shell

Severity: `medium`  
Confidence: `strong-suspect`

Evidence:

- runtime surfaces were extracted, but
  [runtime-reliability-backlog-2026-04-20.md](runtime-reliability-backlog-2026-04-20.md)
  still keeps further `work_cli.py` decomposition as an open safe defer.

Risk:

- future command growth can reintroduce parser/dispatch coupling and make
  contract changes harder to review.

#### OPS-001 — launchd confidence is still limited to deterministic/local smoke

Severity: `medium`  
Confidence: `strong-suspect`

Evidence:

- runtime reliability backlog still lists real macOS LaunchAgent smoke on a
  launchd-capable runner as deferred.

Risk:

- local deterministic tests are strong, but they do not fully replace a real
  platform service-manager signal.

#### CAND-001 — candidate contour still lacks a live dissertation-work proving pass

Severity: `low`  
Confidence: `strong-suspect`

Evidence:

- [candidate-polish-audit-2026-04-20.md](candidate-polish-audit-2026-04-20.md)
  explicitly keeps a live-work dry-run as residual risk.

Risk:

- synthetic fixtures prove the framework, but not the operator ergonomics on a
  real dissertation bundle.

## Existing Audit Reconciliation

| Document | Role after this master audit | Current status |
| --- | --- | --- |
| [engineering-audit-autonomous-workspace-2026-04-19.md](engineering-audit-autonomous-workspace-2026-04-19.md) | broad architecture and original audit baseline | partially superseded for runtime/candidate specifics; still useful for macro architecture context |
| [runtime-reliability-audit-2026-04-20.md](runtime-reliability-audit-2026-04-20.md) | authoritative evidence for daemon/CLI/runtime wave | active supporting source; closed items accepted |
| [runtime-reliability-backlog-2026-04-20.md](runtime-reliability-backlog-2026-04-20.md) | authoritative open/closed runtime backlog | active supporting source; open safe-defer items carried forward |
| [candidate-polish-audit-2026-04-20.md](candidate-polish-audit-2026-04-20.md) | authoritative candidate-contour verdict | active supporting source; no longer sufficient as repo-wide verdict |
| [candidate-polish-backlog-2026-04-20.md](candidate-polish-backlog-2026-04-20.md) | candidate-specific follow-up matrix | active supporting source |
| [final-quality-audit-2026-04-20.md](final-quality-audit-2026-04-20.md) | technical close-out for the recent polish pass | active supporting source, but superseded as the canonical repo-wide verdict by this document |

## Project-State Section: `biometrics-vkr`

Operational state only; no doctrinal or citation-quality judgment is made here.

### Confirmed status

- work id: `biometrics-vkr`
- active lanes: `thesis`, `article`
- thesis profile: `sogu-vkr-2025`
- article profile: `ru-law-article-v1`
- active dissertation contour: `false`
- active run: `null`
- suggested next action:
  `launch-thesis review-section works/biometrics-vkr/thesis/manuscript/sections/00-title.md`

### Confirmed blockers / gaps

- article raw standards bundle missing;
- thesis standards profile has conflict flag;
- thesis/article quality advisories absent at status layer;
- thesis evidence ledger advisory absent.

### Interpretation

- The system is surfacing the right kind of operational friction instead of
  masking it.
- The project can continue working safely, but a formal readiness claim for the
  active work would be overstated until standards visibility and advisory gaps
  are addressed.

## Final Readiness Verdict

### Repo-layer verdict

The repository is **stable for deterministic offline engineering use** and is
backed by a broad, passing unittest suite plus reproducible CLI smoke.

It is **not yet entitled to an unconditional “fully final / release-quality”
claim** because:

- docs-truth still has confirmed defects;
- CI still lacks explicit formatting enforcement;
- the audit target is a dirty in-flight tree rather than a pinned release
  snapshot.

### Project-layer verdict

`biometrics-vkr` is **operationally trackable but not formally clean** at the
status/standards layer. That is a project-state blocker, not a proof of weak
manuscript content.

## Repro Commands

```bash
export PYTHONPATH=.
python3 -m unittest discover -s tests -q
ruff check telegram_console/ tests/
ruff format --check telegram_console/ tests/
python3 -m telegram_console.work_cli skill-source-map audit --json
python3 -m telegram_console.work_cli work-status --json
python3 -m telegram_console.work_cli one-shot-dissertation --work biometrics-vkr --skip-docx
```
